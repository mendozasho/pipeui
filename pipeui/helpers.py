from __future__ import annotations

import re
from pathlib import Path
from typing import Generator

import duckdb


def get_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    from pipeui.duckdb import create_schema, get_connection
    from pipeui.main import DB_PATH
    conn = get_connection(str(DB_PATH))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def infer_pattern(filename: str) -> str | None:
    """Return a generalized regex pattern for a filename, or None if no digits exist.

    Generally used to infer the filename of a new data source. For example, `sales-2025.04.03.xlsx`.
    """
    stem = Path(filename).stem
    if not re.search(r"\d", stem):
        return None
    return re.sub(r"\d+", r"\\d+", stem)
