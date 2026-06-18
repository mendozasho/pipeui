from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal

import duckdb
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeui.backend.data.base.db import get_conn
from pipeui.backend.domain.sources.create import create_source, find_source_by_pattern, peek_header_columns
from pipeui.backend.domain.sources.ingestion import get_source_detail, get_source_rows, ingest_source
from pipeui.backend.domain.sources.migration import migrate_column
from pipeui.backend.domain.runner.resolve import TRANSFORMED, resolve_frame

router = APIRouter(prefix="/sources", tags=["sources"])

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


def _source_rows(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            sr.source_id,
            sr.source_name,
            sr.date_ingested,
            sr.date_registered,
            sr.ingestion_method,
            sr.pattern,
            sr.primary_key,
            sr.table_url,
            sr.content_hash_id
        FROM source_registry sr
        ORDER BY sr.date_registered DESC, sr.source_name
        """
    ).fetchall()

    col_names = [
        "source_id", "source_name", "date_ingested", "date_registered",
        "ingestion_method", "pattern", "primary_key", "table_url", "content_hash_id",
    ]

    results = []
    for row in rows:
        record = dict(zip(col_names, row))
        record["source_id"] = str(record["source_id"])
        record["content_hash_id"] = str(record["content_hash_id"])
        record["date_ingested"] = record["date_ingested"].isoformat() if record["date_ingested"] else None
        record["date_registered"] = record["date_registered"].isoformat() if record["date_registered"] else None

        cols = conn.execute(
            """
            SELECT cr.column_id, cr.column_name, cr.column_type
            FROM column_registry cr
            JOIN source_column_map scm ON scm.column_id = cr.column_id
            WHERE scm.source_id = ?
            ORDER BY cr.column_name
            """,
            [record["source_id"]],
        ).fetchall()

        record["columns"] = [
            {"column_id": str(c[0]), "column_name": c[1], "column_type": c[2]}
            for c in cols
        ]
        results.append(record)

    return results


@router.get("")
def list_sources(conn: duckdb.DuckDBPyConnection = Depends(get_conn)):
    rows = _source_rows(conn)
    for row in rows:
        detail = get_source_detail(conn, uuid.UUID(row["source_id"]), include_functions=False)
        row["row_count"] = detail["row_count"] if detail else 0
    return rows


@router.post("")
async def register_source(
    file: UploadFile,
    source_name: str = Form(...),
    primary_key: str = Form(...),
    ingestion_method: str = Form("upsert"),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{suffix}'. Accepted: {sorted(ALLOWED_EXTENSIONS)}",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        # Check if an existing source's pattern matches this filename before creating
        matched_id = find_source_by_pattern(conn, file.filename or "")
        if matched_id is not None:
            rows = _source_rows(conn)
            record = next((r for r in rows if r["source_id"] == str(matched_id)), None)
            return {"ok": True, "matched_existing": True, "source": record}

        source_id, failed = create_source(
            conn=conn,
            file_path=tmp_path,
            source_name=source_name,
            primary_key=primary_key,
            ingestion_method=ingestion_method,
        )

        if failed.has_failures():
            reasons = [reason for _, reason in failed.failures]
            return JSONResponse(
                status_code=422,
                content={"ok": False, "errors": reasons},
            )

        rows = _source_rows(conn)
        record = next((r for r in rows if r["source_id"] == str(source_id)), None)
        return {"ok": True, "matched_existing": False, "source": record}

    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/peek-columns")
async def peek_columns_route(file: UploadFile):
    """Return only the header row's column names for the uploaded file.

    Reads just the first row server-side (openpyxl read-only / a single csv row) so
    the Register modal can populate its column picker without the browser parsing a
    multi-hundred-thousand-row workbook on the main thread. Works for files too large
    to round-trip through the JS heap.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{suffix}'. Accepted: {sorted(ALLOWED_EXTENSIONS)}",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        return {"columns": peek_header_columns(tmp_path)}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/{source_id}")
def get_source(
    source_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    detail = get_source_detail(conn, sid, include_functions=True)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")
    return detail


@router.get("/{source_id}/join-columns")
def get_join_columns(
    source_id: str,
    transformed: bool = False,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Columns the join modal binds against for a right-hand source.

    With ``transformed=false`` (default) returns the source's RAW registered columns
    — the instance table's schema. With ``transformed=true`` returns the source's
    TRANSFORMED column set: the columns of the frame ``resolve_frame`` resolves for
    the transformed mode (its latest staging output, materialized on demand if the
    source has never run). This is the endpoint the join column picker calls with the
    transformed flag so the user maps keys against the columns the join will actually
    see (PRD Implementation Decisions -> Join honors the toggle).

    Returns {"columns": [{"column_name", "column_type"}]}. 404 when the source is
    unknown.
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    exists = conn.execute(
        "SELECT 1 FROM source_registry WHERE source_id = ?", [sid]
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    if not transformed:
        cols = conn.execute(
            """
            SELECT cr.column_name, cr.column_type
            FROM column_registry cr
            JOIN source_column_map scm ON scm.column_id = cr.column_id
            WHERE scm.source_id = ?
            ORDER BY cr.column_name
            """,
            [sid],
        ).fetchall()
        return {"columns": [{"column_name": c[0], "column_type": c[1]} for c in cols]}

    # Transformed: resolve the transformed frame and report its columns. The dtype is
    # best-effort from the resolved frame (the registry has no row for derived columns).
    frame, _ref = resolve_frame(conn, sid, TRANSFORMED)
    return {
        "columns": [
            {"column_name": str(name), "column_type": str(dtype)}
            for name, dtype in zip(frame.columns, frame.dtypes)
        ]
    }


@router.get("/{source_id}/rows")
def get_rows(
    source_id: str,
    limit: int = 200,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """Return up to `limit` rows from the instance table.

    404 if the source is not registered.
    Returns {"columns": [...], "rows": [...]} with an empty rows list when not
    yet ingested or when the table has no data (§9 Row preview note).
    """
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    # 404 when source is not in source_registry at all
    exists = conn.execute(
        "SELECT 1 FROM source_registry WHERE source_id = ?", [sid]
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    rows = get_source_rows(conn, sid, limit=limit)
    columns = list(rows[0].keys()) if rows else []
    return {"columns": columns, "rows": rows}


class ColumnMigrateBody(BaseModel):
    column_type: str
    scope: Literal["this_source", "all_shared"] = "this_source"
    on_uncastable: Literal["nullify", "abort"] = "abort"


@router.patch("/{source_id}/columns/{col_id}")
def migrate_column_route(
    source_id: str,
    col_id: str,
    body: ColumnMigrateBody,
    dry_run: bool = False,
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    """PATCH /sources/{source_id}/columns/{col_id}?dry_run=false

    Migrate a column to a new type (§7). The route validates IDs, checks the
    column belongs to the source, then delegates to migrate_column().

    Dry-run returns castable/uncastable counts + shared_sources without mutating.
    Commit returns ok=True + rows_migrated on success, or a structured failure
    payload (never a 500) on validation error or aborted migration.
    """
    # Validate UUIDs
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")
    try:
        cid = uuid.UUID(col_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid col_id: {col_id!r}")

    # 404 if source not found
    source_exists = conn.execute(
        "SELECT 1 FROM source_registry WHERE source_id = ?", [sid]
    ).fetchone()
    if source_exists is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id!r} not found")

    # 404 if column not found in column_registry at all
    col_exists = conn.execute(
        "SELECT 1 FROM column_registry WHERE column_id = ?", [cid]
    ).fetchone()
    if col_exists is None:
        raise HTTPException(status_code=404, detail=f"Column {col_id!r} not found")

    # 404 if column does not belong to this source
    mapping_exists = conn.execute(
        "SELECT 1 FROM source_column_map WHERE source_id = ? AND column_id = ?",
        [sid, cid],
    ).fetchone()
    if mapping_exists is None:
        raise HTTPException(
            status_code=404,
            detail=f"Column {col_id!r} does not belong to source {source_id!r}",
        )

    result = migrate_column(
        conn=conn,
        source_id=sid,
        column_id=cid,
        new_type=body.column_type,
        scope=body.scope,
        on_uncastable=body.on_uncastable,
        dry_run=dry_run,
    )

    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)

    return result


@router.post("/{source_id}/ingest")
async def ingest_source_route(
    source_id: str,
    file: UploadFile,
    ingestion_method: str | None = Form(default=None),
    confirm_schema_diff: bool = Form(default=False),
    conn: duckdb.DuckDBPyConnection = Depends(get_conn),
):
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid source_id: {source_id!r}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{suffix}'. Accepted: {sorted(ALLOWED_EXTENSIONS)}",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        rows_ingested, skipped_pks, failed, schema_diff = ingest_source(
            conn=conn,
            source_id=sid,
            file_path=tmp_path,
            ingestion_method=ingestion_method,
            confirm_schema_diff=confirm_schema_diff,
        )

        # Schema diff detected and not confirmed — ask the frontend for confirmation
        if schema_diff is not None:
            return {"requires_confirmation": True, "schema_diff": schema_diff}

        if failed.has_failures():
            reasons = [reason for _, reason in failed.failures]
            return JSONResponse(
                status_code=422,
                content={"ok": False, "errors": reasons},
            )

        return {
            "ok": True,
            "rows_ingested": rows_ingested,
            "rows_skipped": [str(pk) for pk in skipped_pks],
        }

    finally:
        Path(tmp_path).unlink(missing_ok=True)
