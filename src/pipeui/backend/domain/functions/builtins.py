"""Built-in pipeline steps: join and pivot.

attach_builtin(conn, source_id, builtin_type, builtin_config) -> dict
    Creates a source_builtin_map row and returns {"ok": True, "step_id": "..."}.

detach_builtin(conn, source_id, step_id) -> bool
    Removes the row; returns False when not found.

patch_builtin(conn, source_id, step_id, *, builtin_config=None, position=None) -> bool
    Updates builtin_config and/or position; returns False when not found.

get_builtin_steps(conn, source_id) -> list[dict]
    Returns all source_builtin_map rows for a source ordered by position,
    each with step_type="builtin".

execute_builtin_step(conn, df, step) -> tuple[pd.DataFrame, str | None]
    Executes a single built-in step against the working DataFrame and returns
    (result_df, consumed_result_id).  consumed_result_id is the resolved
    transformed-output result_id when a join consumed a transformed source
    (lineage), else None.  Built-ins run as DuckDB SQL, NOT via the worker
    subprocess.

get_unified_pipeline(conn, source_id) -> dict | None
    Returns a unified list of function steps and built-in steps ordered by
    position, with a step_type discriminator.
"""
from __future__ import annotations

import json
import uuid

from dataclasses import dataclass
from typing import Callable, Optional

import duckdb
import pandas as pd

from pipeui.backend.data.base.ids import new_id
from pipeui.backend.domain.runner.resolve import RAW, TRANSFORMED, resolve_frame
from pipeui.backend.data.runner.steps import BuiltinStepContext
# get_builtin_steps lives in step_loader (L1, pure read); re-exported here so
# ``from pipeui.backend.domain.functions.builtins import get_builtin_steps`` keeps working.
from pipeui.backend.data.runner.step_loader import get_builtin_steps  # noqa: F401

# ---------------------------------------------------------------------------
# Catalog read
# ---------------------------------------------------------------------------

