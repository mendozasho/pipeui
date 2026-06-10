---
created: 2026-06-10
status: draft
tracks:
  - "Track 1: v1 Code Cleanup (Claude Code)"
  - "Track 2: v1 UI Polish (Claude Design → Claude Code)"
  - "Track 3: v1 New Features (Claude Code)"
  - "Track 4: v2 New Features"
---

# PRD: v1 Cleanup, UI Polish, and v2 Features

## Problem Statement

The v1 app is functionally complete through Phase F2. Before v2 work begins, the
codebase carries known module-boundary violations, stale vocabulary, missing test
coverage, and UX rough edges accumulated during rapid delivery. Separately, users
need new analytical capabilities (built-in join/pivot steps, SQL functions, source
grouping) that go beyond what Python-function pipelines alone can provide.

Three categories of work address this:

1. **Code correctness & debt** — boundary violations, naming smells, and missing
   test infrastructure that will compound as v2 adds complexity.
2. **UI polish** — inconsistent loading states, error surfacing, and screen-level
   visual treatments that make the app feel unfinished.
3. **New v1 capabilities** — built-in pipeline operations (join, pivot), SQL
   function support, and source grouping that give analysts more expressive power
   without writing Python.

---

## Solution

### Track 1 — v1 Code Cleanup (Claude Code, no design input needed)

Thirteen targeted fixes shipped as independent issues:

1. **`migration.py` REFACTOR_PLAN §3 compliance** — replace inline
   `_content_hash_id()` calls with a proper `ColumnRegistryUpdate` object at the
   write boundary; same pattern already used in `update_function_set()`.

2. **`AppSettings` module boundary fix** — move `AppSettings` + `DEFAULTS` to
   `validation/settings.py`; move `load_settings()` + `save_settings()` to
   `helpers.py` (already imported by `main.py` and `api/functions.py`);
   `SettingsPatch` stays in `api/settings.py`.

3. **Return-type vocabulary cleanup** — docs-only pass: retire `vector`/`matrix`
   in `design.md`, `CLAUDE_REFERENCE.md`, and `CLAUDE.md`; replace with
   `pd.Series`/`pd.DataFrame`. Strike the Active Deferred Work entry in CLAUDE.md.

4. **`duckdb.py` → `db.py` rename** — update 8 import sites across production
   code and tests; run test suite to verify.

5. **`src/` layout reshape + CLI entry points** — restructure the package to
   `src/pipeui/`; add `pipeui init` (idempotent: creates `pipeui.config.json` +
   DuckDB file in the user's working directory) and `pipeui start` (launches
   uvicorn) via `pyproject.toml` `[project.scripts]`. Config and DB are always
   written to the user's project root, not the installed package directory.
   Separate issue from item 4.

6. **Quirk-encoding fixture builder** — `make_quirky_file(tmp_path, spec)` fixture
   factory in `conftest.py`: generates CSV/xlsx files with (a) mixed-type columns
   for `TRY_CAST` pre-check testing, (b) ambiguous-type columns for inference
   testing, (c) columns that force the `VARCHAR` fallback.

7. **PK uniqueness warning badge** — source detail drawer shows a warning badge
   when `row_count > COUNT(DISTINCT pk_column)` for the source's instance table.
   UI-only; no schema changes. Badge is ephemeral — computed at drawer open time.

8. **Collapsible columns section in source detail drawer** — columns section
   defaults to collapsed with a count indicator on the right of the section header
   (e.g. `Columns  14 ›`). User expands on demand. No threshold — always
   collapsible.

9. **Export filename preview** — show the resolved filename (e.g.
   `sales_jan_validate_nulls_20260610.csv`) as a label near the export button
   before download triggers. No modal — inline label only.

10. **Relative timestamps on result cards** — replace raw ISO strings with a
    `timeAgo(isoString)` helper (e.g. "2 minutes ago"). Absolute ISO shown in a
    tooltip on hover.

11. **Source grouping UI — human-readable pattern display** — the source list
    currently shows the raw regex stored in `pattern` (e.g. `sales_jan_\d+`).
    Replace with a cleaned display form (e.g. `sales_jan_*`) derived from the
    pattern string. No backend change; purely a frontend formatting helper.

12. **Column mismatch confirmation popup on ingest** — when a file being ingested
    has columns that differ from the source's current `source_column_map` (new
    columns, removed columns, or type mismatches), show a diff popup before the
    write proceeds. On confirmation the normal `column_registry` +
    `source_column_map` write path executes. No new schema or flag needed — the
    accepted state is fully captured by what is in `source_column_map` after the
    write.

