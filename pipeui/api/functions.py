"""API routes for function registration and listing — Phase D.

§14: api/ calls workflow/ only; never touches schema/, validation/, or sql_user_table/.
"""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from pipeui.helpers import get_conn
from pipeui.workflow.functions import list_functions, scan_functions

router = APIRouter(prefix="/functions", tags=["functions"])


@router.get("")
def get_functions(conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    """Return all registered functions with their parameters, ordered by function_name."""
    return list_functions(conn)


@router.get("/{function_id}")
def get_function_detail(function_id: str, conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    """Return full detail for one function including parameters and attached sources.

    Returns 404 if the function_id is not found.
    attached_sources is an empty list (not an error) when no source_function_map rows exist.
    """
    detail = get_function(conn, function_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Function not found")
    return detail


@router.post("/scan")
def scan(conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    """Scan all directories in functions_paths and register/update eligible functions.

    Returns {"log": [...]} with one entry per discovered function (added,
    re-registered, or skipped with reason).
    """
    from pipeui.api.settings import load_settings
    settings = load_settings()
    log = scan_functions(conn, settings.functions_paths)
    return JSONResponse({"log": log})
