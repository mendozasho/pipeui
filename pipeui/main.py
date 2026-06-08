from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pipeui.api.sources import router as sources_router
from pipeui.duckdb import get_connection, create_schema

# frontend/ is a sibling of the pipeui package, two levels up from this file
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
DB_PATH = Path(os.environ.get("PIPEUI_DB", "pipeui.db"))

app = FastAPI(title="PipeUI")
app.include_router(sources_router)

# Serve the React frontend from the root after routes are registered
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def get_db():
    conn = get_connection(str(DB_PATH))
    create_schema(conn)
    return conn
