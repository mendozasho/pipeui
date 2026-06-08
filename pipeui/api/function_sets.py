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
from pipeui.workflow.function_sets import create_function_set, list_function_sets

router = APIRouter(prefix="/function-sets", tags=["function-sets"])


class CreateSetBody(BaseModel):
    set_name: str
    set_description: Optional[str] = None
    members: list[str] = []


@router.get("")
def get_function_sets(conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    """Return all function sets as summaries, ordered by set_name."""
    return list_function_sets(conn)


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
