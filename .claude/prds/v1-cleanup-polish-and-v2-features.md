---
created: 2026-06-10
status: draft
tracks:
  - "Track 1: Infrastructure & Debt (no user-facing change)"
  - "Track 2: Data Screen"
  - "Track 3: Functions Screen"
  - "Track 4: Builder Screen"
  - "Track 5: Results Screen & Shared UI"
---

# PRD: v1 Cleanup, Polish, and New Features

## Problem Statement

The v1 app is functionally complete through Phase F2. Before v2 work begins, the
codebase carries known module-boundary violations, stale vocabulary, and missing
test infrastructure that will compound as complexity grows. Alongside that debt,
the app has UX rough edges (inconsistent loading states, error surfacing, visual
polish) and is missing analytical capabilities analysts need: built-in join/pivot
operations, SQL function support, and source grouping.

Work is organized into five vertical tracks — each track ships all layers
(backend + API + frontend) together for a complete user-facing outcome.

---

## Solution

### Track 1 — Infrastructure & Debt

Pure backend/tooling changes with no user-facing behaviour change. Safe to land
in any order; no design input needed.

1. **`migration.py` §3 compliance** — replace inline `_content_hash_id()` calls
   with a `ColumnRegistryUpdate` object at the write boundary; same pattern as
   `update_function_set()`. No behaviour change.

2. **`AppSettings` module boundary fix** — move `AppSettings` + `DEFAULTS` to
   `validation/settings.py`; move `load_settings()` + `save_settings()` to
   `helpers.py` (already imported by `main.py` and `api/functions.py`);
   `SettingsPatch` stays in `api/settings.py`.

3. **Return-type vocabulary cleanup** — docs-only pass: retire `vector`/`matrix`
   in `design.md`, `CLAUDE_REFERENCE.md`, and `CLAUDE.md`; replace with
   `pd.Series`/`pd.DataFrame`. Strike the Active Deferred Work entry in CLAUDE.md.

4. **`duckdb.py` → `db.py` rename** — update 8 import sites across production
   code and tests.

5. **`src/` layout reshape + CLI entry points** — restructure to `src/pipeui/`;
   add `pipeui init` (idempotent: creates `pipeui.config.json` + DuckDB file in
   the user's working directory) and `pipeui start` (launches uvicorn) via
   `pyproject.toml` `[project.scripts]`. Separate issue from item 4.

6. **Quirk-encoding fixture builder** — `make_quirky_file(tmp_path, spec)` factory
   in `conftest.py`: generates CSV/xlsx files with mixed-type columns (for
   `TRY_CAST` pre-check), ambiguous-type columns (for inference), and
   VARCHAR-fallback columns.

---

### Track 2 — Data Screen

All items touch the source detail drawer or the source list. Ship as independent
issues within this track; no ordering dependency between them except where noted.

1. **Source grouping display** — the source list shows a human-readable pattern
   label (e.g. `sales_jan_*`) derived from the stored `pattern` regex instead of
   the raw regex. Frontend-only formatting helper; no backend change. Sources with
   the same `pattern` are visually grouped under a shared group header.

2. **Column mismatch confirmation** — when ingesting a file whose columns differ
   from the source's current `source_column_map` (new columns, removed columns,
   type changes), the backend diffs the incoming schema before writing and returns
   a `schema_diff` payload with `requires_confirmation: true`. Frontend shows a
   diff popup; on confirmation, re-calls ingest with `confirm_schema_diff=true`.
   The normal `column_registry` + `source_column_map` write path then executes —
   no new schema or flag needed.

3. **Collapsible columns section** — the columns section in the source detail
   drawer defaults to collapsed with a count indicator on the right of the section
   header (e.g. `Columns  14 ›`). User expands on demand. Frontend-only.

4. **PK uniqueness warning badge** — source detail drawer shows a warning badge
   when `row_count > COUNT(DISTINCT pk_column)` on the source's instance table.
   Computed at drawer open time from the existing `GET /sources/{id}` response
   (add `distinct_pk_count` to the response). No schema changes.

5. **Data page source list redesign** *(Design → Code)* — density and information
   hierarchy redesign. Design decides: card grid vs table, visual prominence of
   source name / row count / last ingested date / status pill. Design brief
   required before Code implementation.

---

### Track 3 — Functions Screen

1. **SQL file support** — scan `.sql` files from `functions_paths` alongside `.py`
   files in `POST /functions/scan`. SQL functions always operate at DataFrame
   granularity; `{source_table}` placeholder substituted at run time. Metadata
   via comment block at top of file:
   ```
   -- name: clean_nulls
   -- description: Remove rows where key columns are null
   -- type: transform
   SELECT * FROM {source_table} WHERE col_a IS NOT NULL
   ```
   `-- type` omission sets `function_type = 'unknown'`; Functions screen shows an
   **Unknown** type badge with tooltip: *"Add `-- type: transform` or
   `-- type: validation` to this file's header and rescan."* Shared scanner
   helpers (e.g. `register_function_entry()`) used by both `.py` and `.sql`
   scanners — no duplication. Frontend: SQL functions appear in the functions list
   with a SQL badge; behaviour otherwise identical to Python functions.

