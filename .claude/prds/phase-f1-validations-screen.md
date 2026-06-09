---
created: 2026-06-09
phase: F1
status: approved
---

# Phase F1 — Validations Screen

## Problem Statement

Analysts can run validation functions on their data via the Builder screen, but the Results screen is a placeholder — "Run a pipeline to see results here." After running validations, there is no way to see which rows failed, no way to understand which validation rule caught them, and no way to export the failures so the analyst can correct the underlying data. The pipeline runs but produces no actionable output.

## Solution

Implement the Validations screen within the existing Results nav item. The screen has two sub-tabs: **By Source** (pick a source, see all attached validation functions and their per-function pass/fail breakdown + failing rows) and **By Function** (pick a validation function, run it across every source it is attached to, see per-source results). Each view provides a capped in-app preview of failing rows and a full CSV export per function or per source. Results are ephemeral — held in React state for the session — and export is the durable artifact for the analyst to take away.

## User Stories

1. As an analyst, I want to navigate to the Results screen and see a Validations view, so that I have a dedicated place to review data quality outcomes.
2. As an analyst, I want to select a source in the "By Source" sub-tab, so that I can see all validation functions attached to that source.
3. As an analyst, I want to run validations for a selected source from the Results screen, so that I do not have to go back to the Builder to get fresh results.
4. As an analyst, I want to see a per-function breakdown of results (not per set), so that I can pinpoint exactly which validation rule is failing.
5. As an analyst, I want to see the set name as a grouping label above its functions, so that I understand which set each function belongs to.
6. As an analyst, I want to see pass/fail counts for each validation function, so that I know at a glance how severe the data quality issue is.
7. As an analyst, I want to see a pass rate percentage per function, so that I can compare severity across functions quickly.
8. As an analyst, I want to see a capped preview (up to 200 rows) of the full failing rows for each function, so that I can spot patterns without leaving the app.
9. As an analyst, I want the failing rows preview to show all column values (not just the PK), so that I have enough context to understand why the row failed.
10. As an analyst, I want to export all failing rows for a specific function to CSV, so that I can take the failures into Excel and fix them.
11. As an analyst, I want clicking a result tag on a Builder pipeline card to navigate to Results → Validations → By Source pre-scoped to that source, so that I land directly in the right context after a run.
12. As an analyst, I want to switch to the "By Function" sub-tab, so that I can see how a single validation rule performs across all sources it is attached to.
13. As an analyst, I want to select a validation function in the "By Function" sub-tab, so that I can focus on one rule across all its sources.
14. As an analyst, I want to trigger a cross-source run for the selected function from the Results screen, so that all sources are validated in one action.
15. As an analyst, I want to see per-source pass/fail counts in the "By Function" view, so that I know which sources are affected by this rule.
16. As an analyst, I want to see the failing rows preview per source in the "By Function" view, so that I can inspect the failures in context.
17. As an analyst, I want to export failing rows per source in the "By Function" view to CSV, so that I can share source-specific failure reports with the relevant data owners.
18. As an analyst, I want validation results to persist in the session while I navigate between screens, so that I do not have to re-run just because I switched tabs.
19. As an analyst, I want to see an empty state when no run has happened yet in this session, so that the screen does not look broken on first load.
20. As an analyst, I want to see a loading indicator while a validation run is in progress, so that I know the system is working.
21. As an analyst, I want to see a clear error message when a validation function's worker crashes, so that I know the function itself failed rather than the data.
22. As an analyst, I want function errors to be isolated — other functions in the same run still show their results — so that one broken function does not block the rest.
23. As an analyst, I want the failing rows preview to be horizontally scrollable for wide tables, so that wide sources do not break the layout.
24. As an analyst, I want the CSV export filename to include the source name and function name, so that exported files are identifiable without opening them.

## Implementation Decisions

### Backend: per-function result shape

The current `_execute_validation_step` in `workflow/run.py` aggregates `rows_passed` / `rows_failed` across all functions in a step and returns a single result entry per step (set-level). This must change.

`_execute_validation_step` is replaced with a per-function loop that returns one result entry per validation function within the step, each containing:

