from __future__ import annotations

import re
from pathlib import Path

import duckdb

from pipeui.schema.constants import DUCKDB_TO_PYTHON, PYTHON_TO_DUCKDB
from pipeui.schema.queries import DDL as _DDL


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


# Additive column migrations for databases created before a column was added.
# Each entry is (table, column, ddl_fragment). Safe to run on fresh DBs because
# DuckDB raises an error on duplicate column names — we catch and ignore it.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("function_registry", "is_active", "BOOLEAN DEFAULT TRUE"),
]


def _run_migrations(conn: duckdb.DuckDBPyConnection) -> None:
    for table, column, definition in _COLUMN_MIGRATIONS:
        try:
            conn.execute("BEGIN")
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")


def infer_column_types(
        conn: duckdb.DuckDBPyConnection,
        file_path: str
) -> list[tuple[str, str]]:
    """Infer column names and types from a CSV or xlsx file using DuckDB sniffing.

    Needs a duckdb connection to execute the DESCRIBE SELECT query, which is used to try and
    get the column types.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".xlsx":
        try:
            rows = conn.execute(
                f"DESCRIBE SELECT * FROM read_xlsx('{file_path}')"
            ).fetchall()
        except Exception as exc:
            # Fall back to pandas for xlsx when read_xlsx is unavailable.
            import pandas as pd  # noqa: PLC0415

            df = pd.read_excel(file_path, nrows=0)
            print(exc)
            return [
                (col, map_pandas_dtype(str(df[col].dtype)))
                for col in df.columns
            ]
    elif ext == ".csv":
        rows = conn.execute(
            "DESCRIBE SELECT * FROM read_csv_auto(?)", [file_path]
        ).fetchall()
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    result = []
    for row in rows:
        col_name = row[0]
        raw_type = row[1].upper() if row[1] else ""
        # Normalize parameterized types like VARCHAR(100) or DECIMAL(18,3) → base name
        base_type = re.split(r"[\s(]", raw_type)[0]
        col_type = base_type if base_type in DUCKDB_TO_PYTHON else "VARCHAR"
        result.append((col_name, col_type))
    return result


def map_pandas_dtype(dtype_str: str) -> str:
    """Map a pandas dtype string to a DUCKDB_TO_PYTHON key or 'varchar'."""
    return PYTHON_TO_DUCKDB.get(dtype_str, "VARCHAR")


def get_db_path(conn: duckdb.DuckDBPyConnection) -> str:
    """Return the DB file path for this connection, or ':memory:' for in-memory."""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for row in rows:
            if row[1] == "main" and row[2]:
                return row[2]
    except Exception as exc:
        print(f"Error retrieving database path: {exc}")
    return ":memory:"