2. **Functions drawer redesign** *(Design → Code)* — card layout rework and Run
   button styling brought in line with the design system. Design brief required.

---

### Track 4 — Builder Screen

1. **Built-in pipeline steps (join + pivot)** — new `source_builtin_map` table
   for pipeline-level built-in steps (separate from `source_function_map`); extend
   `function_set_map` to support built-in steps (nullable `builtin_step_id` FK —
   a step is either a function or a built-in, never both). `builtin_type` is an
   extensible VARCHAR enum: `join`, `pivot` (future: `filter`, `sql`).
   `builtin_config` is a JSON blob validated per `builtin_type` at the workflow
   layer. Execution happens as DuckDB SQL assembled from config — not via the
   worker subprocess. Built-in steps are interleaved with function steps in the
   pipeline ordered by `position`.

   **Join config:** `{ right_source_id, join_type: inner|left|right|full,
   on: [{left_col, right_col}], keep_columns: "all" | [col_id, ...] }`
   Column keep is select-all or multi-select.

   **Pivot config:** `{ index_columns: [col_id], pivot_column: col_id,
   value_columns: [{col_id, aggregations: [sum|avg|count|min|max]}] }`
   Multiple aggregation methods per value column; UI lets user drag columns into
   aggregation slots.

   **Functions screen — Built-ins tab:** new tab positioned between Functions and
   Sets (order: Functions | Built-ins | Sets). Built-in cards are draggable into
   the pipeline canvas in the Builder and into function sets on the Sets tab.
   Dragging onto the canvas opens a configuration popup.

   **API additions:** `POST /sources/{id}/attach-builtin`,
   `DELETE /sources/{id}/attach-builtin/{step_id}`,
   `PATCH /sources/{id}/attach-builtin/{step_id}`.
   `GET /sources/{id}/pipeline` updated to return built-in steps interleaved with
   function steps by position.

2. **Builder step cards redesign** *(Design → Code)* — order number badges on
   each step card; column multi-select checkboxes redesigned (currently plain HTML
   checkboxes that do not match the design system). Design brief required.

---

### Track 5 — Results Screen & Shared UI

1. **Relative timestamps on result cards** — replace raw ISO strings with a
   `timeAgo(isoString)` helper ("2 minutes ago"). Absolute ISO shown in a tooltip
   on hover. Frontend-only.

2. **Export filename preview** — show the resolved filename as a label near the
   export button before download triggers (e.g.
   `sales_jan_validate_nulls_20260610.csv`). Inline label; no modal. Frontend-only.

3. **Results screen empty state** *(Design → Code)* — full page treatment: icon,
   heading copy, sub-copy. Currently a single centred text line. Design decides
   icon, copy, and layout within the existing CSS token system.

4. **Shared loading components** *(Design → Code)* — `Spinner` and `LoadingState`
   components added to `ui.jsx`. After Design: audit pass across all four screens
   replaces inline `"Loading…"` text, `disabled={loading}` patterns, and ad-hoc
   spinner divs with the shared components.

5. **Shared error surfacing** *(Design → Code)* — separate Design brief specifies
   the rule for inline error (under a form field, inside a drawer) vs flash toast.
   After Design: audit pass standardises all error paths including `console.error`-
   only paths that currently have no user-facing feedback.

---

## User Stories

### Track 1 — Infrastructure & Debt

1. As a developer, I want `migration.py` to use `ColumnRegistryUpdate` at the
   write boundary so that the hash pattern is consistent across the codebase.
2. As a developer, I want `AppSettings` in `validation/settings.py` and
   `load_settings`/`save_settings` in `helpers.py` so that `api/` does not own
   validation objects.
3. As a developer, I want all `vector`/`matrix` prose replaced with
   `pd.Series`/`pd.DataFrame` so that the vocabulary is consistent.
4. As a developer, I want `duckdb.py` renamed to `db.py` so that it does not
   shadow the third-party `duckdb` package.
5. As a developer, I want the package structured as `src/pipeui/` so it is
   installable cleanly as a dependency.
6. As a user, I want to run `pipeui init` in my project directory to create the
   config file and database without touching the installed package.
7. As a user, I want to run `pipeui start` to launch the app server from my
   project directory.
8. As a developer, I want a `make_quirky_file` fixture that generates edge-case
   CSV/xlsx files so that migration and inference tests have realistic inputs.

### Track 2 — Data Screen