13. **SQL file support** — scan `.sql` files from `functions_paths` alongside
    `.py` files in `POST /functions/scan`. SQL functions always operate at
    DataFrame granularity; `{source_table}` placeholder is substituted at run
    time with the instance table name. Metadata declared via comment block:
    ```
    -- name: clean_nulls
    -- description: Remove rows where key columns are null
    -- type: transform
    SELECT * FROM {source_table} WHERE col_a IS NOT NULL
    ```
    `-- type` must be `transform` or `validation`; omission sets
    `function_type = 'unknown'`. Functions screen shows an **Unknown** type badge;
    hovering shows tooltip: *"Type unknown — add `-- type: transform` or
    `-- type: validation` to this file's header and rescan."* Any helpers shared
    between `.py` and `.sql` scanning live in a shared module — no duplication.

---

### Track 2 — v1 UI Polish (Claude Design brief required first)

Each item requires a Design brief before Claude Code implementation. The brief
must specify: the component name and props to add to `ui.jsx`, which existing CSS
tokens to use, and what the handoff artifact looks like (written spec, not Figma).

1. **Shared loading components** — `Spinner` and `LoadingState` components added
   to `ui.jsx`. After Design: audit pass across all four screens replaces inline
   `"Loading…"` text, `disabled={loading}` patterns, and ad-hoc spinner divs with
   the shared components.

2. **Shared error surfacing** — spec the rule for when to use inline error
   (under a form field, inside a drawer) vs flash toast. After Design: audit pass
   standardises all error paths; eliminates `console.error`-only paths that have
   no user-facing feedback.

3. **Results screen empty state** — full page treatment: icon, heading copy, and
   sub-copy. Currently a single centred text line. Design decides icon choice,
   copy, and layout within the existing CSS token system.

4. **Builder step cards** — two sub-items:
   - Order number badges on each pipeline step card
   - Column multi-select checkboxes redesigned (currently plain HTML checkboxes)

5. **Functions drawer** — card layout rework and Run button styling brought in
   line with the design system. Run button currently does not match the accent/ink
   colour scheme of other primary actions.

6. **Data page source list** — density and information hierarchy redesign. The
   source list is a table today; Design decides whether a card grid or table is
   correct, and what visual prominence each datum (source name, row count, last
   ingested, status pill) should carry.

---

### Track 3 — v1 New Features (Claude Code)

#### 3A — Built-in Pipeline Steps (join + pivot)

Analysts need to join two sources or pivot a source's data as part of a pipeline
without writing Python. Built-in steps slot into the pipeline alongside Python
and SQL functions, execute in position order, and can be included in function
sets.

**Schema additions:**
- `source_builtin_map (step_id UUID4, source_id, builtin_type VARCHAR,
  builtin_config JSON, position INTEGER)` — pipeline-level built-in steps,
  separate from `source_function_map`.
- `function_set_map` extended: a set step is either a `function_id` reference or
  a `builtin_step_id` reference, never both (nullable FK pattern).

`builtin_type` is an extensible enum: current values `join`, `pivot`; future
values `filter`, `sql` slot in without schema changes.

**Join config shape:**
```json
{
  "right_source_id": "...",
  "join_type": "inner | left | right | full",
  "on": [{"left_col": "col_id", "right_col": "col_id"}],
  "keep_columns": "all | [col_id, ...]"
}
```
Column keep is either select-all or a multi-select list.

**Pivot config shape:**
```json
{
  "index_columns": ["col_id"],
  "pivot_column": "col_id",
  "value_columns": [
    {"col_id": "...", "aggregations": ["sum", "avg"]}
  ]
}
```
Multiple aggregation methods per value column; UI allows dragging columns into
aggregation slots.

**Functions screen — Built-ins tab:** new tab between the Functions tab and the
Sets tab (order: Functions | Built-ins | Sets). Built-in cards are draggable into
the pipeline canvas in the Builder, triggering a configuration popup. Built-ins
can also be dragged into function sets on the Sets tab.

**API additions:** `POST /sources/{id}/attach-builtin`, `DELETE
/sources/{id}/attach-builtin/{step_id}`, `PATCH
/sources/{id}/attach-builtin/{step_id}` (update config). `GET
/sources/{id}/pipeline` updated to return built-in steps interleaved with
function steps by position.

**Execution:** built-in steps are executed by the workflow layer as DuckDB SQL
assembled from `builtin_config` — not via the worker subprocess. The result
DataFrame is passed to the next pipeline step as if it were a function's output.

#### 3B — Source Grouping (full implementation, v1)

The backend already derives and stores a `pattern` regex per source via
`infer_pattern()`. This track exposes grouping fully in the UI:

- Source list groups sources by their `pattern` field; sources with the same
  pattern appear under a shared group header showing the human-readable pattern
  label.
- When ingesting a file into a source that already has a `source_column_map`,
  the backend diffs the incoming file's inferred columns against the stored
  schema before writing. If any mismatch is detected (new column, removed column,
  type change), a diff popup is shown to the user. Confirmation proceeds with the
  normal `column_registry` + `source_column_map` write path. No new schema or
  flag needed.
- Future (out of scope): auto-infer files on disk matching the pattern and offer
  bulk-ingest with a confirmation list.

---

## User Stories

### Track 1 — Code Cleanup

1. As a developer, I want `migration.py` to use `ColumnRegistryUpdate` at the
   write boundary so that the hash recomputation and collision check follow the
   same pattern as the rest of the codebase.
2. As a developer, I want `AppSettings` in `validation/settings.py` and
   `load_settings`/`save_settings` in `helpers.py` so that `api/` does not own
   validation objects.
3. As a developer, I want all `vector`/`matrix` prose references replaced with
   `pd.Series`/`pd.DataFrame` so that the vocabulary is consistent across all
   documentation.
4. As a developer, I want `duckdb.py` renamed to `db.py` so that the module name
   does not shadow the third-party `duckdb` package.
5. As a developer, I want the package structured as `src/pipeui/` so it is
   installable cleanly as a dependency.
6. As a user, I want to run `pipeui init` in my project directory to create the
   config file and database without touching the installed package.
7. As a user, I want to run `pipeui start` to launch the app server from any
   terminal in my project directory.
8. As a developer, I want a `make_quirky_file` fixture that generates edge-case
   CSV/xlsx files so that migration and inference tests have realistic inputs.
9. As an analyst, I want to see a warning badge in the source drawer when my
   source's primary key column has duplicate values so that I know the data may
   have integrity issues.
10. As an analyst, I want the columns section in the source drawer to be collapsed
    by default so that I can focus on other details without scrolling past dozens
    of columns.
11. As an analyst, I want to see the column count on the collapsed columns section
    header so that I know how many columns exist without expanding.
12. As an analyst, I want to see the resolved filename before I download an export
    so that I know exactly what file I am getting.
