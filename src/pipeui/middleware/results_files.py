"""Results-report file downloads — shared middleware helper (#152).

The three results-export entry points (source-tied in pipelines.py, cross-source
function in validations.py, set in pipelines.py) all end the same way:
build_results_report → write csv/xlsx to a temp file → FileResponse with
attachment disposition + cleanup. That tail lives here so the route modules
stay thin and the download contract cannot drift between entry points.

§14: middleware-layer module; calls domain writers only.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from pipeui.backend.domain.runner.export import write_results_csv, write_results_xlsx

_RESULTS_FILE_FORMATS = {
    "csv": (write_results_csv, "text/csv"),
    "xlsx": (
        write_results_xlsx,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


def sanitise_filename(s: str) -> str:
    # Mirrors the frontend sanitiseFilename so download names match the old UX.
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s or "")


def results_file_response(report: dict[str, Any], format: str, label: str) -> FileResponse:
    """Write a results report ({"columns", "rows"}) to a temp file and serve it.

    Filename: {sanitised label}_{ISO date}_validation.{format}. 422 on an unknown
    format. The temp file is removed by a background task after the response.
    """
    if format not in _RESULTS_FILE_FORMATS:
        raise HTTPException(status_code=422, detail=f"Unsupported export format: {format!r}")
    writer, media_type = _RESULTS_FILE_FORMATS[format]

    fd, tmp_path = tempfile.mkstemp(suffix=f".{format}", prefix="pipeui_export_")
    os.close(fd)
    try:
        writer(report, tmp_path)
    except Exception:
        os.remove(tmp_path)
        raise

    filename = f"{sanitise_filename(label)}_{date.today().isoformat()}_validation.{format}"
    return FileResponse(
        tmp_path,
        media_type=media_type,
        filename=filename,
        background=BackgroundTask(os.remove, tmp_path),
    )