9. As an analyst, I want sources with the same naming pattern grouped together in
   the source list so that monthly/weekly uploads of the same report appear together.
10. As an analyst, I want group headers to show a readable label (e.g. `sales_jan_*`)
    instead of raw regex so that the grouping is self-explanatory.
11. As an analyst, I want to be warned before ingesting a file whose columns differ
    from the source's existing schema so that I do not accidentally corrupt it.
12. As an analyst, I want to see exactly which columns were added, removed, or
    changed in the mismatch popup so that I can make an informed decision.
13. As an analyst, I want the columns section in the source drawer to be collapsed
    by default with a count on the header so that wide tables don't overwhelm the
    drawer.
14. As an analyst, I want a warning badge in the source drawer when my PK column
    has duplicate values so that I know the data may have integrity issues.
15. As an analyst, I want the source list to clearly show name, row count, and
    last ingested date in a well-prioritised layout so that I can scan sources
    quickly.

### Track 3 — Functions Screen

16. As an analyst, I want to register `.sql` files from my functions path so that
    I can write analytical queries without Python.
17. As an analyst, I want SQL functions to receive the full source table so that I
    can write any query without binding individual columns.
18. As an analyst, I want SQL functions without a `-- type` header to show an
    Unknown badge with a tooltip so that I know how to fix the registration.
19. As an analyst, I want the function detail drawer layout and Run button to match
    the rest of the app's design so that the experience feels consistent.

### Track 4 — Builder Screen

20. As an analyst, I want to drag a Join step into my pipeline so that I can
    combine two sources without writing code.
21. As an analyst, I want to configure a join's type, join columns, and which
    output columns to keep so that I control the output precisely.
22. As an analyst, I want to select all columns from a join or multi-select only
    the ones I need so that I can control output width.
23. As an analyst, I want to drag a Pivot step into my pipeline so that I can
    reshape a source without writing code.
24. As an analyst, I want to configure a pivot's index columns, pivot column, and
    value columns with multiple aggregation methods so that I get exactly the
    summary I need.
25. As an analyst, I want to include built-in steps in a function set so that I can
    reuse a standard sequence of operations across sources.
26. As an analyst, I want the Built-ins tab between Functions and Sets so that the
    tab order matches my typical workflow.
27. As an analyst, I want each pipeline step to show a clear order number so that
    I can verify the execution sequence at a glance.
28. As an analyst, I want polished column multi-select checkboxes in the Builder
    that match the design system.

### Track 5 — Results Screen & Shared UI

29. As an analyst, I want result cards to show relative timestamps ("2 minutes ago")
    so that I can quickly gauge how recent a run was.
30. As an analyst, I want to hover a relative timestamp to see the exact date and
    time for precise reference.
31. As an analyst, I want to see the resolved filename before downloading an export
    so that I know exactly what file I am getting.
32. As an analyst, I want the Results screen to show a helpful empty state with an
    icon and copy when no runs exist so that I know what to do next.
33. As an analyst, I want a consistent spinner while any screen loads data so that
    I always know when the app is working.
34. As an analyst, I want buttons disabled during async operations so that I cannot
    trigger duplicate requests.
35. As an analyst, I want inline error messages near the relevant form field or
    panel when something goes wrong so that I know exactly what to fix.
36. As an analyst, I want flash toasts for transient errors (network failures,
    server errors) so that errors are visible without blocking my workflow.

---

## Implementation Decisions

### Track 1

- `ColumnRegistryUpdate` is constructed from the existing `ColumnRegistryEntry`
  fields; `update_obj.content_hash_id` replaces the inline call. No behaviour
  change — structural §3 compliance only.

- `AppSettings` and `DEFAULTS` move to `validation/settings.py`. `load_settings()`
  and `save_settings()` move to `helpers.py` because they are already imported by
  `main.py` and `api/functions.py`. `SettingsPatch` stays in `api/settings.py`.

- CLI entry points declared under `[project.scripts]` in `pyproject.toml`.
  `pipeui init` writes to `Path.cwd()` — the user's working directory, never the
  installed package directory. Safe to re-run on an existing DB (idempotent).

- `make_quirky_file(tmp_path, spec)` returns a `Path`. `spec` declares which
  column kinds to include: `mixed_type`, `ambiguous_type`, `varchar_fallback`.

### Track 2

- Column mismatch detection happens in the ingest workflow before the write: diff
  incoming inferred columns against `source_column_map` rows. API returns
  `schema_diff` + `requires_confirmation: true` (HTTP 200). On re-call with
  `confirm_schema_diff=true`, the normal write path executes. No new schema or
  flag needed — accepted state is fully captured by `source_column_map` after
  the write.

- `distinct_pk_count` is added to the `GET /sources/{id}` response; frontend
  computes `row_count > distinct_pk_count` and renders the badge. No new endpoint.