13. As an analyst, I want result cards to show relative timestamps ("2 minutes
    ago") so that I can quickly gauge how recent a run was.
14. As an analyst, I want to hover a relative timestamp to see the exact date and
    time so that I can reference it precisely when needed.
15. As an analyst, I want source names in the source list to display a clean
    pattern label (e.g. `sales_jan_*`) instead of the raw regex so that the UI is
    readable.
16. As an analyst, I want to be warned before ingesting a file whose columns differ
    from the source's existing schema so that I do not accidentally corrupt the
    source's column map.
17. As an analyst, I want to see exactly which columns were added, removed, or
    changed in the mismatch popup so that I can make an informed decision before
    confirming.
18. As an analyst, I want to register `.sql` files from my functions path so that I
    can write analytical queries without Python.
19. As an analyst, I want SQL functions to receive the full source table so that I
    can write any query I need without binding individual columns.
20. As an analyst, I want SQL functions without a `-- type` header to show an
    Unknown badge with a tooltip so that I know how to fix the registration.

### Track 2 — UI Polish

21. As an analyst, I want a consistent spinner component while any screen is
    loading data so that I always know when the app is working.
22. As an analyst, I want buttons to be disabled while their async action is in
    flight so that I cannot trigger duplicate requests.
23. As an analyst, I want inline error messages under form fields when my input is
    invalid so that I know exactly what to fix.
24. As an analyst, I want flash toasts for transient errors (network failures,
    server errors) so that errors are visible but do not block my workflow.
25. As an analyst, I want the Results screen to show a helpful empty state with an
    icon and descriptive copy when no runs have been made so that I know what to
    do next.
26. As an analyst, I want each pipeline step in the Builder to show a clear order
    number so that I can verify the execution sequence at a glance.
27. As an analyst, I want the column multi-select checkboxes in the Builder step
    cards to be visually polished and consistent with the design system.
28. As an analyst, I want the function detail drawer to have a layout and Run
    button that match the rest of the app's design language.
29. As an analyst, I want the source list on the Data page to clearly show the
    source name, row count, and last ingested date in a well-prioritised hierarchy
    so that I can scan my sources at a glance.

### Track 3 — v2 Features

30. As an analyst, I want to drag a Join step into my pipeline so that I can
    combine two sources without writing code.
31. As an analyst, I want to configure a join's type (inner, left, right, full),
    join columns, and which output columns to keep so that I have full control
    over the join.
32. As an analyst, I want to select all columns from both sources in a join, or
    multi-select only the ones I need so that I can control the output width.
33. As an analyst, I want to drag a Pivot step into my pipeline so that I can
    reshape a source without writing code.
34. As an analyst, I want to configure a pivot's index columns, pivot column, and
    value columns with one or more aggregation methods so that I get exactly the
    summary I need.
35. As an analyst, I want to drag aggregation methods onto value columns in the
    pivot UI so that configuring multi-aggregation feels natural.
36. As an analyst, I want to include built-in steps (join, pivot) in a function
    set so that I can reuse a standard sequence of operations across multiple
    sources.
37. As an analyst, I want the Built-ins tab on the Functions screen to be
    positioned between the Functions tab and the Sets tab so that the tab order
    matches the typical workflow (functions → operations → sets).
38. As an analyst, I want my sources grouped by naming pattern in the source list
    so that monthly/weekly uploads of the same report type appear together.
39. As an analyst, I want group headers to show a human-readable label derived
    from the pattern so that the grouping is self-explanatory.

---

## Implementation Decisions

### Track 1

- `ColumnRegistryUpdate` is constructed from the existing `ColumnRegistryEntry`
  fields; `update_obj.content_hash_id` replaces the inline `_content_hash_id()`
  call. The collision check and UPDATE use the object's computed hash — no
  behaviour change, only structural compliance with §3.

- `AppSettings` and `DEFAULTS` move to `validation/settings.py`. `load_settings()`
  and `save_settings()` move to `helpers.py` because they are already imported by
  `main.py` and `api/functions.py` — they are shared utilities, not route-local
  helpers. `SettingsPatch` (request-body model) stays in `api/settings.py`.

- The `src/` reshape updates `pyproject.toml` (package discovery), all import
  paths, and `conftest.py`. CLI entry points are declared under
  `[project.scripts]` in `pyproject.toml`. `pipeui init` writes
  `pipeui.config.json` and the DuckDB file to `Path.cwd()` (the user's working
  directory), not relative to the installed package.

- `make_quirky_file(tmp_path, spec)` returns a `Path` to a temp file. `spec` is a
  dict declaring column kinds: `{"mixed_type": True, "ambiguous_type": True,
  "varchar_fallback": True}`. Only the kinds requested are included.

- PK uniqueness warning is computed at drawer open time via the existing `GET
  /sources/{id}` detail endpoint — no new endpoint needed. The frontend computes
  `row_count > distinct_pk_count` from the response and renders the badge.

- Column mismatch detection happens in the ingest workflow before the write path:
  diff incoming inferred columns against `source_column_map` rows for the source.
  The API returns a `schema_diff` payload (new columns, removed columns, type
  changes) when a mismatch is detected, with HTTP 200 and a `requires_confirmation`
  flag. The frontend shows the diff popup; on user confirmation, re-calls the
  ingest endpoint with `confirm_schema_diff=true`.

- SQL function registration shares the `function_registry` + `parameter` write
  path with Python function registration. A SQL function has no `parameter` rows
  (always DataFrame-level). `function_signature` is stored as `{source_table}:
  pd.DataFrame -> pd.DataFrame` (or `-> pd.Series[bool]` for validation type).
  The scan workflow is refactored into a shared `register_function_entry()`
  helper used by both the `.py` and `.sql` scanners.

### Track 2

- Each Design brief must specify: component name, props interface, which existing
  CSS tokens to use (e.g. `var(--accent)`, `var(--text-4)`), and the precise rule
  for when each pattern applies (for error surfacing). The brief is the handoff
  artifact — a written spec, not a Figma file.

- `Spinner` and `LoadingState` are added to `ui.jsx` alongside existing shared
  components (`Btn`, `Icon`, `Drawer`, etc.). All screens import from `ui.jsx`;
  no screen defines its own loading primitives.

### Track 3

- **Separate tables** for built-in steps: `source_builtin_map` and a
  `function_set_map` extension (nullable `builtin_step_id` FK). The pipeline
  query unions `source_function_map` and `source_builtin_map` ordered by
  `position`. This keeps the existing `source_function_map → function_registry`
  join clean.

- `builtin_type` is stored as VARCHAR (not a DuckDB enum) so new values (`filter`,
  `sql`) can be added without a schema migration. Validation of allowed values
  happens at the workflow layer.

- Built-in steps execute in the workflow layer as DuckDB SQL assembled from
  `builtin_config` — not via the worker subprocess. The assembled SQL is run
  against the DuckDB connection directly; the result is passed to the next
  pipeline step as a DataFrame.

- For source grouping, `find_source_by_pattern()` already exists in
  `workflow/create.py`. The frontend groups the `GET /sources` response by
  `pattern` value client-side; no new API endpoint needed. The human-readable
  label is derived by replacing `\d+` with `*` in the pattern string.

---

## Testing Decisions

Good tests for this work assert **external behaviour** — API responses, DB state
after a workflow call, frontend rendering of fetched data — not internal
implementation details.

### Track 1

- **`migration.py` fix** — existing `tests/test_migration.py` (13 tests) provides
  coverage; verify no regressions after the `ColumnRegistryUpdate` swap.
- **Module boundary moves** — `tests/test_api_settings.py` covers settings
  round-trips; add a test that imports `AppSettings` from `validation.settings`
  and `load_settings` from `helpers` to lock the new locations.
- **`db.py` rename / `src/` reshape** — run the full test suite; no new tests
  needed. The restructure is correct when all existing tests pass.
- **`make_quirky_file`** — add tests in `test_migration.py` and `test_ingestion.py`
  that use the fixture to cover the `TRY_CAST` pre-check and `VARCHAR` fallback
  paths.
- **PK warning badge** — test via `GET /sources/{id}` response shape: assert
  `distinct_pk_count` is present and correct before/after ingest. Frontend unit
  test for badge rendering when `row_count > distinct_pk_count`.
- **Column mismatch** — add tests in `test_ingestion.py`: assert `requires_confirmation=true`
  response when column diff detected; assert write proceeds correctly after
  `confirm_schema_diff=true`; assert no diff when schema is unchanged.
- **SQL functions** — add `test_sql_functions.py`: scan a temp `.sql` file, assert
  `function_registry` row created with correct `function_type`; assert `unknown`
  type when `-- type` missing; assert execution against instance table returns
  expected rows. Prior art: `tests/test_functions.py`.

### Track 2

- UI Polish items are verified by Claude Design review (spec compliance) and
  manual happy-path testing after implementation. No new backend tests needed.

### Track 3

- **Built-in steps** — add `test_builtins.py`: assert `source_builtin_map` row
  created on attach; assert pipeline query returns built-in steps interleaved by
  position; assert join output DataFrame matches expected shape; assert pivot
  output columns match expected aggregation columns. Prior art:
  `tests/test_attach.py`, `tests/test_run_workflow.py`.
- **Source grouping** — add tests asserting `GET /sources` returns `pattern` field;
  assert mismatch detection fires correctly; assert schema write proceeds on
  confirmation. Prior art: `tests/test_api_sources.py`.

---

## Out of Scope

- **v2 scalar persistence** — per-source scalar argument override store (CLAUDE.md
  Active Deferred Work).
- **Persistent staging tables** — transform results written to a named DuckDB
  table that survives session refresh.
- **Cross-source join UI** (beyond the built-in join step) — the Results/Summary
  layer cross-join is deferred from F2.
- **Run history persistence** — result cards written to DuckDB.
- **Results filter/search** — filter cards by source, function, date, or type.
- **Scheduled / watched runs** — re-run pipeline when a source file changes.
- **SQL TEXT built-in step** — a freeform SQL step in the Built-ins tab. Deferred;
  `builtin_type` enum is designed to accommodate it.
- **Filter built-in step** — deferred; same extensibility note.
- **Bulk pattern-matched file ingest** — auto-infer files on disk matching a
  source pattern and offer bulk confirmation.
- **Mobile / responsive layout** — desktop-only through at least v2.
- **PK uniqueness enforcement at registration** — full enforcement deferred to v2;
  only the UI warning badge ships in Track 1.

---

## Further Notes

- **Design-first gate for Track 2:** no Claude Code issue should be opened for a
  Track 2 item until a Design brief exists for it. The brief is the prerequisite,
  not an optional input.

- **Track 3 depends on Track 1 completion:** the `src/` reshape and CLI entry
  points (item 5) must be complete before v2 feature branches begin, since all
  subsequent work will be done against the reshaped package.

- **Issue ordering for Track 1:** items 1–6 are independent and can be parallelised.
  Items 7–13 are also independent of each other. Item 5 (`src/` reshape) should be
  the last Track 1 item merged, since it touches every import path and will
  conflict with in-flight branches.

- **`builtin_type` extensibility:** the `filter` and `sql` built-in types are
  intentionally not implemented in Track 3. The schema (`builtin_type VARCHAR`,
  `builtin_config JSON`) is designed to accommodate them with no migration.

- **SQL function `function_signature` convention:** stored as
  `{source_table}: pd.DataFrame -> <return_type>` to be consistent with the
  `param_name: type` signature format used for Python functions, while clearly
  indicating the SQL placeholder.
