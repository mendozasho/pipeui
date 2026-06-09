---
created: 2026-06-09
phase: E2
status: approved
---

# Phase E2 — Builder Execution

## Problem Statement

Users can register sources, register functions, and assemble pipelines in the Builder — but there is no way to execute them. The Builder screen is read-only after E1: pipelines are configured but never run. Users cannot validate their data quality, cannot produce transformed outputs for stakeholders, and have no feedback on whether their pipeline configuration is correct. The gap between "I have a configured pipeline" and "I have results" is unbridged.

## Solution

Make the Builder executable. Each report card in the Builder opens a side panel with a pipeline canvas (assembled in E1) and run controls. The user can run all validation functions on a source, all transform functions on a source, or an individual function set. Transform steps chain: each step receives the output of the previous transform step as its working table. Validation steps are side-effect-only: they record pass/fail results but do not modify the working table. Run outcomes are surfaced as result tags on each pipeline card and in a new Results screen (placeholder in E2, fully implemented in F1 and F2).

## User Stories

1. As a user, I want to see a list of registered reports in the Builder screen, so that I can select which report to work with.
2. As a user, I want to click a report in the Builder to open a side panel for that report, so that I can see its pipeline and run controls.
3. As a user, I want the side panel to show the pipeline canvas with my attached function sets in their current order, so that I can see what will run.
4. As a user, I want to drag function set cards up and down in the pipeline canvas, so that I can change the execution order.
5. As a user, I want a "Run Validations" button in the side panel, so that I can execute only the validation functions in this report's pipeline.
6. As a user, I want a "Run Transforms" button in the side panel, so that I can execute only the transform functions and produce output for stakeholders.
7. As a user, I want a per-set run icon on each pipeline canvas card, so that I can run a single function set without running the entire pipeline.
8. As a user, I want to see a result tag on each pipeline canvas card after a run, so that I know at a glance whether that set succeeded, had issues, or errored.
9. As a user, I want result tags to show one of three states — success, issues, or error — so that I can quickly identify which sets need attention.
10. As a user, I want a Results nav item in the sidebar, so that I can navigate to a dedicated screen for full run output.
11. As a user, I want the Results screen to be a placeholder in E2, so that the nav exists and links through even before F1/F2 fill it in.
12. As a user, I want clicking a result tag to navigate to the Results screen, so that I can see the full output from that run.
13. As a user, I want transform steps to chain their output, so that each step receives the working table produced by the previous transform step.
14. As a user, I want a failed transform step to be skipped (the chain continues with the last good table), so that a single broken function does not block the rest of the pipeline.
15. As a user, I want a failed step to be clearly marked as failed in the result tag, so that I know the chain was interrupted at that point.
16. As a user, I want validation steps to record pass/fail results without modifying the working table, so that validations do not corrupt the data flowing through the pipeline.
17. As a user, I want to configure `output_mode` (append / replace) on each transform set card, so that I can control whether the transform result adds a new column or overwrites the bound column.
18. As a user, I want `output_mode` to default to `append`, so that transform results never silently overwrite existing columns unless I explicitly choose replace.
19. As a user, I want `output_mode` to only appear on transform set cards (not validation cards), so that the UI is not cluttered with irrelevant controls.
20. As a user, I want `pd.DataFrame`-returning functions to always replace the working table regardless of `output_mode`, so that I don't get unexpected behavior when using table-shaped transforms.
21. As a user, I want the right panel to have a Functions tab showing individual functions split into Validation and Transform sections, so that I can find the function I want to drag onto the pipeline.
22. As a user, I want the right panel to have a Sets tab showing all function sets with a type badge (Validation / Transform / Mixed), so that I can drag entire sets onto the pipeline.
23. As a user, I want to drag a function or set from the right panel onto the pipeline canvas, so that I can attach it to the current report.
24. As a user, I want the ephemeral transform output to be stored per source and replaced on re-run, so that the Results screen always shows the latest run.
25. As a user, I want the run result to include per-step outcomes, so that I can see which steps passed and which failed.
26. As a user, I want validation step results to include `rows_passed` and `rows_failed`, so that I can see the data quality outcome.
27. As a user, I want transform step results to include `rows_affected`, so that I can see how many rows were processed.

## Implementation Decisions

### Schema changes to `source_function_map`

Two new columns are added to `source_function_map`:

- `position INTEGER NOT NULL` — execution order for this step within the source's pipeline. Assigned as `MAX(position) + 1` for the source on attach. Gaps are allowed on delete (no compaction). Steps are ordered `ASC` by position at read and execution time.
- `output_mode VARCHAR NOT NULL` — controls how a transform step's result is applied to the working table. Valid values: `append` (result becomes a new column) or `replace` (result overwrites the bound column). Ignored for `pd.DataFrame`-returning functions (always replaces the working table). Default: `append`.

