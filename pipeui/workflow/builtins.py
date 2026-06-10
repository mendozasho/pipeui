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

execute_builtin_step(conn, df, step) -> pd.DataFrame
    Executes a single built-in step against the working DataFrame and returns
    the result.  Built-ins run as DuckDB SQL, NOT via the worker subprocess.

get_unified_pipeline(conn, source_id) -> dict | None
    Returns a unified list of function steps and built-in steps ordered by
    position, with a step_type discriminator.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import duckdb
import pandas as pd

from pipeui.ids import new_id

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_JOIN_TYPES = {"inner", "left", "right", "full"}
_VALID_AGGREGATIONS = {"sum", "avg", "min", "max", "count"}


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
    if builtin_type not in ("join", "pivot"):
        return {"ok": False, "detail": f"builtin_type must be 'join' or 'pivot'; got {builtin_type!r}"}

    # Validate config shape
    if builtin_type == "join":
        err = _validate_join_config(builtin_config)
    else:
        err = _validate_pivot_config(builtin_config)
    if err:
        return {"ok": False, "detail": err}

    # Source must exist
    if conn.execute("SELECT 1 FROM source_registry WHERE source_id = ?", [source_id]).fetchone() is None:
        return {"ok": False, "detail": f"source_id {source_id!r} not found"}

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

def get_builtin_steps(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> list[dict]:
    """Return all source_builtin_map rows for a source ordered by position."""
    rows = conn.execute(
        "SELECT step_id, builtin_type, builtin_config, position FROM source_builtin_map WHERE source_id = ? ORDER BY position ASC",
        [source_id],
    ).fetchall()
    result = []
    for step_id, btype, bcfg, pos in rows:
        result.append({
            "step_id": str(step_id),
            "step_type": "builtin",
            "builtin_type": btype,
            "builtin_config": json.loads(bcfg) if isinstance(bcfg, str) else bcfg,
            "position": pos,
        })
    return result


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

    # Built-in steps
    for bstep in get_builtin_steps(conn, source_id):
        steps.append(bstep)

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
    step: dict,
) -> pd.DataFrame:
    """Execute a single built-in step against the working DataFrame.

    Uses DuckDB directly (no worker subprocess).
    Raises ValueError for bad config; other exceptions propagate.
    """
    btype = step["builtin_type"]
    cfg = step["builtin_config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)

    if btype == "join":
        return _execute_join(conn, df, cfg)
    elif btype == "pivot":
        return _execute_pivot(conn, df, cfg)
    else:
        raise ValueError(f"Unknown builtin_type: {btype!r}")


def _execute_join(
    conn: duckdb.DuckDBPyConnection,
    left_df: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Execute a join built-in step.

    Config shape:
      { "right_source_id": "...", "join_type": "inner|left|right|full",
        "on": [{"left_col": "...", "right_col": "..."}],
        "keep_columns": "all" }
    """
    from pipeui.sql_user_table import instance_table_name

    right_source_id = uuid.UUID(cfg["right_source_id"])
    join_type = cfg.get("join_type", "inner").upper()
    on_clauses = cfg["on"]

    right_tname = instance_table_name(right_source_id)

    # Register left df as a temporary view
    _left_view = f"_builtin_join_left_{uuid.uuid4().hex[:8]}"
    conn.execute(f'CREATE OR REPLACE TEMP VIEW "{_left_view}" AS SELECT * FROM left_df')

    on_sql = " AND ".join(
        f'"{_left_view}"."{c["left_col"]}" = "{right_tname}"."{c["right_col"]}"'
        for c in on_clauses
    )

    keep_columns = cfg.get("keep_columns", "all")
    if keep_columns == "all":
        select_clause = f'"{_left_view}".*, "{right_tname}".*'
    else:
        select_clause = "*"

    sql = (
        f'SELECT {select_clause} '
        f'FROM "{_left_view}" '
        f'{join_type} JOIN "{right_tname}" ON {on_sql}'
    )
    try:
        result = conn.execute(sql).df()
    finally:
        conn.execute(f'DROP VIEW IF EXISTS "{_left_view}"')

    return result


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