```
{
  "function_id": "...",
  "function_name": "...",
  "set_name": "...",          // grouping label only
  "set_id": "...",
  "status": "ok" | "failed",
  "rows_passed": 42,
  "rows_failed": 5,
  "pass_rate": 0.894,         // rows_passed / total_rows, null on error
  "failing_rows": [...],      // list of full row dicts, capped at 200
  "error": null | "..."
}
```

`failing_rows` contains the full row values (all columns) for rows where the boolean result was `False`. The cap of 200 matches the existing data preview cap (`GET /sources/{id}/rows?limit=200`). All failing rows are returned in the export endpoint — the 200-row cap applies only to the run response.

The `POST /pipelines/{source_id}/run?run_type=validations` response shape is updated accordingly — `steps` becomes a flat list of per-function entries (not per-set). The existing `run_type=transforms` path is unchanged.

### New endpoint: cross-source validation run

A new route `POST /validations/run` with query param `function_id` is added to a new `api/validations.py` module. The backend:

1. Looks up all `source_function_map` rows where the function (via its set) is attached to a source.
2. For each source, runs the function against that source's instance table via the existing worker boundary.
3. Returns a single response with per-source results in the same per-function shape as above, plus `source_name` and `source_id` on each entry.

Response shape:
```
{
  "function_id": "...",
  "function_name": "...",
  "sources": [
    {
      "source_id": "...",
      "source_name": "...",
      "status": "ok" | "failed",
      "rows_passed": 98,
      "rows_failed": 2,
      "pass_rate": 0.98,
      "failing_rows": [...],   // capped at 200, full rows on export
      "error": null | "..."
    }
  ]
}
```

404 if `function_id` is unknown. Returns structured result (not 500) if any source's worker crashes — that source gets `status: "failed"` and the rest still run.

### Export endpoint

A new `GET /validations/export` endpoint returns the full (uncapped) failing rows for a given function + source pair as a CSV download. Query params: `function_id`, `source_id`. The endpoint re-runs the validation against that source on request (no server-side result storage). The response uses `Content-Disposition: attachment; filename="{source_name}_{function_name}_failures.csv"`.

Alternatively, the export can be client-side: the frontend builds the CSV from the full `failing_rows` payload returned by the run endpoint if the result is held in React state. **Decision: client-side CSV generation using the result already in state**, avoiding a second server round-trip and keeping the export logic simple. The run endpoint returns all failing rows (uncapped) in a separate `"failing_rows_full"` field alongside the capped `"failing_rows"` preview, or the cap is removed entirely for the export case. **Resolved: the run endpoints return all failing rows uncapped; the frontend caps the in-app preview to 200 rows for display and uses the full array for export.** This keeps the export path simple and avoids a redundant endpoint.

### Frontend: Validations screen

`screen-results.jsx` is fully replaced (it is currently a placeholder). The new component renders the Validations screen with:

- A two-button sub-tab bar: **By Source** and **By Function**
- No outer tab shell (Validations / Transforms) — that is added in F2

**By Source sub-tab:**
- Source selector dropdown (populated from `GET /sources`)
- "Run Validations" button — calls `POST /pipelines/{source_id}/run?run_type=validations`
- Results grouped by set name (label only), then per-function rows showing: function name, status badge, rows passed, rows failed, pass rate
- Expandable failing rows table per function (collapsed by default, expand to see preview ≤200 rows)
- "Export CSV" button per function — triggers client-side CSV generation from the full failing rows array in state
- Empty state when no run has happened this session; loading state during run

**By Function sub-tab:**
- Function selector dropdown (populated from `GET /functions`, filtered to `function_type = validation`)
- "Run Across All Sources" button — calls `POST /validations/run?function_id={id}`
- Results as a per-source list: source name, status badge, rows passed, rows failed, pass rate
- Expandable failing rows table per source (same pattern as By Source)
- "Export CSV" button per source — client-side CSV from state

**Navigation from Builder:** when the Builder result tag is clicked, `app.jsx` navigates to Results and passes `{ source_id }` as context. The Validations screen reads this context, pre-selects the source in the By Source sub-tab, and shows the last-known result for that source (if a run happened this session) or the empty state.

**Session state:** React state in `app.jsx` holds a `validationResults` map keyed by `source_id` (for By Source runs) and a `crossSourceResults` map keyed by `function_id` (for By Function runs). Both persist for the session duration and are not cleared on screen navigation.

