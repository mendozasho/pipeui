from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeui.helpers import load_settings, save_settings
from pipeui.validation.settings import AppSettings  # noqa: F401 – re-exported for import compat
from pipeui.workflow.functions import scan_functions

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsPatch(BaseModel):
    db_path: str | None = None
    accent: str | None = None
    density: str | None = None
    functions_paths: list[str] | None = None


@router.get("/browse")
def browse_directory(path: str = Query(default="")):
    """List subdirectories at the given path for the folder picker UI.

    Returns { path, parent, entries: [{name, path}] }.
    Defaults to the user's home directory when path is empty.
    Returns { error } for inaccessible or non-existent paths.
    """
    target = Path(path).expanduser() if path.strip() else Path.home()
    target = target.resolve()

    if not target.exists() or not target.is_dir():
        return JSONResponse({"error": f"Not a directory: {str(target)}"}, status_code=400)

    try:
        entries = sorted(
            [
                {"name": e.name, "path": str(e)}
                for e in target.iterdir()
                if e.is_dir() and not e.name.startswith(".")
            ],
            key=lambda x: x["name"].lower(),
        )
    except PermissionError:
        return JSONResponse({"error": f"Permission denied: {str(target)}"}, status_code=403)

    parent = str(target.parent) if target != target.parent else None
    return {"path": str(target), "parent": parent, "entries": entries}


@router.get("")
def get_settings():
    return load_settings()


@router.patch("")
def patch_settings(patch: SettingsPatch):
    current = load_settings()
    # Use model_fields_set to include only explicitly-provided fields (handles empty list for functions_paths)
    updates = {k: v for k, v in patch.model_dump().items() if k in patch.model_fields_set}

    # Validate functions_paths before saving: each entry must resolve to an existing directory
    if "functions_paths" in updates and updates["functions_paths"]:
        invalid = [
            p for p in updates["functions_paths"]
            if not (lambda rp: rp.exists() and rp.is_dir())(Path(p).resolve())
        ]
        if invalid:
            return JSONResponse({"ok": False, "invalid_paths": invalid}, status_code=422)

    restart_required = "db_path" in updates and updates["db_path"] != current.db_path
    paths_changed = "functions_paths" in updates and updates["functions_paths"] != current.functions_paths
    updated = current.model_copy(update=updates)
    save_settings(updated)

    response: dict = {"ok": True, "settings": updated.model_dump(), "restart_required": restart_required}

    if paths_changed:
        # Trigger immediate rescan when functions_paths changes (CONTEXT.md § function scanning)
        from pipeui.main import DB_PATH
        from pipeui.db import create_schema, get_connection
        conn = get_connection(str(DB_PATH))
        create_schema(conn)
        try:
            scan_log = scan_functions(conn, updated.functions_paths)
        finally:
            conn.close()
        response["scan_log"] = scan_log

    return JSONResponse(response)