This is a breaking schema change. Because no real (non-dev) data exists, a DDL recreate is safe.

The `get_pipeline` workflow response must be updated to order steps by `source_function_map.position` (not by `MIN(function_set_map.position)` as currently derived).

### Ephemeral staging tables

After each successful transform step, the working table is written to an ephemeral DuckDB table named `staging_{source_id_short}_{unix_timestamp}` within the same DuckDB file. "Source id short" is the first 8 hex characters of the source UUID.

On `POST /pipelines/{source_id}/run`:
- All prior `staging_{source_id_short}_*` tables for this source are dropped first.
- The working table starts as a full copy of the source's instance table.
- After each successful transform step the working table is written/replaced in DuckDB.
- On completion the final working table persists as the staging table for this source until the next run.

Validation steps do not write a staging table — they only produce a result record.

### Execution model

**Run types:**

- `POST /pipelines/{source_id}/run?run_type=validations` — executes only steps whose set contains at least one `function_type = validation` function. Transform steps are skipped (working table passes through unchanged).
- `POST /pipelines/{source_id}/run?run_type=transforms` — executes only steps whose set contains at least one `function_type = transform` function. Validation steps are skipped.
- `POST /pipelines/{source_id}/run?run_type=set&set_id={set_id}` — executes only the single specified set attached to this source.

**Chaining (transform runs only):** steps execute in `source_function_map.position` order. Each transform step receives the current working table, runs its functions via the Phase D worker, and produces a new working table. On failure the step is skipped and the last good working table flows to the next step.

**Validation runs:** each validation step runs its functions against the source's instance table (the original data, not a working table). Results are collected per function: which rows passed, which failed.

**Worker boundary:** unchanged from Phase D. Each function call runs in its own subprocess via the Phase D worker. Arrow IPC carries data in/out. The worker never receives the DuckDB connection.

### New workflow module: `workflow/run.py`

New module containing:

- `run_pipeline(conn, source_id, run_type, set_id=None)` — orchestrates execution. Fetches the pipeline, dispatches per step, collects results, writes the staging table on transforms.
- Returns a structured result dict for the API layer.

### API

New route added to `api/pipelines.py`:

**`POST /pipelines/{source_id}/run`**
Query params: `run_type: "validations" | "transforms" | "set"`, `set_id` (required when `run_type=set`).

Response:
```
{
  "run_type": "transforms",
  "steps": [
    {
      "source_function_map_id": "...",
      "set_name": "...",
      "function_type": "transform",
      "status": "ok" | "failed",
      "rows_affected": 42,      // transform only
      "rows_passed": null,
      "rows_failed": null,
      "error": null | "..."
    },
    {
      "source_function_map_id": "...",
      "set_name": "...",
      "function_type": "validation",
      "status": "ok" | "failed",
      "rows_affected": null,
      "rows_passed": 98,        // validation only
      "rows_failed": 2,
      "error": null | "..."
    }
  ]
}
```

`status: "failed"` on a transform step implies it was skipped in the chain. Subsequent steps still run against the last good working table. A crashing step's error message is included in `error`.

404 if the source does not exist. Structured failure (not 500) if the worker crashes.

`PATCH /pipelines/{source_id}/steps/{source_function_map_id}` — new route to update `position` and/or `output_mode` on an existing attachment. Used when the user reorders cards or changes output mode.

Body: `{ "position": 2, "output_mode": "replace" }` (both optional).

### Builder screen redesign

**Layout:**
- **Center / main area:** list of registered reports (sources). Each report card shows source name, last run status tag (if any), and is clickable to open the side panel.
- **Side panel** (opens when a report is selected):
  - Top: report name + two buttons: **Run Validations** and **Run Transforms**
  - Pipeline canvas: ordered list of attached set cards (drag-to-reorder; fires `PATCH /pipelines/{source_id}/steps/{sfm_id}` with new position on drop)
  - Each set card shows: set name, type badge, per-function list, result tag (post-run), per-set run icon, `output_mode` dropdown (transform sets only), remove button
- **Right panel:** two tabs
  - **Functions tab:** individual registered functions, split into two sections — Validations (function_type=validation) and Transforms (function_type=transform). Each function is draggable onto the pipeline canvas.
  - **Sets tab:** all registered function sets, flat list. Each set card has a type badge: `Validation` (all members validation), `Transform` (all members transform), or `Mixed`. Each set is draggable onto the pipeline canvas.

**Result tag states:** `success` (all steps ok), `issues` (at least one validation step had rows_failed > 0), `error` (at least one step has status=failed). Clicking the tag navigates to the Results screen.

