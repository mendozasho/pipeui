"""API routes for built-in pipeline steps (join, pivot, filter).

GET    /builtins                                       — list all builtin_registry rows
POST   /sources/{source_id}/attach-builtin          — attach a built-in step
DELETE /sources/{source_id}/attach-builtin/{step_id} — remove a built-in step
PATCH  /sources/{source_id}/attach-builtin/{step_id} — update config or position
GET    /sources/{source_id}/pipeline                  — unified pipeline (functions + builtins)
"""
from __future__ import annotations

import json
import uuid

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeui.backend.data.base.db import get_conn
from pipeui.backend.domain.runner.builtins import (
    attach_builtin,
    detach_builtin,
    get_unified_pipeline,
    patch_builtin,
)

router = APIRouter(prefix="/sources", tags=["builtins"])
catalog_router = APIRouter(tags=["builtins"])


# ---------------------------------------------------------------------------
# Catalog route — GET /builtins
# ---------------------------------------------------------------------------

@catalog_router.get("/builtins")
def list_builtins(conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    """Return all rows from builtin_registry."""
    rows = conn.execute(
        "SELECT builtin_id, builtin_type, display_name, description, config_schema FROM builtin_registry ORDER BY builtin_type"
    ).fetchall()
    result = []
    for builtin_id, builtin_type, display_name, description, config_schema in rows:
        result.append({
            "builtin_id": str(builtin_id),
            "builtin_type": builtin_type,
            "display_name": display_name,
            "description": description,
            "config_schema": json.loads(config_schema) if isinstance(config_schema, str) else config_schema,
        })
    return result


def _parse_source_id(source_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")


def _parse_step_id(step_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(step_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid step_id: {step_id!r}")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AttachBuiltinBody(BaseModel):
    builtin_type: str
    builtin_config: dict


class PatchBuiltinBody(BaseModel):
    builtin_config: dict | None = None
    position: int | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/{source_id}/attach-builtin")
def attach_builtin_route(
    source_id: str,
    body: AttachBuiltinBody,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Attach a built-in step (join or pivot) to a source."""
    sid = _parse_source_id(source_id)
    result = attach_builtin(conn, sid, body.builtin_type, body.builtin_config)
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result


@router.delete("/{source_id}/attach-builtin/{step_id}", status_code=204)
def detach_builtin_route(
    source_id: str,
    step_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Remove a built-in step from a source."""
    sid = _parse_source_id(source_id)
    stid = _parse_step_id(step_id)
    if not detach_builtin(conn, sid, stid):
        raise HTTPException(status_code=404, detail=f"Built-in step {step_id!r} not found")


@router.patch("/{source_id}/attach-builtin/{step_id}")
def patch_builtin_route(
    source_id: str,
    step_id: str,
    body: PatchBuiltinBody,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Update builtin_config and/or position for a built-in step."""
    sid = _parse_source_id(source_id)
    stid = _parse_step_id(step_id)
    if not patch_builtin(conn, sid, stid, builtin_config=body.builtin_config, position=body.position):
        raise HTTPException(status_code=404, detail=f"Built-in step {step_id!r} not found")
    return {"ok": True}


@router.get("/{source_id}/pipeline")
def get_pipeline_route(
    source_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Return the unified pipeline (function steps + built-in steps) ordered by position."""
    sid = _parse_source_id(source_id)
    result = get_unified_pipeline(conn, sid)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")
    return result
