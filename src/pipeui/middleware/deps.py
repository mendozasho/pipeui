"""Shared FastAPI dependencies for the middleware (API) layer.

``get_conn`` — the request-scoped DuckDB connection provider every route depends on.
It lives here (the API seam), NOT in the data layer: it wires the app-level
``DB_PATH`` (``app/config``) to the data-layer connection + schema bootstrap — a
composition concern the bottom data module must not own, because owning it forces a
``backend → app`` up-import (the violation #49 fixes). §14 / ARCHITECTURE §2.
"""
from __future__ import annotations

from pipeui.app.config import DB_PATH
from pipeui.backend.data.base.db import create_schema, get_connection


def get_conn():
    """FastAPI Depends provider — yields a connected, schema-initialised DuckDB connection."""
    conn = get_connection(str(DB_PATH))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
