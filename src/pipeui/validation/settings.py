from __future__ import annotations

from pydantic import BaseModel

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
