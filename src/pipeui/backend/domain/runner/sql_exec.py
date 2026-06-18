"""SQL-function execution (L3 — runner execution mechanics).

Runs a ``.sql`` function against the source's instance table by substituting
``{source_table}`` and executing on the DuckDB connection. Returns a DataFrame on
success or a ``FailedFunctionEntry`` on error — the executors decide how to surface
that (a transform aborts the step; a validation interprets it).

Split out of ``executors.py`` (#45): one responsibility, imported **down** by the
executors registry. Unlike Python functions, a SQL function is *not* process-isolated
— it is the backend's own query against its own connection (no worker boundary).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import duckdb
import pandas as pd  # noqa: F401  (referenced in the return annotation)

from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.data.base.tables import instance_table_name


def _execute_sql_function(
    conn: duckdb.DuckDBPyConnection,
    module_path: str,
    source_id: uuid.UUID,
) -> "pd.DataFrame | FailedFunctionEntry":
    """Execute a SQL function by substituting {source_table} and running on DuckDB.

    Returns a DataFrame on success or a FailedFunctionEntry on error.
    """
    try:
        sql_source = Path(module_path).read_text(encoding="utf-8")
    except OSError as exc:
        entry = FailedFunctionEntry()
        entry.add("sql_read", f"cannot read SQL file: {exc}")
        return entry

    # Strip leading comment header lines to get the actual SQL body
    body_lines = [ln for ln in sql_source.splitlines() if not ln.strip().startswith("--")]
    sql_body = "\n".join(body_lines).strip()

    if not sql_body:
        entry = FailedFunctionEntry()
        entry.add("sql_empty", "SQL file contains no query after header comments")
        return entry

    tname = instance_table_name(source_id)
    sql = sql_body.replace("{source_table}", f'"{tname}"')

    try:
        return conn.execute(sql).df()
    except Exception as exc:
        entry = FailedFunctionEntry()
        entry.add("sql_exec", str(exc))
        return entry
