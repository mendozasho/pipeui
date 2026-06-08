from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import duckdb
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from pipeui.duckdb import create_schema, get_connection
from pipeui.workflow.create import create_source

router = APIRouter(prefix="/sources", tags=["sources"])

# Hardcoded for now; will become an app setting when that feature is wired up.
DB_PATH = Path("pipeui.db")

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


def get_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    conn = get_connection(str(DB_PATH))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


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
    return _source_rows(conn)


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
        return {"ok": True, "source": record}

    finally:
        Path(tmp_path).unlink(missing_ok=True)
