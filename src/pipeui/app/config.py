from __future__ import annotations

from pathlib import Path

from pipeui.app.helpers import load_settings

# Startup-frozen DB path. Evaluated ONCE at import time from the settings file
# (load_settings eagerly creates the file if absent). It is a module-level
# constant on purpose: api/settings.py treats a db_path change as
# restart_required precisely because this value is frozen at process start.
# Do NOT turn this into a function/property that re-reads config — that would
# silently break the restart-required contract.
DB_PATH = Path(load_settings().db_path)
