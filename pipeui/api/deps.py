"""Shared FastAPI dependencies for API route modules."""
from __future__ import annotations

from typing import Generator

import duckdb

from pipeui.duckdb import create_schema, get_connection


def get_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    from pipeui.main import DB_PATH
    conn = get_connection(str(DB_PATH))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
