"""DuckDB connection + registry schema lifecycle (data/base).

Connection creation, schema DDL + registry migrations, and connection introspection
only. Type-inference moved to ``data/sources/inference.py`` and the FastAPI
``get_conn`` provider to ``middleware/deps.py`` (#49) — so this bottom data module
imports neither ``app`` (no more ``DB_PATH`` up-import) nor FastAPI, and emits no stdout.
"""
from __future__ import annotations

import duckdb

from pipeui.backend.data.base.schema.queries import DDL as _DDL, SEED_BUILTINS as _SEED_BUILTINS


############################
# DuckDB Related Functions
############################
# Leaving it here in case in the future, we need to get away from DuckDB
def get_connection(db_path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Establishes and returns a connection to a DuckDB database.

    This function creates a connection to a DuckDB database using the provided
    database file path. If no path is provided, it defaults to an in-memory
    database.

    :param db_path: The file path to the DuckDB database. Defaults to ":memory:"
                    which creates an in-memory database.
    :type db_path: str
    :return: A DuckDBPyConnection object representing the connection to the database.
    :rtype: duckdb.DuckDBPyConnection
    """
    return duckdb.connect(db_path)


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Creates the necessary schema in the provided DuckDB connection.

    This function executes a predefined SQL Data Definition Language (DDL) statement
    to create database schema elements such as tables or other objects within the
    given DuckDB connection.

    :param conn: The DuckDB connection object to execute the schema creation
        DDL statement on.
    :type conn: duckdb.DuckDBPyConnection

    :return: None
    """
    conn.execute(_DDL)
    _run_migrations(conn)
    conn.execute(_SEED_BUILTINS)


# Registry schema migrations — additive ALTER TABLE statements for DBs created
# before a column was added to a registry table. Distinct from workflow/migration.py
# which handles user data column-type changes (§7). Each entry: (table, column, ddl).
_REGISTRY_SCHEMA_MIGRATIONS: list[tuple[str, str, str]] = [
    ("function_registry", "is_active", "BOOLEAN DEFAULT TRUE"),
    # runner-execution: columns added to the DDL by slices 2 and 4b. Existing DBs
    # need them backfilled or get_pipeline/fetch_steps/attach 500 (see #254).
    # NOT NULL is omitted: DuckDB rejects ADD COLUMN with constraints. DEFAULT 0
    # backfills existing rows; inserts always supply position, so no NULL arises.
    ("alias_map", "position", "INTEGER DEFAULT 0"),
    ("source_function_map", "append_name", "VARCHAR"),
    # #258: param default capture — existing DBs need these or the executor can't
    # fall back to Python defaults and the frontend can't flag required params.
    ("parameter", "has_default", "BOOLEAN DEFAULT FALSE"),
    ("parameter", "default_value", "VARCHAR"),
    # #134 FunctionContract: signature-order param position + execution engine/body.
    # DEFAULT 0 / 'python' backfill existing rows; the scanner always supplies real
    # values on the next re-register.
    ("parameter", "position", "INTEGER DEFAULT 0"),
    ("function_registry", "engine", "VARCHAR DEFAULT 'python'"),
    ("function_registry", "function_body", "VARCHAR"),
]


def _run_migrations(conn: duckdb.DuckDBPyConnection) -> None:
    for table, column, definition in _REGISTRY_SCHEMA_MIGRATIONS:
        try:
            conn.execute("BEGIN")
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")


def get_db_path(conn: duckdb.DuckDBPyConnection) -> str:
    """Return the DB file path for this connection, or ':memory:' for in-memory.

    A failed PRAGMA falls back to ':memory:' silently — the data layer emits no stdout.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for row in rows:
            if row[1] == "main" and row[2]:
                return row[2]
    except Exception:
        pass
    return ":memory:"