def list_builtin_catalog(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return the built-in step catalog from builtin_registry, ordered by type.

    Each entry: ``{builtin_id, builtin_type, display_name, description, config_schema}``
    with config_schema parsed from its stored JSON. The read+parse the API seam used to
    do inline (DIP fix — §14): GET /builtins now delegates here.
    """
    rows = conn.execute(
        "SELECT builtin_id, builtin_type, display_name, description, config_schema "
        "FROM builtin_registry ORDER BY builtin_type"
    ).fetchall()
    return [
        {
            "builtin_id": str(builtin_id),
            "builtin_type": builtin_type,
            "display_name": display_name,
            "description": description,
            "config_schema": json.loads(config_schema) if isinstance(config_schema, str) else config_schema,
        }
        for builtin_id, builtin_type, display_name, description, config_schema in rows
    ]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_JOIN_TYPES = {"inner", "left", "right", "full"}
_VALID_AGGREGATIONS = {"sum", "avg", "min", "max", "count"}
# Filter operators (CONTEXT.md "built-in step" → Filter config shape). The first
# group are binary comparisons; is_null/is_not_null take no value.
_FILTER_COMPARISONS = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_VALID_FILTER_OPERATORS = set(_FILTER_COMPARISONS) | {
    "contains", "not_contains", "is_null", "is_not_null"
}
_NULLARY_FILTER_OPERATORS = {"is_null", "is_not_null"}


def _validate_join_config(cfg: dict) -> str | None:
    """Return an error string or None if config is valid."""
    if not cfg.get("right_source_id"):
        return "join config must include right_source_id"
    join_type = cfg.get("join_type", "inner")
    if join_type not in _VALID_JOIN_TYPES:
        return f"join_type must be one of {sorted(_VALID_JOIN_TYPES)!r}; got {join_type!r}"
    on = cfg.get("on")
    if not on or not isinstance(on, list):
        return "join config must include a non-empty 'on' list"
    for clause in on:
        if not clause.get("left_col") or not clause.get("right_col"):
            return "each 'on' entry must have left_col and right_col"
    return None


def _validate_pivot_config(cfg: dict) -> str | None:
    if not cfg.get("pivot_column"):
        return "pivot config must include pivot_column"
    value_columns = cfg.get("value_columns")
    if not value_columns or not isinstance(value_columns, list):
        return "pivot config must include a non-empty value_columns list"
    for vc in value_columns:
        if not vc.get("col_id") and not vc.get("col_name"):
            return "each value_column entry must have col_id or col_name"
        aggs = vc.get("aggregations", [])
        bad = [a for a in aggs if a not in _VALID_AGGREGATIONS]
        if bad:
            return f"unknown aggregations {bad!r}; valid: {sorted(_VALID_AGGREGATIONS)!r}"
    return None


def _validate_filter_config(cfg: dict) -> str | None:
    if not cfg.get("column"):
        return "filter config must include column"
    operator = cfg.get("operator")
    if operator not in _VALID_FILTER_OPERATORS:
        return (
            f"operator must be one of {sorted(_VALID_FILTER_OPERATORS)!r}; got {operator!r}"
        )
    # Binary operators need a value; is_null / is_not_null do not.
    if operator not in _NULLARY_FILTER_OPERATORS and cfg.get("value") in (None, ""):
        return f"operator {operator!r} requires a value"
    return None


def _validate_rename_config(cfg: dict) -> str | None:
    """Attach-time shape check for a rename built-in (#40).

    Config: ``{"renames": {"<old>": "<new>", ...}}`` — a non-empty mapping; every old
    and new name a non-empty string; new names unique (no two columns map to the same
    target). Run-time existence + target-collision are checked in ``_execute_rename``
    (step status=failed), the same split filter uses.
    """
    renames = cfg.get("renames")
    if not isinstance(renames, dict) or not renames:
        return "rename config must include a non-empty 'renames' mapping"
    for old, new in renames.items():
        if not isinstance(old, str) or not old.strip():
            return "every rename source column must be a non-empty name"
        if not isinstance(new, str) or not new.strip():
            return f"rename target for {old!r} must be a non-empty name"
    new_names = list(renames.values())
    if len(set(new_names)) != len(new_names):
        return "rename targets must be unique — no two columns may map to the same new name"
    return None


# ---------------------------------------------------------------------------
# attach / detach / patch
# ---------------------------------------------------------------------------

def attach_builtin(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    builtin_type: str,
    builtin_config: dict,
) -> dict:
    """Create a source_builtin_map row.

    Returns {"ok": True, "step_id": "<uuid>"} or {"ok": False, "detail": "..."}.
    """
    spec = BUILTIN_EXECUTORS.get(builtin_type)
    if spec is None:
        return {"ok": False, "detail": f"builtin_type must be one of {sorted(BUILTIN_EXECUTORS)}; got {builtin_type!r}"}

    # Validate config shape (attach-time) via the registered validator
    err = spec.validate(builtin_config)
    if err:
        return {"ok": False, "detail": err}

    # Source must exist
    if conn.execute("SELECT 1 FROM source_registry WHERE source_id = ?", [source_id]).fetchone() is None:
        return {"ok": False, "detail": f"source_id {source_id!r} not found"}

    # Singleton built-ins (e.g. rename, #40) allow at most one per source. The flag
    # lives on the BuiltinSpec, so a future singleton type is one registration — no
    # type-specific branch here (OCP).
    if spec.singleton and conn.execute(
        "SELECT 1 FROM source_builtin_map WHERE source_id = ? AND builtin_type = ?",
        [source_id, builtin_type],
    ).fetchone() is not None:
        return {"ok": False, "detail": f"only one {builtin_type!r} step is allowed per source"}

    # Position = MAX(position)+1 across both map tables for this source
    sfm_max = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM source_function_map WHERE source_id = ?",
        [source_id],
    ).fetchone()[0]
    sbm_max = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM source_builtin_map WHERE source_id = ?",
        [source_id],
    ).fetchone()[0]
    position = max(sfm_max, sbm_max) + 1

    step_id = new_id()
    conn.execute(
        "INSERT INTO source_builtin_map (step_id, source_id, builtin_type, builtin_config, position) VALUES (?, ?, ?, ?, ?)",
        [step_id, source_id, builtin_type, json.dumps(builtin_config), position],
    )
    return {"ok": True, "step_id": str(step_id)}


def detach_builtin(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    step_id: uuid.UUID,
) -> bool:
    """Remove a built-in step row.  Returns False when not found."""
    row = conn.execute(
        "SELECT step_id FROM source_builtin_map WHERE step_id = ? AND source_id = ?",
        [step_id, source_id],
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM source_builtin_map WHERE step_id = ?", [step_id])
    return True


def patch_builtin(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    step_id: uuid.UUID,
    *,
    builtin_config: dict | None = None,
    position: int | None = None,
) -> bool:
    """Update builtin_config and/or position.  Returns False when not found."""
    row = conn.execute(
        "SELECT step_id FROM source_builtin_map WHERE step_id = ? AND source_id = ?",
        [step_id, source_id],
    ).fetchone()
    if row is None:
        return False
    if builtin_config is not None:
        conn.execute(
            "UPDATE source_builtin_map SET builtin_config = ? WHERE step_id = ?",
            [json.dumps(builtin_config), step_id],
        )
    if position is not None:
        conn.execute(
            "UPDATE source_builtin_map SET position = ? WHERE step_id = ?",
            [position, step_id],
        )
    return True


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_unified_pipeline(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> dict | None:
    """Return pipeline with both function steps and built-in steps unified by position.

    Returns None if source_id is not in source_registry.
    Response shape:
      {
        "source": { source_id, source_name, columns: [...] },
        "steps": [
          {
            step_type: "function" | "builtin",
            position: int,
            ... step-specific fields
          }
        ]
      }
    """
    src_row = conn.execute(
        "SELECT source_id, source_name FROM source_registry WHERE source_id = ?",
        [source_id],
    ).fetchone()
    if src_row is None:
        return None

    col_rows = conn.execute(
        """
        SELECT cr.column_id, cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()
    columns = [
        {"column_id": str(r[0]), "column_name": r[1], "column_type": r[2]}
        for r in col_rows
    ]

    # Function steps
    fn_rows = conn.execute(
        """
        SELECT
            sfm.source_function_map_id,
            fs.set_id,
            fs.set_name,
            sfm.position,
            sfm.output_mode
        FROM source_function_map sfm
        JOIN function_set fs ON fs.set_id = sfm.set_id
        WHERE sfm.source_id = ?
        ORDER BY sfm.position ASC
        """,
        [source_id],
    ).fetchall()

    steps: list[dict] = []
    for sfm_id, set_id, set_name, position, output_mode in fn_rows:
        steps.append({
            "step_type": "function",
            "source_function_map_id": str(sfm_id),
            "set_id": str(set_id),
            "set_name": set_name,
            "position": position,
            "output_mode": output_mode,
        })

    # Built-in steps. get_builtin_steps now produces the typed BuiltinStepContext
    # carrier; this API-response builder serializes each back to the wire dict shape
    # the unified-pipeline endpoint returns (the carrier boundary ends at the runner).
    for bstep in get_builtin_steps(conn, source_id):
        steps.append({
            "step_id": bstep.step_id,
            "step_type": "builtin",
            "builtin_type": bstep.builtin_type,
            "builtin_config": bstep.builtin_config,
            "position": bstep.position,
        })

    # Sort unified list by position
    steps.sort(key=lambda s: (s["position"], s.get("set_name") or s.get("builtin_type") or ""))

    return {
        "source": {
            "source_id": str(src_row[0]),
            "source_name": src_row[1],
            "columns": columns,
        },
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_builtin_step(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    step: "BuiltinStepContext",
    *,
    run_transforms: Optional[Callable[[duckdb.DuckDBPyConnection, uuid.UUID], None]] = None,
) -> tuple[pd.DataFrame, str | None]:
    """Execute a single built-in step against the working DataFrame.

    Returns ``(result_df, consumed_result_id)``. ``consumed_result_id`` is the
    resolved transformed-output ``result_id`` when a join consumed a transformed
    source (lineage — PRD User Story 7), else ``None`` (pivot/filter never consume
    another source, and a raw join consumes the source's own data, not a result).

    ``run_transforms`` is the injected runner (DIP) threaded to ``resolve_frame`` so a
    transformed join can materialize a never-run right source without builtins
    importing the orchestrator. Required only on the materialize path of a
    transformed join; ``None`` for raw joins and pivot/filter.

    Uses DuckDB directly (no worker subprocess).
    Raises ValueError for bad config; other exceptions propagate.
    """
    btype = step.builtin_type
    cfg = step.builtin_config
    if isinstance(cfg, str):
        cfg = json.loads(cfg)

    spec = BUILTIN_EXECUTORS.get(btype)
    if spec is None:
        raise ValueError(f"Unknown builtin_type: {btype!r}")
    return spec.execute(conn, df, cfg, run_transforms)


def _execute_join(
    conn: duckdb.DuckDBPyConnection,
    left_df: pd.DataFrame,
    cfg: dict,
    *,
    run_transforms: Optional[Callable[[duckdb.DuckDBPyConnection, uuid.UUID], None]] = None,
) -> tuple[pd.DataFrame, str | None]:
    """Execute a join built-in step.

    Config shape:
      { "right_source_id": "...", "join_type": "inner|left|right|full",
        "on": [{"left_col": "...", "right_col": "..."}],
        "use_transformed": bool, "keep_columns": "all" }

    The right-hand source is resolved through ``resolve_frame`` per the
    ``use_transformed`` flag — raw -> the source's instance table, transformed ->
    the source's resolved transformed output (latest staging, materialized on demand
    if never run). This is the single seam that decides where the join's right input
    comes from, so the toggle is honored instead of always reading raw (PRD
    Implementation Decisions -> Join honors the toggle).

    Returns ``(result_df, consumed_result_id)``: when the join consumed a
    transformed source, ``consumed_result_id`` is that resolved transformed-output's
    ``result_id`` so the join records the result it consumed (lineage — PRD User
    Story 7); a raw join consumes the source's own data, not a produced result, so
    it is ``None``.
    """
    right_source_id = uuid.UUID(cfg["right_source_id"])
    join_type = cfg.get("join_type", "inner").upper()
    on_clauses = cfg["on"]
    mode = TRANSFORMED if cfg.get("use_transformed") else RAW

    # Resolve the right frame through the seam; register both sides as temp views so
    # the join shape is uniform regardless of where the right frame came from. The
    # injected ``run_transforms`` runner lets a transformed join materialize a
    # never-run right source (DIP — resolve never imports the orchestrator).
    right_df, ref = resolve_frame(conn, right_source_id, mode, run_transforms=run_transforms)
    consumed_result_id = ref.result_id if mode == TRANSFORMED else None

    _left_view = f"_builtin_join_left_{uuid.uuid4().hex[:8]}"
    _right_view = f"_builtin_join_right_{uuid.uuid4().hex[:8]}"
    conn.execute(f'CREATE OR REPLACE TEMP VIEW "{_left_view}" AS SELECT * FROM left_df')
    conn.execute(f'CREATE OR REPLACE TEMP VIEW "{_right_view}" AS SELECT * FROM right_df')

    on_sql = " AND ".join(
        f'"{_left_view}"."{c["left_col"]}" = "{_right_view}"."{c["right_col"]}"'
        for c in on_clauses
    )

    keep_columns = cfg.get("keep_columns", "all")
    if keep_columns == "all":
        select_clause = f'"{_left_view}".*, "{_right_view}".*'
    else:
        select_clause = "*"

    sql = (
        f'SELECT {select_clause} '
        f'FROM "{_left_view}" '
        f'{join_type} JOIN "{_right_view}" ON {on_sql}'
    )
    try:
        result = conn.execute(sql).df()
    finally:
        conn.execute(f'DROP VIEW IF EXISTS "{_left_view}"')
        conn.execute(f'DROP VIEW IF EXISTS "{_right_view}"')

    return result, consumed_result_id


def _execute_pivot(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Execute a pivot built-in step using DuckDB PIVOT syntax.

    Config shape:
      { "index_columns": [...], "pivot_column": "...",
        "value_columns": [{"col_id": "...", "col_name": "...", "aggregations": ["sum", "avg"]}] }
    """
    pivot_col = cfg["pivot_column"]
    index_cols = cfg.get("index_columns", [])
    value_columns = cfg["value_columns"]

    _pivot_view = f"_builtin_pivot_{uuid.uuid4().hex[:8]}"
    conn.execute(f'CREATE OR REPLACE TEMP VIEW "{_pivot_view}" AS SELECT * FROM df')

    # Build PIVOT query
    # DuckDB PIVOT: PIVOT tbl ON pivot_col USING agg(val_col) GROUP BY index_cols
    # We build one pivot per (col_name, aggregation) combination.
    # For multiple value_columns/aggregations we use a single PIVOT with multiple USING clauses.
    using_parts = []
    for vc in value_columns:
        col_name = vc.get("col_name") or vc.get("col_id", "value")
        aggs = vc.get("aggregations", ["sum"])
        for agg in aggs:
            using_parts.append(f'{agg}("{col_name}")')

    using_clause = ", ".join(using_parts)
    group_clause = (
        f' GROUP BY {", ".join(chr(34) + c + chr(34) for c in index_cols)}'
        if index_cols
        else ""
    )

    sql = f'PIVOT "{_pivot_view}" ON "{pivot_col}" USING {using_clause}{group_clause}'
    try:
        result = conn.execute(sql).df()
    finally:
        conn.execute(f'DROP VIEW IF EXISTS "{_pivot_view}"')

    return result


def _execute_filter(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Execute a filter built-in step — keep rows where the column matches the predicate.

    Config shape (CONTEXT.md):
      { "column": "...", "operator": "eq|neq|gt|gte|lt|lte|contains|not_contains|is_null|is_not_null",
        "value": "<string>" }

    The value is stored as a string and bound as a parameter; DuckDB casts it to the
    column's type for comparison. contains/not_contains compare against the column cast
    to VARCHAR. is_null / is_not_null take no value.
    """
    err = _validate_filter_config(cfg)
    if err:
        raise ValueError(err)

    column = cfg["column"]
    operator = cfg["operator"]
    value = cfg.get("value")
    col_sql = f'"{column}"'

    _filter_view = f"_builtin_filter_{uuid.uuid4().hex[:8]}"
    conn.execute(f'CREATE OR REPLACE TEMP VIEW "{_filter_view}" AS SELECT * FROM df')

    if operator in _FILTER_COMPARISONS:
        where, params = f"{col_sql} {_FILTER_COMPARISONS[operator]} ?", [value]
    elif operator == "contains":
        where, params = f"CAST({col_sql} AS VARCHAR) LIKE ?", [f"%{value}%"]
    elif operator == "not_contains":
        where, params = f"CAST({col_sql} AS VARCHAR) NOT LIKE ?", [f"%{value}%"]
    elif operator == "is_null":
        where, params = f"{col_sql} IS NULL", []
    else:  # is_not_null (validated above)
        where, params = f"{col_sql} IS NOT NULL", []

    try:
        result = conn.execute(
            f'SELECT * FROM "{_filter_view}" WHERE {where}', params
        ).df()
    finally:
        conn.execute(f'DROP VIEW IF EXISTS "{_filter_view}"')

    return result


def _execute_rename(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Execute a rename built-in (#40) — rename the selected columns in the working df.

    Config: ``{"renames": {"<old>": "<new>", ...}}``. Operates on the FULL working df
    (including columns added by upstream joins) and is output-only — it does NOT touch
    the source's real schema (``column_registry`` / instance table).

    Run-time checks (raise ``ValueError`` → step status=failed, the same split filter uses):
      - every source column named in ``renames`` must exist in the working df;
      - no target name may collide with a *surviving* column (an existing column not
        itself being renamed away).

    ``conn`` is unused (rename is pure pandas) but kept for the uniform executor signature.
    """
    renames = cfg["renames"]
    cols = list(df.columns)

    missing = [old for old in renames if old not in cols]
    if missing:
        raise ValueError(f"rename: column(s) not found in the working data: {missing}")

    surviving = set(cols) - set(renames.keys())
    collisions = sorted({new for new in renames.values() if new in surviving})
    if collisions:
        raise ValueError(f"rename: target name(s) already exist in the working data: {collisions}")

    return df.rename(columns=renames)


# ---------------------------------------------------------------------------
# Built-in dispatch registry (OCP — #50)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuiltinSpec:
    """The validate + execute pair for one built-in type — mirrors the runner's
    ``STEP_EXECUTORS`` registry so a new built-in (e.g. rename, #40) REGISTERS here
    instead of editing the attach-time validation and run-time execution if/elif chains.

    - ``validate(cfg) -> str | None`` — attach-time config-shape check; error string or None.
    - ``execute(conn, df, cfg, run_transforms) -> (df, consumed_result_id)`` — runs the
      step. ``run_transforms`` is the injected runner (DIP) used only by the transformed-join
      materialize path; pivot/filter accept-and-ignore it and return ``consumed_result_id=None``.
    - ``singleton`` — at most one step of this type per source (enforced in attach_builtin);
      rename is singleton + pinned-last (#40).
    """

    validate: Callable[[dict], "str | None"]
    execute: Callable[..., "tuple[pd.DataFrame, str | None]"]
    singleton: bool = False


# The dispatch table both attach_builtin (validation) and execute_builtin_step
# (execution) look up by builtin_type. Adapter lambdas normalize the executors'
# differing shapes (join takes run_transforms + returns a consumed_result_id; pivot/
# filter return a bare df) to the uniform BuiltinSpec.execute signature.
BUILTIN_EXECUTORS: dict[str, BuiltinSpec] = {
    "join": BuiltinSpec(
        validate=_validate_join_config,
        execute=lambda conn, df, cfg, run_transforms: _execute_join(
            conn, df, cfg, run_transforms=run_transforms
        ),
    ),
    "pivot": BuiltinSpec(
        validate=_validate_pivot_config,
        execute=lambda conn, df, cfg, run_transforms: (_execute_pivot(conn, df, cfg), None),
    ),
    "filter": BuiltinSpec(
        validate=_validate_filter_config,
        execute=lambda conn, df, cfg, run_transforms: (_execute_filter(conn, df, cfg), None),
    ),
    "rename": BuiltinSpec(
        validate=_validate_rename_config,
        execute=lambda conn, df, cfg, run_transforms: (_execute_rename(conn, df, cfg), None),
        singleton=True,
    ),
}
