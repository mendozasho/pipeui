from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pipeui.api.builtins import router as builtins_router, catalog_router as builtins_catalog_router
from pipeui.api.function_sets import router as function_sets_router
from pipeui.api.functions import router as functions_router
from pipeui.api.pipelines import router as pipelines_router
from pipeui.api.settings import router as settings_router
from pipeui.helpers import load_settings
from pipeui.api.sources import router as sources_router
from pipeui.api.validations import router as validations_router
from pipeui.db import get_connection, create_schema

# frontend/ is bundled as package data inside the pipeui package directory
FRONTEND_DIR = Path(__file__).parent / "frontend"

# Load (and eagerly create if absent) the config file at startup
_settings = load_settings()
DB_PATH = Path(_settings.db_path)

app = FastAPI(title="PipeUI")
app.include_router(settings_router)
app.include_router(sources_router)
app.include_router(builtins_catalog_router)
app.include_router(builtins_router)
app.include_router(functions_router)
app.include_router(function_sets_router)
app.include_router(pipelines_router)
app.include_router(validations_router)

# Serve the React frontend from the root after routes are registered
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def get_db():
    conn = get_connection(str(DB_PATH))
    create_schema(conn)
    return conn


def run():
    import uvicorn
    uvicorn.run("pipeui.main:app", host="127.0.0.1", port=8000, reload=True)
