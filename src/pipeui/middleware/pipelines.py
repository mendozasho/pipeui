"""API routes for pipeline read, attach, detach, and run — Phase E1/E2.

GET    /pipelines/{source_id}                          → get_pipeline workflow
POST   /pipelines/{source_id}/steps                    → attach step (commit) or dry-run suggest
POST   /pipelines/{source_id}/steps?dry_run=true       → suggest_bindings (no writes)
DELETE /pipelines/{source_id}/steps/{sfm_id}           → detach_function workflow
POST   /pipelines/{source_id}/run                      → run_pipeline workflow

§14: route modules call workflow/ only; never touch schema/, validation/,
or sql_user_table/ directly.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from pipeui.middleware.deps import get_conn
from pipeui.middleware.results_files import results_file_response, sanitise_filename
from pipeui.backend.domain.functions.attach import AttachBinding, attach_function, detach_function
from pipeui.backend.domain.functions.pipeline_read import get_pipeline
from pipeui.backend.domain.functions.suggest import suggest_bindings
from pipeui.backend.domain.functions.step_edit import patch_pipeline_step
from pipeui.backend.domain.sources.read import get_source_summary, source_exists
from pipeui.backend.domain.runner.export import (
    build_results_report,
    build_transformed_report,
    get_staging_meta,
    write_transformed_csv,
    write_transformed_xlsx,
)
from pipeui.backend.domain.runner.run import get_staging_rows, run_pipeline, run_set_across_sources

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
    output_mode: str = "append"
    scalar_values: dict | None = None   # param_id -> literal value; persisted to source_scalar_map
    output_targets: list[str] | None = None  # ordered target column_ids for a replace step (slice 4)
    append_name: str | None = None      # optional user-provided append column name (slice 4)


class PatchStepIn(BaseModel):
    position: int | None = None
    output_mode: str | None = None
    bindings: dict | None = None        # param_id -> [column_id, ...], replaces all alias_map rows
    scalar_values: dict | None = None   # param_id -> value string, upserted into source_scalar_map
    function_output: dict | None = None  # function_id -> {output_mode, append_name?, output_targets?[col_id]} (#264)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run-set")
def run_set_route(
    set_id: str = Query(...),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Run a function set across all sources it is attached to.

    ?set_id={uuid} — required; the function_set to run.

    Returns { set_id, set_name, sources: [...] } with per-source step results.
    404 when set_id is unknown or the set has no attached sources.
    """
    try:
        parsed_set_id = uuid.UUID(set_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid set_id: {set_id!r}")

    result = run_set_across_sources(conn, parsed_set_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Set {set_id!r} not found")
    if not result.get("sources"):
        raise HTTPException(status_code=404, detail=f"Set {set_id!r} has no attached sources")
    return result


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

    # Parse scalar_values: param_id str -> literal value (str). Used for scalar
    # params and str params in plain-string mode (Bug #186).
    parsed_scalars: Optional[dict] = None
    if body.scalar_values is not None:
        try:
            parsed_scalars = {
                uuid.UUID(p_id): str(value)
                for p_id, value in body.scalar_values.items()
            }
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid scalar_values: {exc}")

    # Parse ordered output-target column_ids for a replace step (slice 4).
    parsed_targets: Optional[list] = None
    if body.output_targets is not None:
        try:
            parsed_targets = [uuid.UUID(c) for c in body.output_targets]
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid output_targets: {exc}")

    result = attach_function(
        conn,
        sid,
        bindings,
        function_id=fn_id,
        set_id=st_id,
        output_mode=body.output_mode,
        scalar_values=parsed_scalars,
        output_targets=parsed_targets,
        append_name=body.append_name,
    )
    return result


@router.delete("/{source_id}/steps/{source_function_map_id}", status_code=204)
def delete_pipeline_step(
    source_id: str,
    source_function_map_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Remove a pipeline step atomically.

    Deletes the source_function_map row, all associated alias_map rows, and
    (when the set was auto-created and has no remaining references) the
    function_set + function_set_map rows.

    404 when source_function_map_id is unknown or doesn't belong to source_id.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")
    try:
        sfm_id = uuid.UUID(source_function_map_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source_function_map_id: {source_function_map_id!r}",
        )

    ok = detach_function(conn, sid, sfm_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Step {source_function_map_id!r} not found for source {source_id!r}",
        )

@router.patch("/{source_id}/steps/{source_function_map_id}")
def patch_pipeline_step_route(
    source_id: str,
    source_function_map_id: str,
    body: PatchStepIn,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Update position and/or output_mode on a pipeline step.

    Both fields are optional. Returns { ok: true } on success.
    404 when the step is not found for that source.
    422 when output_mode is not 'append' or 'replace'.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")
    try:
        sfm_id = uuid.UUID(source_function_map_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source_function_map_id: {source_function_map_id!r}",
        )

    # Parse bindings dict: param_id str -> [column_id str, ...]
    parsed_bindings = None
    if body.bindings is not None:
        try:
            parsed_bindings = {
                uuid.UUID(p_id): [uuid.UUID(c) for c in col_ids]
                for p_id, col_ids in body.bindings.items()
            }
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid bindings: {exc}")

    # Parse scalar_values dict: param_id str -> value str
    parsed_scalars = None
    if body.scalar_values is not None:
        try:
            parsed_scalars = {
                uuid.UUID(p_id): str(value)
                for p_id, value in body.scalar_values.items()
            }
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid scalar_values: {exc}")

    # Parse function_output: function_id str -> {output_mode, append_name?, output_targets?[col_id str]}
    parsed_function_output = None
    if body.function_output is not None:
        try:
            parsed_function_output = {}
            for fn_id, cfg in body.function_output.items():
                if not isinstance(cfg, dict):
                    raise ValueError(f"entry for {fn_id!r} must be a mapping")
                targets = cfg.get("output_targets")
                parsed_targets = (
                    [uuid.UUID(c) for c in targets] if targets is not None else None
                )
                parsed_function_output[uuid.UUID(fn_id)] = {
                    "output_mode": cfg.get("output_mode"),
                    "append_name": cfg.get("append_name"),
                    "output_targets": parsed_targets,
                }
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid function_output: {exc}")

    try:
        ok = patch_pipeline_step(
            conn, sid, sfm_id,
            position=body.position,
            output_mode=body.output_mode,
            bindings=parsed_bindings,
            scalar_values=parsed_scalars,
            function_output=parsed_function_output,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Step {source_function_map_id!r} not found for source {source_id!r}",
        )
    return {"ok": True}


@router.get("/{source_id}/staging")
def get_staging_rows_route(
    source_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Return the most recent staging table rows for a source.

    Returns {"columns": [...], "rows": [...]} after a transform run.
    Returns {"columns": [], "rows": []} (not an error) if no staging table exists yet.
    404 if source_id is not found in source_registry.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    # Check source exists
    if not source_exists(conn, sid):
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    return get_staging_rows(conn, sid)


@router.get("/{source_id}/staging/meta")
def get_staging_meta_route(
    source_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Return metadata about the source's latest staging table (#110).

    {"exists": bool, "row_count": int, "columns": [...]} — a cheap preflight for
    the file-download flow; never materializes rows.
    404 if source_id is not found in source_registry.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    if not source_exists(conn, sid):
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    return get_staging_meta(conn, sid)


# Domain writers + media types per download format (#110).
_FILE_EXPORT_FORMATS = {
    "csv": (write_transformed_csv, "text/csv"),
    "xlsx": (
        write_transformed_xlsx,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


@router.get("/{source_id}/export/transformed/file")
def download_transformed_file(
    source_id: str,
    format: str = Query(default="csv"),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Download the source's transformed table as a file (#110).

    ?format=csv|xlsx — the table is written server-side (DuckDB COPY TO for csv,
    streaming openpyxl for xlsx) and served with Content-Disposition: attachment,
    so large tables never round-trip through JSON in the browser.

    404 when source_id is unknown or no transform has run yet.
    422 on invalid source_id, unknown format, or a table too large for xlsx.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    if format not in _FILE_EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail=f"Unsupported export format: {format!r}")
    writer, media_type = _FILE_EXPORT_FORMATS[format]

    summary = get_source_summary(conn, sid)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    fd, tmp_path = tempfile.mkstemp(suffix=f".{format}", prefix="pipeui_export_")
    os.close(fd)
    try:
        n_rows = writer(conn, sid, tmp_path)
    except ValueError as exc:  # xlsx over the sheet row limit
        os.remove(tmp_path)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        os.remove(tmp_path)
        raise

    if n_rows is None:
        os.remove(tmp_path)
        raise HTTPException(
            status_code=404,
            detail=f"No transformed data available for source {source_id!r}",
        )

    filename = (
        f"{sanitise_filename(summary['source_name'])}_"
        f"{date.today().isoformat()}_transform.{format}"
    )
    return FileResponse(
        tmp_path,
        media_type=media_type,
        filename=filename,
        background=BackgroundTask(os.remove, tmp_path),
    )


@router.get("/{source_id}/export/results")
def export_source_results_report(
    source_id: str,
    run_type: str = Query(default="validations"),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Export the transposed results report for a source (source-tied entry point).

    Runs the source's pipeline (default run_type=validations — each validation function
    ran) and returns {"columns": [...], "rows": [...]}: one row per function run
    (function × source), with pass/fail + metadata columns, INCLUDING runs that passed.

    404 when source_id is unknown.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    summary = get_source_summary(conn, sid)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    result = run_pipeline(conn, sid, run_type)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    return build_results_report(result, source_name=summary["source_name"])


@router.get("/{source_id}/export/results/file")
def download_source_results_file(
    source_id: str,
    format: str = Query(default="csv"),
    run_type: str = Query(default="validations"),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Download the source-tied results report as a file (#152).

    ?format=csv|xlsx — re-runs the source's pipeline (same contract as the JSON
    /export/results route: the export reflects current data) and serves the
    per-function report with Content-Disposition: attachment.

    404 when source_id is unknown. 422 on invalid source_id or unknown format.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    summary = get_source_summary(conn, sid)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    result = run_pipeline(conn, sid, run_type)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    report = build_results_report(result, source_name=summary["source_name"])
    return results_file_response(report, format, summary["source_name"])


@router.get("/sets/{set_id}/export/results/file")
def download_set_results_file(
    set_id: str,
    format: str = Query(default="csv"),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Download a set run's results report as a file (#152).

    ?format=csv|xlsx — re-runs the set across all attached sources and serves the
    per-function report (flattened sources × steps) as an attachment.

    404 when set_id is unknown. 422 on invalid set_id or unknown format.
    """
    try:
        parsed_set_id = uuid.UUID(set_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid set_id: {set_id!r}")

    result = run_set_across_sources(conn, parsed_set_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Set {set_id!r} not found")

    report = build_results_report(result)
    return results_file_response(report, format, result.get("set_name") or "set")


@router.get("/{source_id}/export/transformed")
def export_transformed_report(
    source_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Export the source's transformed report (the transformed data table).

    Returns {"columns": [...], "rows": [...]} — the transformed data after all assigned
    transforms completed (the latest staging table). Returns an empty payload (not an
    error) when no transform has run, so a mixed validation/transform set exports
    cleanly (#193).

    404 when source_id is unknown.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    if not source_exists(conn, sid):
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    return build_transformed_report(conn, sid)


@router.post("/{source_id}/run")
def run_pipeline_route(
    source_id: str,
    run_type: str = Query(default="transforms"),
    set_id: Optional[str] = Query(default=None),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Execute the pipeline for a source.

    ?run_type=transforms  — execute only transform steps (default)
    ?run_type=validations — execute only validation steps
    ?run_type=set&set_id={id} — execute only the specified set
    ?run_type=all         — execute all steps (validation + transform) in position order

    Returns { run_type, steps: [...] } with per-step status.
    404 when source not found.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    parsed_set_id: Optional[uuid.UUID] = None
    if set_id is not None:
        try:
            parsed_set_id = uuid.UUID(set_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid set_id: {set_id!r}")

    try:
        result = run_pipeline(conn, sid, run_type, set_id=parsed_set_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline run failed: {exc}")

    if result is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    return result
