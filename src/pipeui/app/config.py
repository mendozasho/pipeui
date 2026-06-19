"""App configuration — settings-file I/O + the startup-frozen DB path.

Owns ``pipeui.config.json``: ``load_settings``/``save_settings`` (the I/O folded in
from the dissolved ``app/helpers.py``, #49) plus ``DB_PATH``, the process-frozen DB
path. This is the composition root's config module; the backend never imports it
(that would be a backend→app up-import — the violation #49 fixes).
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeui.backend.data.base.settings import AppSettings, DEFAULTS

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


# Startup-frozen DB path. Evaluated ONCE at import time from the settings file
# (load_settings eagerly creates the file if absent). It is a module-level
# constant on purpose: api/settings.py treats a db_path change as
# restart_required precisely because this value is frozen at process start.
# Do NOT turn this into a function/property that re-reads config — that would
# silently break the restart-required contract.
DB_PATH = Path(load_settings().db_path)
