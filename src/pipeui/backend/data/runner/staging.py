"""Staging store (L1) — write/read/drop a source's transformed-output staging
tables. Single responsibility: how a source's transformed output is stored
(CONTEXT.md → Runner module responsibilities → ``staging.py`` (L1)).

These helpers (``staging_prefix``, ``write_staging_table``,
``drop_prior_staging_tables``, ``latest_staging``) were previously duplicated
across ``run.py`` and ``resolve.py``; both now import from here so there is one
definition. The *create flow's* column-type cache lives separately in
``create_flow_cache.py`` — a different responsibility that only shares the word
"staging".
"""
from __future__ import annotations

import uuid
from typing import Optional

import duckdb
import pandas as pd


def staging_prefix(source_id: uuid.UUID) -> str:
    return f"staging_{source_id.hex[:8]}_"


def drop_prior_staging_tables(
    conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID
) -> None:
    """Drop all prior staging tables for this source."""
    prefix = staging_prefix(source_id)
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchall()
    for (tname,) in rows:
        if tname.startswith(prefix):
            conn.execute(f'DROP TABLE IF EXISTS "{tname}"')


def write_staging_table(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    df: pd.DataFrame,
    timestamp: int,
) -> str:
    """Write df to a new staging table; return the table name."""
    tname = f"{staging_prefix(source_id)}{timestamp}"
    conn.execute(f'DROP TABLE IF EXISTS "{tname}"')
    conn.execute(f'CREATE TABLE "{tname}" AS SELECT * FROM df')
    return tname


def latest_staging(
    conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID
) -> Optional[tuple[str, int]]:
    """Return (table_name, timestamp) of the source's latest staging table, or None."""
    prefix = staging_prefix(source_id)
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchall()
    candidates: list[tuple[int, str]] = []
    for (tname,) in rows:
        if tname.startswith(prefix):
            suffix = tname[len(prefix):]
            try:
                candidates.append((int(suffix), tname))
            except ValueError:
                pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    ts, tname = candidates[-1]
    return tname, ts
