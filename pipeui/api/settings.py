from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

CONFIG_PATH = Path("pipeui.config.json")

DEFAULTS: dict = {
    "db_path": "pipeui.db",
    "accent": "#7c6cf5",
    "density": "regular",
    "functions_paths": [],
}


class AppSettings(BaseModel):
    db_path: str = DEFAULTS["db_path"]
    accent: str = DEFAULTS["accent"]
    density: str = DEFAULTS["density"]
    functions_paths: list[str] = []


class SettingsPatch(BaseModel):
    db_path: str | None = None
    accent: str | None = None
    density: str | None = None
    functions_paths: list[str] | None = None


def load_settings() -> AppSettings:
    if not CONFIG_PATH.exists():
        settings = AppSettings()
        CONFIG_PATH.write_text(settings.model_dump_json(indent=2))
        return settings
    data = json.loads(CONFIG_PATH.read_text())
    return AppSettings(**{**DEFAULTS, **data})


def save_settings(settings: AppSettings) -> None:
    CONFIG_PATH.write_text(settings.model_dump_json(indent=2))


@router.get("")
def get_settings():
    return load_settings()


@router.patch("")
def patch_settings(patch: SettingsPatch):
    current = load_settings()
    # Use model_fields_set to include only explicitly-provided fields (handles empty list for functions_paths)
    updates = {k: v for k, v in patch.model_dump().items() if k in patch.model_fields_set}
    restart_required = "db_path" in updates and updates["db_path"] != current.db_path
    paths_changed = "functions_paths" in updates and updates["functions_paths"] != current.functions_paths
    updated = current.model_copy(update=updates)
    save_settings(updated)

    response: dict = {"ok": True, "settings": updated.model_dump(), "restart_required": restart_required}

    if paths_changed:
        # Trigger immediate rescan when functions_paths changes (CONTEXT.md § function scanning)
        from pipeui.main import DB_PATH
        from pipeui.duckdb import create_schema, get_connection
        from pipeui.workflow.functions import scan_functions
        conn = get_connection(str(DB_PATH))
        create_schema(conn)
        try:
            scan_log = scan_functions(conn, updated.functions_paths)
        finally:
            conn.close()
        response["scan_log"] = scan_log

    return JSONResponse(response)
