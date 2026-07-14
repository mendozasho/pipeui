"""Result-export builders — slice runner-execution/5.

Two consumption exports for the Results screen (PRD: Results surfacing — three tiers):

  build_results_report(run_result) -> {"columns": [...], "rows": [...]}
      The **transposed results report**: one row per RunResult keyed by its normalized
      label, with pass/fail + metadata columns. INCLUDES runs that fully passed — there
      is no filtering on rows_failed, so the report is a complete record of every run.
      Accepts either runner output shape (both entry points collapse to one row list):
        - source-tied validations: run_pipeline(conn, source_id, "validations")
            -> { run_type, steps: [...] }   (each step already carries the RunResult contract)
        - Functions-page cross-source: run_validation_across_sources(conn, function_id)
            -> { function_id, function_name, sources: [...] }  (each per-source entry IS a RunResult)

  build_transformed_report(conn, source_id) -> {"columns": [...], "rows": [...]}
      The **transformed report**: the source's transformed data table after all assigned
      transforms completed (the latest staging table). A validation-only run writes no
      staging table, so this returns an empty payload rather than raising — that is the
      #193 staging-export-failure fix for a mixed validation/transform set.

File-download exports (#110 — large tables must never round-trip through JSON):

  get_staging_meta(conn, source_id) -> {"exists", "row_count", "columns"}
      Cheap preflight for the download flow — no row materialization.

  write_transformed_csv(conn, source_id, dest_path) -> row count | None
      DuckDB-native COPY TO; None when no staging table exists.

  write_transformed_xlsx(conn, source_id, dest_path) -> row count | None
      openpyxl write-only streaming over Arrow batches; raises ValueError when the
      table exceeds the xlsx sheet row limit. None when no staging table exists.

§14: this is a workflow-layer module; api/ route modules call it, never schema/ directly.
The RunResult label normalization (results.normalize_label) is reused read-only — this
module never modifies results.py.
"""
from __future__ import annotations

import uuid
from typing import Any

import duckdb

from pipeui.backend.data.base.results import normalize_label
from pipeui.backend.data.runner.staging import latest_staging
from pipeui.backend.domain.runner.run import get_staging_rows

# xlsx sheet limit is 1,048,576 rows; one is reserved for the header.
XLSX_MAX_DATA_ROWS = 1_048_575


# Columns surfaced in the transposed results report, in order. Every RunResult row
# carries these keys (missing values default to None) so the exported file is rectangular.
_RESULT_COLUMNS = [
    "result_id",
    "label",
    "function_name",
    "function_type",
    "status",
    "rows_passed",
    "rows_failed",
    "pass_rate",
    "error",
]


def _result_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Project one runner result entry onto the transposed report's column set.

    Re-normalizes the `label` defensively (acceptance #3 — no leading underscores or
    odd tokens) even though the runner already normalizes it, so a malformed label can
    never reach the exported file. Falls back to the function name for the label seed.
    """
    label_seed = entry.get("label") or entry.get("function_name")
    row: dict[str, Any] = {col: entry.get(col) for col in _RESULT_COLUMNS}
    row["label"] = normalize_label(label_seed)
    return row


def build_results_report(run_result: dict | None) -> dict[str, Any]:
    """Build the transposed results report from either runner output shape.

    Returns {"columns": [...], "rows": [...]} — one row per RunResult, including runs
    that fully passed. Returns an empty payload for a None/empty run result.
    """
    if not run_result:
        return {"columns": list(_RESULT_COLUMNS), "rows": []}

    # The cross-source (Functions-page) shape carries a `sources` list; the source-tied
    # shape carries a `steps` list. Both are lists of per-RunResult dicts.
    entries = run_result.get("sources")
    if entries is None:
        entries = run_result.get("steps") or []

    rows = [_result_row(e) for e in entries]
    return {"columns": list(_RESULT_COLUMNS), "rows": rows}


def build_transformed_report(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> dict[str, Any]:
    """Build the transformed report (the transformed data table) for a source.

    Returns the latest staging table as {"columns": [...], "rows": [...]} — the
    transformed data after all assigned transforms completed. Returns an empty payload
    (not an error) when no transform has run, so a mixed validation/transform set
    exports cleanly (#193).
    """
    return get_staging_rows(conn, source_id)


def get_staging_meta(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> dict[str, Any]:
    """Return {"exists", "row_count", "columns"} for the source's latest staging table.

    Never materializes rows — one count(*) plus a DESCRIBE. {"exists": False,
    "row_count": 0, "columns": []} when no transform has run yet.
    """
    staged = latest_staging(conn, source_id)
    if staged is None:
        return {"exists": False, "row_count": 0, "columns": []}
    tname, _ts = staged
    (row_count,) = conn.execute(f'SELECT count(*) FROM "{tname}"').fetchone()
    columns = [r[0] for r in conn.execute(f'DESCRIBE "{tname}"').fetchall()]
    return {"exists": True, "row_count": int(row_count), "columns": columns}


def write_transformed_csv(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    dest_path: str,
) -> int | None:
    """Write the latest staging table to dest_path as CSV; return the row count.

    Returns None when no staging table exists (mirrors the #193 empty-payload
    contract). NULL/NaN cells become empty CSV fields natively — no JSON
    round-trip, no Python-side row handling.
    """
    staged = latest_staging(conn, source_id)
    if staged is None:
        return None
    tname, _ts = staged
    # COPY TO cannot take a bound parameter for the path; the staging table name
    # is repo-generated (staging_{hex}_{ts}) so only the path needs escaping.
    escaped = dest_path.replace("'", "''")
    row = conn.execute(
        f'COPY (SELECT * FROM "{tname}") TO \'{escaped}\' (FORMAT CSV, HEADER)'
    ).fetchone()
    return int(row[0])


def write_transformed_xlsx(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    dest_path: str,
) -> int | None:
    """Write the latest staging table to dest_path as xlsx; return the row count.

    Returns None when no staging table exists. Raises ValueError before writing
    anything when the table exceeds XLSX_MAX_DATA_ROWS — the format cannot hold
    it and the caller should steer the user to CSV. Streams Arrow record batches
    through an openpyxl write-only workbook so the full table is never
    materialized in memory.
    """
    staged = latest_staging(conn, source_id)
    if staged is None:
        return None
    tname, _ts = staged

    (row_count,) = conn.execute(f'SELECT count(*) FROM "{tname}"').fetchone()
    row_count = int(row_count)
    if row_count > XLSX_MAX_DATA_ROWS:
        raise ValueError(
            f"Table has {row_count:,} rows — the xlsx format holds at most "
            f"{XLSX_MAX_DATA_ROWS:,} data rows. Export as CSV instead."
        )

    from openpyxl import Workbook

    columns = [r[0] for r in conn.execute(f'DESCRIBE "{tname}"').fetchall()]
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Transformed")
    ws.append(columns)

    reader = conn.execute(f'SELECT * FROM "{tname}"').to_arrow_reader(10_000)
    for batch in reader:
        for row in batch.to_pylist():
            ws.append([_xlsx_safe(row[c]) for c in columns])
    wb.save(dest_path)
    return row_count


def _xlsx_safe(value: Any) -> Any:
    """Coerce a cell value to a type openpyxl accepts natively."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, dict)):
        return str(value)
    if isinstance(value, float) and value != value:  # NaN is not representable in xlsx
        return None
    return value
