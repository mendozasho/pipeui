"""API routes for cross-source validation runs — Phase F1.

POST /validations/run?function_id={id}
    Fan-out: run a validation function across every source it is attached to.
    Returns per-source pass/fail counts and failing rows.

§14: route module calls workflow/ only — never touches schema/, validation/,
or sql_user_table/ directly.
"""
from __future__ import annotations

import uuid

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from pipeui.db import get_conn
from pipeui.workflow.run import run_validation_across_sources

router = APIRouter(prefix="/validations", tags=["validations"])


@router.post("/run")
def run_validation_by_function(
    function_id: str = Query(...),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Run a validation function across all sources it is attached to.

    Returns:
      { function_id, function_name, sources: [...] }
    where each source entry has:
      source_id, source_name, status ("ok"|"failed"), rows_passed, rows_failed,
      pass_rate, failing_rows (uncapped), error

    A worker crash on one source marks it status="failed" without blocking
    the remaining sources. The overall HTTP response is always 200 with a
    structured payload.

    404 when function_id is unknown.
    """
    try:
        fid = uuid.UUID(function_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid function_id: {function_id!r}")

    result = run_validation_across_sources(conn, fid)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Function {function_id!r} not found")

    return result