**Drag-to-reorder:** HTML5 drag-and-drop within the pipeline canvas. On drop, fires `PATCH` to update `position`. Optimistic reorder in the UI; reverts on API error.

### Results screen (placeholder)

New nav item **Results** added to the app shell, replacing the former Validations and Staging nav items. In E2, the screen renders a placeholder ("Run a pipeline to see results here"). F1 fills in the Validations tab; F2 fills in the Transforms tab.

`app.jsx` nav rail updates from (Data, Functions, Builder, Settings) to (Data, Functions, Builder, Results, Settings).

### CONTEXT.md / ROADMAP updates

- `CONTEXT.md` already updated: six-screen → five-screen layout, Results screen term, result tag term (done in the grilling session).
- ROADMAP.md: mark Phase E1 complete; update Phase E2, F1, F2 descriptions to reflect Results screen replacing separate Validations/Staging screens.

## Testing Decisions

### What makes a good test here

Tests assert observable state and API response shape — rows in `source_function_map`, staging table presence/absence in DuckDB, response JSON from the API. Never mock the worker or internal call sequences; use real DuckDB fixtures. Each test guards one documented behavioral guarantee and is named for it.

### Modules to test

**`tests/test_api_pipelines_run.py`** (primary seam — API via FastAPI TestClient):
- `POST /pipelines/{source_id}/run?run_type=transforms` executes transform steps in position order
- `POST /pipelines/{source_id}/run?run_type=validations` executes validation steps and returns rows_passed/rows_failed
- A failed transform step is skipped; subsequent steps receive the last good working table
- A failed step has `status: "failed"` and `error` populated in the response
- Re-running drops the prior staging table and creates a new one
- `run_type=set` executes only the specified set
- Returns 404 when source_id is unknown
- Returns structured failure (not 500) on worker crash

**`tests/test_run_workflow.py`** (workflow seam — real DuckDB for cases hard to provoke via API):
- `output_mode=append` adds a new column to the working table without removing existing columns
- `output_mode=replace` overwrites the bound column in the working table
- `pd.DataFrame` return always replaces the working table regardless of `output_mode`
- Staging table is named correctly and contains the final working table rows after a transform run
- Validation steps do not write a staging table and do not modify the working table

**`tests/test_api_pipelines.py`** (extend existing):
- `PATCH /pipelines/{source_id}/steps/{sfm_id}` updates `position` correctly
- `PATCH /pipelines/{source_id}/steps/{sfm_id}` updates `output_mode` correctly
- `GET /pipelines/{source_id}` orders steps by `source_function_map.position` (not derived from function_set_map)

**`tests/test_schema.py`** (extend existing):
- `source_function_map` has `position` and `output_mode` columns

### Prior art

- `test_api_migration.py` — API-level guarantee tests with structured failure assertions
- `test_ingestion.py` — atomicity and staging table pattern
- `test_api_pipelines.py` — E1 pipeline API tests; extend rather than duplicate

## Out of Scope

- **Set-scoped run (across all sources):** running a function set against every source it is attached to. Deferred to F1/F2.
- **Full Results screen implementation:** F1 fills in the Validations tab (pass/fail breakdown, export); F2 fills in the Transforms tab (transformed table view, export).
- **Scalar value persistence:** scalar parameter overrides entered in the UI are not persisted across runs. Deferred to v2 (CLAUDE.md Active Deferred Work).
- **Drag-to-reorder pipeline steps in the Builder via the right panel:** the right panel palette supports drag-onto-canvas (attach); reorder within the canvas is already in scope. Dragging from the right panel to reorder existing steps is not in scope.
- **Cross-source joins in the staging/Results layer:** deferred to v2.
- **Persistent staging tables:** staging tables are session-only ephemeral DuckDB tables. Persistence across server restarts is v2.
- **Multiple staging table versions per source:** only the latest run is kept per source. History is v2.

## Further Notes

- The `source_function_map` DDL change (`position` + `output_mode`) is a breaking schema change. Safe to recreate because no production data exists.
- E1's `get_pipeline` orders steps by `MIN(function_set_map.position)`. E2 must update this to order by `source_function_map.position` — the two columns serve different purposes: `function_set_map.position` orders functions *within* a set; `source_function_map.position` orders sets *within* a source's pipeline.
- The `attach_function` workflow must be updated to assign `position = MAX(position for this source) + 1` and accept `output_mode` (default `append`).
- Staging table naming `staging_{source_id_short}_{unix_timestamp}` is forward-compatible with v2 multi-version history: changing the cleanup policy (keep all vs keep latest) is the only change needed.
- The ROADMAP's Phase F1 and F2 descriptions reference "Validations screen" and "Staging screen" separately. These must be updated to reference the unified Results screen with two tabs.
