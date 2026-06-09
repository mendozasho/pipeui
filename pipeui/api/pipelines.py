"""API routes for pipeline read/write — Phase E1.

GET  /pipelines/{source_id}              → get_pipeline workflow
POST /pipelines/{source_id}/steps        → attach step (commit) or dry-run suggest
POST /pipelines/{source_id}/steps?dry_run=true  → suggest_bindings (no writes)

§14: route modules call workflow/ only; never touch schema/, validation/,
or sql_user_table/ directly.
"""
from __future__ import annotations

import uuid
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from pipeui.helpers import get_conn
from pipeui.workflow.attach import AttachBinding, attach_function, get_pipeline, suggest_bindings

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class BindingIn(BaseModel):
    param_id: str
    column_ids: list[str] = []


class AttachStepIn(BaseModel):
    function_id: str | None = None
    set_id: str | None = None
    bindings: list[BindingIn] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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


@router.post("/{source_id}/steps")
def attach_step(
    source_id: str,
    body: AttachStepIn,
    dry_run: bool = Query(default=False),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Attach a function or function set to a source, or dry-run for suggestions.

    With ?dry_run=true: returns suggested column bindings without writing any
    rows to source_function_map, alias_map, or function_set.

    Body: { "function_id": "..." } or { "set_id": "..." },
          "bindings": [{ "param_id": "...", "column_ids": ["...", ...] }]

    Commit (dry_run=false) returns:
      { "ok": True, "source_function_map_id": "..." } on success.
      { "ok": False, "missing_params": [...], "detail": "..." } on validation failure.

    Dry-run returns:
      { "params": [{ param_id, param_name, param_type, suggested_columns }] }
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    # Parse the provided function_id / set_id
    fn_id: Optional[uuid.UUID] = None
    st_id: Optional[uuid.UUID] = None
    try:
        if body.function_id is not None:
            fn_id = uuid.UUID(body.function_id)
        if body.set_id is not None:
            st_id = uuid.UUID(body.set_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Validate exactly one of function_id / set_id
    if (fn_id is None) == (st_id is None):
        raise HTTPException(
            status_code=422,
            detail="Exactly one of 'function_id' or 'set_id' must be provided",
        )

    if dry_run:
        try:
            return suggest_bindings(conn, sid, function_id=fn_id, set_id=st_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Non-dry-run: commit the attach
    # Verify the source exists
    if get_pipeline(conn, sid) is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    bindings = [
        AttachBinding(
            param_id=uuid.UUID(b.param_id),
            column_ids=[uuid.UUID(c) for c in b.column_ids],
        )
        for b in body.bindings
    ]

    result = attach_function(
        conn,
        sid,
        bindings,
        function_id=fn_id,
        set_id=st_id,
    )
    return result
