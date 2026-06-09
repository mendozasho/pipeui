"""API routes for pipeline read — Phase E1.

GET /pipelines/{source_id}  → get_pipeline workflow

§14: route modules call workflow/ only; never touch schema/, validation/,
or sql_user_table/ directly.
"""
from __future__ import annotations

import uuid

import duckdb
from fastapi import APIRouter, Depends, HTTPException

from pipeui.helpers import get_conn
from pipeui.workflow.attach import get_pipeline

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("/{source_id}")
def read_pipeline(
    source_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Return the committed pipeline state for a source.

    404 when source_id is unknown.
    Returns { source, steps: [] } when the source has no attached function sets.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    result = get_pipeline(conn, sid)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")
    return result
