"""Binding suggestion (functions domain) — dry-run column suggestions.

suggest_bindings(conn, source_id, *, function_id|set_id, step_position=None)
    Returns per-parameter column suggestions + the source's available columns
    WITHOUT writing any rows. Drives the attach/edit modal: which columns to
    pre-select, what prior bindings to suggest, the current scalar value.

Split out of ``attach.py`` (#46): the suggest seam. Pure read — no writes, no
transaction.

Principle 7 (#191/#260): ``current_bindings`` is returned in saved
``alias_map.position`` order so a re-opened step restores its exact column order.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

import duckdb

from pipeui.backend.data.functions.classification import binding_kind


def _params_for_set(
    conn: duckdb.DuckDBPyConnection,
    set_id: uuid.UUID,
) -> list[tuple]:
    """Return (param_id, param_name, param_type, function_name, function_doc) rows for all functions in a set."""
    return conn.execute(
        """
        SELECT p.param_id, p.param_name, p.param_type, fr.function_name, fr.function_doc
        FROM function_set_map fsmap
        JOIN function_registry fr ON fr.function_id = fsmap.function_id
        JOIN parameter p ON p.function_id = fsmap.function_id
        WHERE fsmap.set_id = ?
        ORDER BY fsmap.position, p.param_name
        """,
        [set_id],
    ).fetchall()


def _params_for_function(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID,
) -> list[tuple]:
    """Return (param_id, param_name, param_type, function_name, function_doc) rows for a single function."""
    return conn.execute(
        """
        SELECT p.param_id, p.param_name, p.param_type, fr.function_name, fr.function_doc
        FROM parameter p
        JOIN function_registry fr ON fr.function_id = p.function_id
        WHERE p.function_id = ?
        ORDER BY p.param_name
        """,
        [function_id],
    ).fetchall()


def suggest_bindings(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    *,
    function_id: Optional[uuid.UUID] = None,
    set_id: Optional[uuid.UUID] = None,
    step_position: Optional[int] = None,
) -> dict:
    """Return column binding suggestions for a function or set without writing rows.

    Exactly one of function_id / set_id must be provided.

    Response shape:
      {
        "params": [
          {
            "param_id": "...",
            "param_name": "...",
            "param_type": "...",
            "param_kind": "column" | "scalar",
            "binding_kind": "value_or_column" | "column_only",  # classification.py
            "function_name": "...",
            "function_doc": "...",          # owning function's description ("" if none)
            "suggested_columns": [...],     # empty for scalar params
            "current_bindings": [{ "column_id": "...", "column_name": "..." }],
            "current_scalar_value": "..." | null
          }
        ],
        "available_columns": [
          { "column_id": "...", "column_name": "...", "column_type": "..." }
        ]
      }

    Column-only params (pd.Series) carry param_kind="column" and
    binding_kind="column_only". value_or_column params (int, float, bool, str) carry
    param_kind="scalar" and binding_kind="value_or_column" — they open as a free-text
    value but may bind a column (suggested_columns may be populated from prior bindings).
    pd.DataFrame (binding_kind="table") is excluded (auto-filled with the full table).

    current_bindings is the param's existing alias_map rows on THIS source — it
    drives edit-modal pre-selection of column checkboxes. current_scalar_value is
    the persisted source_scalar_map value for (source, param) — set for scalar
    params and for a str param used in plain-string mode — or None. Both let a
    re-opened step restore its saved state (#191).

    available_columns is computed from the source's column_registry rows plus
    columns added by join steps already in the pipeline at positions before
    step_position (when provided).

    suggested_columns contains all target-source columns whose column_id appears
    in any prior alias_map binding for the same parameter_id on *other* sources.
    All matches are returned (no tiebreaker — alias_map has no reliable ordering).
    """
    if (function_id is None) == (set_id is None):
        raise ValueError("Exactly one of function_id or set_id must be provided")

    # 1. Collect all params
    if set_id is not None:
        raw_params = _params_for_set(conn, set_id)
    else:
        raw_params = _params_for_function(conn, function_id)

    # 2. Collect column_ids on the target source
    target_col_rows = conn.execute(
        """
        SELECT cr.column_id, cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()
    target_col_map: dict[str, tuple[str, str]] = {str(r[0]): (r[1], r[2]) for r in target_col_rows}

    # 3. Build available_columns: source columns + columns from prior join steps
    available_columns = [
        {"column_id": str(r[0]), "column_name": r[1], "column_type": r[2]}
        for r in target_col_rows
    ]

    # Add columns contributed by join steps at positions before step_position
    if step_position is not None:
        join_step_rows = conn.execute(
            """
            SELECT sbm.builtin_config
            FROM source_builtin_map sbm
            WHERE sbm.source_id = ? AND sbm.builtin_type = 'join' AND sbm.position < ?
            ORDER BY sbm.position
            """,
            [source_id, step_position],
        ).fetchall()
        seen_col_ids = {c["column_id"] for c in available_columns}
        for (config,) in join_step_rows:
            cfg = json.loads(config) if isinstance(config, str) else config
            right_source_id = cfg.get("right_source_id")
            keep = cfg.get("keep_columns", "all")
            if right_source_id and keep != "none":
                right_cols = conn.execute(
                    """
                    SELECT cr.column_id, cr.column_name, cr.column_type
                    FROM column_registry cr
                    JOIN source_column_map scm ON scm.column_id = cr.column_id
                    WHERE scm.source_id = ?
                    ORDER BY cr.column_name
                    """,
                    [right_source_id],
                ).fetchall()
                for rc in right_cols:
                    rc_id = str(rc[0])
                    if rc_id not in seen_col_ids:
                        available_columns.append(
                            {"column_id": rc_id, "column_name": rc[1], "column_type": rc[2]}
                        )
                        seen_col_ids.add(rc_id)

    # 4. Build result params — unified loop over column + scalar params (pd.DataFrame
    #    excluded). Each param carries: suggested_columns (prior bindings on OTHER
    #    sources, column kinds only), current_bindings (this param's alias_map rows on
    #    THIS source — drives edit pre-selection, #191), and current_scalar_value (the
    #    persisted source_scalar_map value for scalars AND str in plain-string mode, #191).
    result_params = []
    for p_id, p_name, p_type, fn_name, fn_doc in raw_params:
        # Eligibility is derived from the single binding_kind rule (classification.py),
        # never a parallel type literal here (DRY/OCP). `table` (pd.DataFrame) is auto-
        # filled with the full table and never user-bound, so it is excluded. Both
        # `value_or_column` (int/float/bool/str — a literal OR a bound column) and
        # `column_only` (pd.Series — always columns) are surfaced for binding.
        b_kind = binding_kind(p_type)
        if b_kind == "table":
            continue  # pd.DataFrame — auto-filled with the full table, never user-bound

        # param_kind drives the modal's free-text-vs-column-list affordance:
        # column_only params are pure column pickers; value_or_column params open as a
        # scalar text input that can toggle to a column binding.
        kind = "column" if b_kind == "column_only" else "scalar"

        # Suggested columns: prior bindings for this param on OTHER sources that
        # also exist on the target source. Surfaced for any bindable param (a
        # value_or_column numeric/str can carry cross-source column suggestions too).
        suggested = []
        if b_kind in ("column_only", "value_or_column"):
            prior_rows = conn.execute(
                """
                SELECT DISTINCT am.column_id
                FROM alias_map am
                WHERE am.parameter_id = ?
                  AND am.source_id <> ?
                """,
                [p_id, source_id],
            ).fetchall()
            for (col_id,) in prior_rows:
                col_id_str = str(col_id)
                if col_id_str in target_col_map:
                    col_name, _ = target_col_map[col_id_str]
                    suggested.append({"column_id": col_id_str, "column_name": col_name})
            suggested.sort(key=lambda c: c["column_name"])

        # Current bindings: this param's alias_map rows on THIS source. Restores
        # column selections when an attached step is re-opened for edit (#191).
        # Principle 7 (#260): ORDER BY am.position so the saved column ORDER
        # round-trips — never re-sorted alphabetically.
        current_binding_rows = conn.execute(
            """
            SELECT cr.column_id, cr.column_name
            FROM alias_map am
            JOIN column_registry cr ON cr.column_id = am.column_id
            WHERE am.parameter_id = ? AND am.source_id = ?
            ORDER BY am.position
            """,
            [p_id, source_id],
        ).fetchall()
        current_bindings = [
            {"column_id": str(r[0]), "column_name": r[1]}
            for r in current_binding_rows
        ]

        # Current persisted scalar value (or None) — for scalar params and for a
        # str param used in plain-string mode. Restores the typed value on edit (#191).
        scalar_row = conn.execute(
            "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
            [source_id, p_id],
        ).fetchone()
        current_scalar_value = scalar_row[0] if scalar_row is not None else None

        result_params.append({
            "param_id": str(p_id),
            "param_name": p_name,
            "param_type": p_type,
            "param_kind": kind,
            "binding_kind": b_kind,
            "function_name": fn_name,
            "function_doc": fn_doc or "",
            "suggested_columns": suggested,
            "current_bindings": current_bindings,
            "current_scalar_value": current_scalar_value,
        })

    return {"params": result_params, "available_columns": available_columns}