- Source grouping is client-side: group the `GET /sources` response by `pattern`
  value; derive human-readable label by replacing `\d+` with `*`.

### Track 3

- SQL functions share the `function_registry` write path with Python functions.
  A SQL function has no `parameter` rows (always DataFrame-level).
  `function_signature` stored as `{source_table}: pd.DataFrame -> pd.DataFrame`
  (or `-> pd.Series[bool]` for validation type).

- Shared scanner helper `register_function_entry()` used by both `.py` and `.sql`
  scanners — no duplication between them.

### Track 4

- Separate tables: `source_builtin_map (step_id UUID4, source_id, builtin_type
  VARCHAR, builtin_config JSON, position INTEGER)`. `function_set_map` extended
  with nullable `builtin_step_id` FK (step is either `function_id` or
  `builtin_step_id`, never both).

- `builtin_type` stored as VARCHAR (not enum) so future values (`filter`, `sql`)
  require no migration. Allowed-value validation at the workflow layer.

- Built-in execution: workflow layer assembles DuckDB SQL from `builtin_config`
  and runs it against the connection directly — not via the worker subprocess.

- `GET /sources/{id}/pipeline` updated to union `source_function_map` and
  `source_builtin_map` ordered by `position`.

### Track 5

- `timeAgo(isoString)` is a small pure JS helper added to `app.jsx` or `ui.jsx`
  (whichever is the shared utilities home). Tooltip uses the browser's native
  `title` attribute or a lightweight CSS tooltip — no new dependency.

- Track 5 Design items (`Spinner`, `LoadingState`, error surfacing, empty state)
  each require a written spec (component name, props, CSS tokens to use, rule for
  when each pattern applies) before any Code issue opens. The spec is the handoff
  artifact — no Figma files.

---

## Testing Decisions

Good tests assert **external behaviour** — API responses, DB state after a
workflow call, rendered output — not internal implementation details.

- **Track 1** — existing test suites provide coverage for most items; verify no
  regressions after structural moves. Add import-location tests for the module
  boundary moves. `make_quirky_file` is used by new tests in
  `test_migration.py` and `test_ingestion.py` covering `TRY_CAST` and
  VARCHAR-fallback paths. Prior art: `tests/test_migration.py`,
  `tests/test_ingestion.py`.

- **Track 2** — new tests in `test_ingestion.py`: assert `requires_confirmation`
  fires on schema diff; assert write proceeds on `confirm_schema_diff=true`; assert
  no diff when schema is unchanged. Assert `distinct_pk_count` in `GET
  /sources/{id}` response. Prior art: `tests/test_api_sources.py`.

- **Track 3** — new `test_sql_functions.py`: scan a temp `.sql` file, assert
  `function_registry` row created with correct `function_type`; assert `unknown`
  type when `-- type` missing; assert execution returns expected rows. Prior art:
  `tests/test_functions.py`.

- **Track 4** — new `test_builtins.py`: assert `source_builtin_map` row created
  on attach; assert pipeline query returns built-in steps interleaved by position;
  assert join output DataFrame shape; assert pivot output columns match
  aggregations. Prior art: `tests/test_attach.py`, `tests/test_run_workflow.py`.

- **Track 5** — `timeAgo` and filename preview are pure logic; unit-testable in
  isolation. Design-first items verified by spec compliance review and manual
  happy-path testing after implementation.

---

## Out of Scope

- **v2 scalar persistence** — per-source scalar argument override store.
- **Persistent staging tables** — transform results surviving session refresh.
- **Cross-source join UI** in the Results layer (deferred from F2).
- **Run history persistence** — result cards written to DuckDB.
- **Results filter/search** by source, function, date, or type.
- **Scheduled / watched runs**.
- **SQL TEXT built-in step** — freeform SQL in the Built-ins tab. `builtin_type`
  is designed to accommodate it without migration.
- **Filter built-in step** — same extensibility note.
- **Bulk pattern-matched file ingest** — auto-infer + bulk confirm.
- **Mobile / responsive layout** — desktop-only through at least v2.
- **PK uniqueness enforcement at registration** — only the UI warning badge ships;
  full enforcement deferred to v2.

---

## Further Notes

- **Design-first gate:** no Code issue opens for a Design-first item until a
  written Design brief exists (component name, props, CSS tokens, interaction
  rule). The brief is the prerequisite.

- **`src/` reshape merges last** in Track 1 — it touches every import path and
  will conflict with in-flight branches. Land items 1–4 and 6 first.

- **Track 4 dependency:** the built-in steps schema (`source_builtin_map`,
  `function_set_map` extension) must be shipped before the Built-ins tab UI, since
  the UI depends on the API.

- **`builtin_type` extensibility:** `filter` and `sql` built-in types are not
  implemented in this PRD. The schema is explicitly designed to accommodate them
  with no future migration.