### Module boundary

`api/validations.py` calls `workflow/run.py` functions only. It does not touch `schema/`, `validation/`, or `sql_user_table/` directly. The existing `api/pipelines.py` is not modified except to update the validation step response shape.

### CLAUDE_REFERENCE.md update

§14 is updated to reflect:
- New `api/validations.py` route module: `POST /validations/run` · `GET /validations/export` (removed — export is client-side)
- Updated response shape for `POST /pipelines/{source_id}/run?run_type=validations`
- New `screen-results.jsx` description (Validations screen, two sub-tabs)

## Testing Decisions

### What makes a good test

Tests assert observable API response shape and DuckDB state using real DuckDB fixtures — never mock the worker or internal loops. Each test guards one documented behavioral guarantee and is named for it. The highest useful seam is the API via FastAPI `TestClient`; drop to the workflow seam only for cases that cannot be provoked cleanly at the API level.

### Modules to test

**`tests/test_api_validations.py`** (new, primary seam):
- `POST /validations/run?function_id={id}` returns per-source results for all attached sources
- A worker crash on one source marks that source `status: "failed"` and does not block other sources
- `failing_rows` contains full row values for rows where the boolean result was `False`
- Returns 404 when `function_id` is unknown
- Returns empty `sources` list when the function has no `source_function_map` attachments

**`tests/test_api_pipelines_run.py`** (extend existing):
- `POST /pipelines/{source_id}/run?run_type=validations` returns per-function entries (not per-set)
- Each per-function entry includes `function_name`, `set_name`, `rows_passed`, `rows_failed`, `pass_rate`, `failing_rows`
- `failing_rows` is a list of full row dicts for the rows that returned `False`
- A function whose worker crashes returns `status: "failed"` with an `error` message; other functions in the same run still return their results
- `pass_rate` is `null` when `status` is `"failed"`

**`tests/test_run_workflow.py`** (extend existing):
- A boolean `pd.Series` result correctly separates passing and failing rows
- A boolean `pd.DataFrame` (single bool column) result correctly separates passing and failing rows
- A scalar `bool` result maps to `rows_passed=1`/`rows_failed=0` or `0`/`1`

### Prior art

- `test_api_migration.py` — API-level guarantee tests with structured failure assertions
- `test_api_pipelines_run.py` — existing validation step tests to extend
- `test_ingestion.py` — pattern for asserting row-level content in DuckDB fixtures

## Out of Scope

- **Transforms tab / F2:** the Transforms tab and the shared tab shell between Validations and Transforms are deferred to F2. F1 ships the Validations screen only, with its own two sub-tabs (By Source, By Function).
- **Shared code cleanup between F1 and F2:** a dedicated cleanup agent ticket runs after both F1 and F2 exist to identify and consolidate shared utilities (result-shape helpers, export logic, sub-tab layout). Not pre-emptively abstracted in F1.
- **Run history persistence:** validation results are session-only React state. DuckDB-backed run history is v2.
- **Row-level navigation from Results to Data screen:** clicking a failing row to navigate to that row in the source table is v2.
- **Scalar parameter overrides in the validation run:** v2 (CLAUDE.md Active Deferred Work).
- **Cross-source joins in the Results layer:** v2.
- **Export to Excel (.xlsx):** CSV only in v1. Excel export is v2.

## Further Notes

- The `failing_rows` array returned by both endpoints is uncapped at the API layer. The frontend caps the in-app preview display to 200 rows; the full array is available in React state for client-side CSV export. This avoids a dedicated export endpoint and a second server round-trip.
- The cross-source run endpoint `POST /validations/run?function_id={id}` fans out synchronously across all attached sources in one request. If a source has a very large instance table, this request may be slow. Async fan-out is a v2 concern; in v1 a loading spinner is sufficient.
- `pass_rate` is computed as `rows_passed / (rows_passed + rows_failed)` — total rows is derived, not passed separately, to avoid a mismatch when a worker partially processes a table.
- The CSV filename convention `{source_name}_{function_name}_failures.csv` (By Source export) and `{source_name}_{function_name}_failures.csv` (By Function per-source export) uses the same pattern; the client sanitises special characters before constructing the filename.
- ROADMAP.md Phase F1 entry should be updated to mark this phase complete once shipped.
