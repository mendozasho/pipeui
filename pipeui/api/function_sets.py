"""API routes for function sets — Phase D2.

§14: api/ calls workflow/ only; never touches schema/, validation/, or sql_user_table/.
"""
from __future__ import annotations

from typing import Optional

import duckdb
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeui.helpers import get_conn
from pipeui.workflow.function_sets import (
    create_function_set,
    delete_function_set,
    get_function_set,
    list_function_sets,
    update_function_set,
)

router = APIRouter(prefix="/function-sets", tags=["function-sets"])


class CreateSetBody(BaseModel):
    set_name: str
    set_description: Optional[str] = None
    members: list[str] = []


class PatchSetBody(BaseModel):
    set_name: Optional[str] = None
    set_description: Optional[str] = None
    clear_description: bool = False
    members: Optional[list[str]] = None


@router.get("")
def get_function_sets(conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    """Return all function sets as summaries, ordered by set_name."""
    return list_function_sets(conn)


@router.get("/{set_id}")
def get_function_set_detail(
    set_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Return full detail for one function set including ordered members. 404 if not found."""
    from fastapi import HTTPException
    detail = get_function_set(conn, set_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Function set not found")
    return detail


@router.patch("/{set_id}")
def patch_function_set(
    set_id: str,
    body: PatchSetBody,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Update a function set — name, description, and/or member list (replace-members).

    Returns updated full detail on success.
    Returns 404 if set_id not found.
    Returns 422 structured failure on duplicate set_name.
    """
    from fastapi import HTTPException
    result = update_function_set(
        conn,
        set_id=set_id,
        set_name=body.set_name,
        set_description=body.set_description,
        members=body.members,
        clear_description=body.clear_description,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Function set not found")
    if hasattr(result, "has_failures") and result.has_failures():
        reasons = [reason for _, reason in result.failures]
        return JSONResponse(status_code=422, content={"ok": False, "errors": reasons})
    return {"ok": True, "set": result}


@router.post("")
def post_function_set(
    body: CreateSetBody,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Create a new function set.

    Returns the created set summary on success.
    Returns a structured failure (not 500) on duplicate set_name.
    """
    result = create_function_set(
        conn,
        set_name=body.set_name,
        set_description=body.set_description,
        members=body.members,
    )
    if hasattr(result, "has_failures") and result.has_failures():
        reasons = [reason for _, reason in result.failures]
        return JSONResponse(status_code=422, content={"ok": False, "errors": reasons})
    return {"ok": True, "set": result}


@router.delete("/{set_id}", status_code=204)
def delete_function_set_route(
    set_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Delete a function set and its membership rows. 404 if not found.

    Member functions in function_registry are never removed.
    """
    from fastapi import HTTPException
    result = delete_function_set(conn, set_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Function set not found")
