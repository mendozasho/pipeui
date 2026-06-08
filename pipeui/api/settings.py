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
    updated = current.model_copy(update=updates)
    save_settings(updated)
    return JSONResponse({"ok": True, "settings": updated.model_dump(), "restart_required": restart_required})
