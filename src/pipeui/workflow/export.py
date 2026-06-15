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

§14: this is a workflow-layer module; api/ route modules call it, never schema/ directly.
The RunResult label normalization (results.normalize_label) is reused read-only — this
module never modifies results.py.
"""
from __future__ import annotations

import uuid
from typing import Any

import duckdb

from pipeui.results import normalize_label
from pipeui.workflow.run import get_staging_rows


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
