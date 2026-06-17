"""Staging store (L1) — write/read/drop a source's transformed-output staging
tables, plus the create-flow metadata cache (``CreateFlowCache``).

The staging-store helpers (``_staging_prefix``, ``_write_staging_table``,
``_drop_prior_staging_tables``, ``_latest_staging``) are the single home for "how a
source's transformed output is stored" (CONTEXT.md → Runner module responsibilities
→ ``staging.py`` (L1)). They were previously duplicated across ``run.py`` and
``resolve.py``; both now import from here so there is one definition.
"""
from __future__ import annotations

import uuid
from typing import Optional

import duckdb
import pandas as pd


# ---------------------------------------------------------------------------
# Transformed-output staging store (L1)
# ---------------------------------------------------------------------------

def _staging_prefix(source_id: uuid.UUID) -> str:
    return f"staging_{source_id.hex[:8]}_"


def _drop_prior_staging_tables(
    conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID
) -> None:
    """Drop all prior staging tables for this source."""
    prefix = _staging_prefix(source_id)
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchall()
    for (tname,) in rows:
        if tname.startswith(prefix):
            conn.execute(f'DROP TABLE IF EXISTS "{tname}"')


def _write_staging_table(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    df: pd.DataFrame,
    timestamp: int,
) -> str:
    """Write df to a new staging table; return the table name."""
    tname = f"{_staging_prefix(source_id)}{timestamp}"
    conn.execute(f'DROP TABLE IF EXISTS "{tname}"')
    conn.execute(f'CREATE TABLE "{tname}" AS SELECT * FROM df')
    return tname


def _latest_staging(
    conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID
) -> Optional[tuple[str, int]]:
    """Return (table_name, timestamp) of the source's latest staging table, or None."""
    prefix = _staging_prefix(source_id)
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


class CreateFlowCache:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        """
        Initializes the class instance and sets up a temporary table for managing flow
        creation stages. The constructor establishes a connection to a DuckDB database
        and executes a SQL statement to create a temporary table.

        :param conn: A DuckDBPyConnection instance used to execute SQL commands and
                     interact with the DuckDB database.
        :type conn: duckdb.DuckDBPyConnection
        """
        self._conn = conn
        conn.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _stage_create_flow (
                column_name    VARCHAR NOT NULL,
                column_type    VARCHAR NOT NULL,
                is_primary_key BOOLEAN NOT NULL DEFAULT false
            )
        """)

    def stage_columns(self, columns: list[tuple[str, str]]) -> None:
        """
        Stages the provided list of columns in the database for later use in creating or
        modifying database schema structures. This method clears any existing staged data before
        inserting new records.

        :param columns: A list of tuples where each tuple contains the column name and its
            corresponding column type. The column name is a string representing the name of
            the database column, and the column type is a string representing the data type
            of the column.
        :type columns: list[tuple[str, str]]

        :return: None
        """
        self._conn.execute("DELETE FROM _stage_create_flow")
        self._conn.executemany(
            "INSERT INTO _stage_create_flow (column_name, column_type) VALUES (?, ?)",
            columns,
        )

    def set_primary_key(self, column_name: str) -> None:
        """
        Sets a column as the primary key in the table `_stage_create_flow` by updating
        the `is_primary_key` field for the specified column. All other columns will
        have their `is_primary_key` set to `false` to ensure only one primary key is
        active.

        :param column_name: The name of the column to set as the primary key.
        :type column_name: str
        :return: None
        """
        self._conn.execute(
            "UPDATE _stage_create_flow SET is_primary_key = false"
        )
        self._conn.execute(
            "UPDATE _stage_create_flow SET is_primary_key = true WHERE column_name = ?",
            [column_name],
        )

    def get_staged(self) -> list[dict]:
        """
        Retrieves staged data from the `_stage_create_flow` database table and returns it as
        a list of dictionaries. Each dictionary represents a database column with its name,
        type, and whether it is a primary key.

        :return: A list of dictionaries, where each dictionary contains:
                 - `column_name` (str): Name of the database column.
                 - `column_type` (str): Type of the database column.
                 - `is_primary_key` (bool): Indicates whether the column is a primary key.
        :rtype: list[dict]
        """
        rows = self._conn.execute(
            "SELECT column_name, column_type, is_primary_key FROM _stage_create_flow"
        ).fetchall()
        return [
            {"column_name": r[0], "column_type": r[1], "is_primary_key": r[2]}
            for r in rows
        ]

    def get_primary_key(self) -> str | None:
        """
        Retrieves the name of the primary key column from the database.

        This method queries the `_stage_create_flow` table to find the column
        marked as the primary key. If no primary key column is found, this
        method returns `None`.

        :return: The name of the primary key column if it exists, otherwise `None`.
        :rtype: str | None
        """
        row = self._conn.execute(
            "SELECT column_name FROM _stage_create_flow WHERE is_primary_key = true LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def clear(self) -> None:
        """
        Deletes all records from the internal staging flow table.

        This method is used to clear the staging area, ensuring that any existing
        data is removed.

        :return: This method does not return a value.
        :rtype: None
        """
        self._conn.execute("DELETE FROM _stage_create_flow")

    def drop(self) -> None:
        """
        Executes a SQL command to drop the table `_stage_create_flow` in the database if it exists.

        This method ensures that the table `_stage_create_flow` is removed safely without raising
        an error if it does not exist.

        :return: None
        """
        self._conn.execute("DROP TABLE IF EXISTS _stage_create_flow")
