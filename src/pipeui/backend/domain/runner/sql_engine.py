"""SQL engine — render + execute ``engine='sql'`` contracts (L3). #140.

A SQL contract's ``body`` is a template whose ``{placeholder}``s are its declared
params (``-- param:`` header lines). Rendering rules — the injection posture is
**reject, never quote-and-hope**:

  - **column param** (``pd.Series``) → the bound column as a quoted identifier,
    validated against the input frame's actual columns (a closed vocabulary — an
    unknown or hostile name is rejected outright).
  - **scalar param** (``int``/``float``/``bool``/``str``/``date``) → a ``?``
    placeholder; the value is ALWAYS a DuckDB bound parameter, never interpolated.
  - **table param** (``pd.DataFrame``) / **source_ref param** → the name of a
    temp view the executor registered (executor-generated, never user text).
  - ``{source_table}`` stays available to param-declared templates as the working
    frame's view (and is the whole template's only placeholder in the legacy
    implicit shape).

Legacy parity: a param-less (implicit) contract executes exactly as the retired
``sql_exec.execute_sql_function`` did — ``{source_table}`` substituted with the
source's instance table name, no views, no bound params.

Unlike Python functions, a SQL function is *not* process-isolated — it is the
backend's own query against its own connection (no worker boundary).
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

import duckdb
import pandas as pd

from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.data.base.tables import instance_table_name
from pipeui.backend.data.functions.binding import SCALAR_TYPES

if TYPE_CHECKING:  # pragma: no cover
    from pipeui.backend.data.functions.binding import BoundCall
    from pipeui.backend.data.functions.contract import FunctionContract

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# The implicit whole-table placeholder, usable by both legacy (param-less) and
# param-declared templates.
SOURCE_TABLE = "source_table"


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sql_body(contract: "FunctionContract") -> str | FailedFunctionEntry:
    """The template text: registry ``function_body``, else the source file.
    Header comment lines are stripped to the executable SQL."""
    source = contract.body
    if source is None:
        if not contract.source_path:
            entry = FailedFunctionEntry()
            entry.add("sql_read", "SQL contract has neither a body nor a source path")
            return entry
        try:
            source = Path(contract.source_path).read_text(encoding="utf-8")
        except OSError as exc:
            entry = FailedFunctionEntry()
            entry.add("sql_read", f"cannot read SQL file: {exc}")
            return entry
    body_lines = [ln for ln in source.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(body_lines).strip()
    if not body:
        entry = FailedFunctionEntry()
        entry.add("sql_empty", "SQL file contains no query after header comments")
        return entry
    return body


def render_sql(
    contract: "FunctionContract",
    call: "BoundCall",
    *,
    body: str,
    views: Mapping[str, str],
    columns_available: set[str],
) -> tuple[str, list[Any]]:
    """Render a SQL template into ``(sql, ordered ? params)``.

    Raises ``ValueError`` on any rejected placeholder: an unknown name, a column
    param whose bound column is not in ``columns_available`` (the closed
    vocabulary — never quoted-and-hoped), a view param with no registered view, or
    a scalar param with no resolved literal. Scalar values are appended to the
    param list in template (left→right) order, once per occurrence.
    """
    by_name = {p.name: p for p in contract.params}
    params_out: list[Any] = []

    def _substitute(m: re.Match) -> str:
        name = m.group(1)
        if name == SOURCE_TABLE and name not in by_name:
            if SOURCE_TABLE not in views:
                raise ValueError("{source_table} is not available for this call")
            return _quote_ident(views[SOURCE_TABLE])
        pc = by_name.get(name)
        if pc is None:
            raise ValueError(
                f"unknown placeholder '{{{name}}}' — not a declared `-- param:`"
            )
        if pc.type_str in ("pd.DataFrame", "source_ref"):
            view = views.get(name)
            if view is None:
                raise ValueError(f"no registered view for param '{name}'")
            return _quote_ident(view)
        # Column-bound (identifier) beats literal for value_or_column params.
        col = call.column_kwargs.get(name)
        if col is not None:
            if col not in columns_available:
                raise ValueError(
                    f"bound column '{col}' for param '{name}' is not a column of "
                    "the input — refresh the binding"
                )
            return _quote_ident(col)
        if pc.type_str == "pd.Series":
            raise ValueError(f"column param '{name}' has no bound column")
        if pc.type_str in SCALAR_TYPES:
            if name not in call.literal_kwargs:
                raise ValueError(f"scalar param '{name}' has no resolved value")
            params_out.append(call.literal_kwargs[name])
            return "?"
        raise ValueError(f"param '{name}' ({pc.type_str}) cannot appear in a SQL template")

    sql = _PLACEHOLDER_RE.sub(_substitute, body)
    return sql, params_out


def execute_sql_contract(
    conn: duckdb.DuckDBPyConnection,
    contract: "FunctionContract",
    call: "BoundCall",
    *,
    frame: pd.DataFrame,
    source_id: uuid.UUID,
) -> "pd.DataFrame | FailedFunctionEntry":
    """Execute one ``BoundCall`` of a SQL contract; DataFrame or FailedFunctionEntry.

    Param-less (implicit) contracts keep the legacy behavior byte-for-byte:
    ``{source_table}`` → the source's instance table, executed directly. Declared
    params render via ``render_sql``: the working ``frame`` is registered as the
    ``{source_table}`` view (and as each ``table`` param's view), ``source_ref``
    params resolve their referenced source's RAW frame, and scalars bind as ``?``.
    """
    body = _sql_body(contract)
    if isinstance(body, FailedFunctionEntry):
        return body

    if not contract.params:
        sql = body.replace("{source_table}", _quote_ident(instance_table_name(source_id)))
        try:
            return conn.execute(sql).df()
        except Exception as exc:
            entry = FailedFunctionEntry()
            entry.add("sql_exec", str(exc))
            return entry

    # Declared params: register views, render, execute with bound values.
    views: dict[str, str] = {}
    registered: list[str] = []

    def _register(param_name: str, view_frame: pd.DataFrame) -> None:
        view = f"__pipeui_sql_{param_name}_{uuid.uuid4().hex[:8]}"
        conn.register(view, view_frame)
        registered.append(view)
        views[param_name] = view

    try:
        # DuckDB cannot register a zero-column frame; a template that then uses
        # {source_table} gets the renderer's "not available" rejection instead.
        if len(frame.columns) > 0:
            _register(SOURCE_TABLE, frame)
        for p in contract.params:
            if p.type_str == "pd.DataFrame":
                _register(p.name, frame)
            elif p.type_str == "source_ref":
                ref = call.source_refs.get(p.name)
                if ref is None:
                    entry = FailedFunctionEntry()
                    entry.add(contract.name, f"source reference '{p.name}' is not set")
                    return entry
                # Local import: resolve sits beside the executors (L3/L4); the
                # engine only needs the RAW read here.
                from pipeui.backend.domain.runner.resolve import RAW, resolve_frame

                ref_frame, _ref = resolve_frame(conn, uuid.UUID(str(ref)), RAW)
                _register(p.name, ref_frame)

        try:
            sql, bound = render_sql(
                contract, call, body=body, views=views,
                columns_available=set(frame.columns),
            )
        except ValueError as exc:
            entry = FailedFunctionEntry()
            entry.add(contract.name, str(exc))
            return entry

        try:
            return conn.execute(sql, bound).df()
        except Exception as exc:
            entry = FailedFunctionEntry()
            entry.add("sql_exec", str(exc))
            return entry
    finally:
        for view in registered:
            try:
                conn.unregister(view)
            except Exception:
                pass
