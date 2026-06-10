from __future__ import annotations

import json
import re
from pathlib import Path

from pipeui.validation.settings import AppSettings, DEFAULTS

CONFIG_PATH = Path("pipeui.config.json")


def load_settings() -> AppSettings:
    if not CONFIG_PATH.exists():
        settings = AppSettings()
        CONFIG_PATH.write_text(settings.model_dump_json(indent=2))
        return settings
    data = json.loads(CONFIG_PATH.read_text())
    return AppSettings(**{**DEFAULTS, **data})


def save_settings(settings: AppSettings) -> None:
    CONFIG_PATH.write_text(settings.model_dump_json(indent=2))


def infer_pattern(filename: str) -> str | None:
    """Return a generalized regex pattern for a filename, or None if no digits exist.

    Generally used to infer the filename of a new data source. For example, `sales-2025.04.03.xlsx`.
    """
    stem = Path(filename).stem
    if not re.search(r"\d", stem):
        return None
    return re.sub(r"\d+", r"\\d+", stem)
