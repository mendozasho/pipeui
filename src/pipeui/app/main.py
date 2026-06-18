from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pipeui.middleware.builtins import router as builtins_router, catalog_router as builtins_catalog_router
from pipeui.middleware.function_sets import router as function_sets_router
from pipeui.middleware.functions import router as functions_router
from pipeui.middleware.pipelines import router as pipelines_router
from pipeui.middleware.settings import router as settings_router
from pipeui.middleware.sources import router as sources_router
from pipeui.middleware.validations import router as validations_router

# frontend/ is bundled as package data at the pipeui package root; this module now
# lives one level down in app/, so walk up to the package dir to find it.
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

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


def run():
    import uvicorn  # lazy: heavy server dependency, only needed when the server actually starts
    uvicorn.run("pipeui.app.main:app", host="127.0.0.1", port=8000, reload=True)
