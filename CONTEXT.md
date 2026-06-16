# CONTEXT.md — Domain Glossary

Terms resolved during grilling sessions. Implementation details live in CLAUDE_REFERENCE.md; design intent lives in CLAUDE.md. This file is a glossary only.

---

## src-layout reshape + CLI entry point (resolved)

The package must be restructured to `src/pipeui/` before v2 work begins. This is a prerequisite for shipping proper CLI entry points (via `pyproject.toml` `[project.scripts]`) that allow users to install pipeui into their own project environment and manage it from the command line. Two commands only:

- **`pipeui init`** — idempotent; creates `pipeui.config.json` and the DuckDB file in the user's **working directory** (project root), not inside the installed package. Safe to re-run on an existing database (no-op). Config is always managed through the Settings screen at runtime; `init` only seeds the initial files.
- **`pipeui start`** — launches the uvicorn server.

Writing config and the database to the project root (not the package directory) avoids write permission issues when pipeui is installed as a dependency. `db.py` rename and `src/` reshape are separate issues.

---

## PK uniqueness (resolved)

The app does not enforce PK uniqueness at registration or ingestion time. Duplicate PK handling is covered by the ingestion-method model (`upsert`/`append`/`skip`). A UI-only warning badge is shown in the source detail drawer when `row_count > COUNT(DISTINCT pk_column)` — no backend schema changes, no registration rejection. Full enforcement (e.g. rejecting a source whose PK is non-unique) is deferred to v2.

---

## return-type vocabulary (resolved)

The canonical vocabulary for function return types is Python type annotation casing throughout: `pd.Series`, `pd.Series[bool]`, `pd.DataFrame`. The prose terms `vector` and `matrix` are retired — any remaining references are stale and should be replaced. `function_class` uses lowercase `pd.dataframe` as a stored enum value (DuckDB VARCHAR); the Python-facing display uses `pd.DataFrame`. This was resolved during Phase D implementation; the Active Deferred Work entry in CLAUDE.md should be struck.

---

## function_return_type

The shape and type of a function's return value, stored in `function_registry`. Determines how the execution layer aggregates results across alias_map runs.

| value | meaning | function_type derived |
|---|---|---|
| `scalar` | single non-boolean value per row; results collected row-by-row | `transform` |
| `boolean` | single `bool` per row; results collected row-by-row | `validation` |
| `pd.Series` | column-shaped return (non-boolean) | `transform` |
| `pd.Series[bool]` | column-shaped boolean return; works on bool columns via alias_map | `validation` |
| `pd.DataFrame` | table-shaped return | `transform` |

## param_type

The Python annotation spelling of a function parameter, stored in the `parameter` table. Derived directly from `inspect.signature` at registration time.

| value | notes |
|---|---|
| `int` | scalar |
| `float` | scalar |
| `bool` | scalar |
| `str` | scalar unless tied to alias_map, in which case `column_backed` is derived |
| `pd.Series` | column data input |
| `pd.Series[bool]` | boolean column data input; same `function_class` granularity as `pd.Series` |
| `pd.DataFrame` | full table input |

## function_class

Derived classification (not stored per-parameter). Determined by the least-granular (most generic) parameter in the function signature, using `param_type` + alias_map presence.

| value | derived from | multi_select_eligible |
|---|---|---|
| `scalar` | all params are `int`, `float`, `bool`, or `str` not in alias_map | no |
| `column_backed` | least-granular param is a `str` tied to an alias_map row | yes |
| `pd.Series` | least-granular param is `pd.Series` or `pd.Series[bool]` | yes |
| `pd.dataframe` | least-granular param is `pd.DataFrame` | yes |

## column_backed

A derived classification for a `str` parameter that has an alias_map row mapping it to a source column. The parameter receives the column *name* as its string argument at execution time. Validated at attach time: if a `str` param has no alias_map entry, the attach fails.

## function_type

Derived from `function_return_type`. `validation` when return is `boolean` or `pd.Series[bool]`; `transform` otherwise.

---

## functions_paths

A list of folder paths in `pipeui.config.json` (alongside `db_path`). Each entry points to a directory on the user's machine containing `.py` function modules. The app does not copy or upload files — `module_path` in `function_registry` stores the user's actual file path. The Settings screen has an add/remove list UI for managing the paths. All paths are scanned together on rescan.

## SQL function

A `.sql` file registered via the same `functions_paths` scan as `.py` files. Always operates at DataFrame granularity — the full source instance table is the input, substituted via a `{source_table}` placeholder at run time. No parameter binding; SQL functions are attached to sources like any other function but have no `alias_map` rows.

Metadata is declared in a comment block at the top of the file:
```sql
-- name: clean_nulls
-- description: Remove rows where key columns are null
-- type: transform
SELECT * FROM {source_table} WHERE col_a IS NOT NULL
```

`-- type` must be `transform` or `validation`. If omitted, `function_type` is stored as `unknown`. The Functions screen shows an **Unknown** type badge on such functions; hovering the badge shows a tooltip: *"Type unknown — add `-- type: transform` or `-- type: validation` to this file's header and rescan."* The function still registers and can be attached.

Any helpers shared between `.py` and `.sql` scanning (e.g. writing to `function_registry`, building `FailedFunctionEntry`) live in a shared module rather than being duplicated per scanner.

---

## function scanning (rescan model)

Functions are registered by scanning `functions_path`, not by file upload. The registry does **not** auto-update on app startup or when files change on disk. A rescan is triggered explicitly in two ways: (1) saving a changed `functions_path` in Settings, or (2) pressing "Rescan" on the Functions screen. On rescan, the backend discovers all `.py` files in `functions_path`, inspects each function, and registers or re-registers it. Re-registration uses the function collapse rule (Principle 2): same `content_hash_id` → preserve surrogate `function_id`, overwrite mutables only.

## is_active

A mutable boolean column on `function_registry` (default `true`). Set to `false` when a rescan finds the function's `module_path` file no longer exists on disk; restored to `true` when the file reappears on a subsequent rescan. Does not contribute to `content_hash_id`. Inactive functions remain in the registry and in any existing `source_function_map` bindings — they are never auto-deleted.

## scan log

A session-only in-memory record of what changed during a rescan. Entries cover: functions added, re-registered, found missing (file gone → `is_active` flipped), and skipped with a reason (e.g. "missing return annotation", "untyped parameter `x`"). Not persisted to DuckDB. Shown in the Functions screen so the user can see the diff from the last rescan. Resets on server restart. Durable state is captured by `is_active` on `function_registry`.

## function detail drawer

The detail view for a registered function, opened from the Functions screen. Shows: signature, docstring, parameters and their types, `function_class`, `function_type`, `function_return_type`, active/inactive status, and the list of sources the function is currently attached to (joined from `source_function_map` → `source_registry`). Mirrors the drawer pattern used on the Data screen for source detail.

## multi-function set

A **function set** that contains two or more functions. Single-function sets exist in the backend (the backend always creates a set when a user drags a single function onto the pipeline canvas) but are invisible on the frontend — they are never rendered in the Sets tab on the Functions screen, the Builder RightPalette, or any other UI surface. The frontend filters them out after fetching from `GET /function-sets`. A single function dragged onto the canvas is run directly from the Functions palette; the user does not need to see or interact with its backing set.

## function set (playlist)

A user-curated named ordered list of registered functions. Created and managed on the Functions screen. Functions execute in the order the user arranges them. A set contains only `function_id` references — no source binding and no column mappings. Column→parameter mapping (alias_map) happens at attach time per source. Stored in a `function_set` table (set_id, name) + `function_set_map` table (set_map_id, set_id, function_id, position). Built in Phase D2 (after Phase D ships function registration).

When any member function has `is_active = false`, the set card shows a warning marker ("N function(s) unavailable"). At attach time in Phase E, inactive functions in the set are skipped — they are not attached to the source. When the function's file reappears on a subsequent rescan and `is_active` is restored to `true`, the set automatically resolves (no user action needed — the function was never removed from the set).

## worker Python interpreter

The worker process uses `sys.executable` — the same Python interpreter running the app. Because the user installs pipeui into their project environment, their project's dependencies (pandas, numpy, etc.) are already available. No separate venv is created or managed by the app.

---

## source group

A source in `source_registry` whose `pattern` field is non-null acts as a group anchor — it represents a named report that accepts multiple ingested files matching that pattern over time. The group name shown in the UI is a human-readable rendering of the `pattern` regex (e.g. `sales_jan_\d+` → `"sales_jan_*"`) — the raw regex is never shown directly. No separate group table; `source_id` from `source_registry` is the group identity (one source, many ingestion files).

**Column mismatch warning:** when a file being ingested has columns that don't match the source's current `source_column_map` (new columns, removed columns, or type mismatches), the user sees a confirmation popup showing the diff before ingestion proceeds. On confirmation, the normal column_registry + source_column_map write path executes — new columns get new UUID4/UUID5 rows, type changes go through copy-on-write. No extra flag or table needed; the accepted state is fully captured by what is in `source_column_map` after the confirmed write.

**Future (not in current scope):** auto-infer files on disk matching the pattern and offer bulk-ingest with a confirmation list (user removes files they don't want before committing).

---

## built-in step

A pipeline step backed by a DuckDB operation assembled from user configuration, not a Python callable. Stored in a `source_builtin_map` table separate from `source_function_map` (see pipeline step). `builtin_type` is an extensible VARCHAR enum — current values: `join`, `pivot`, `filter`; future: `sql`. `builtin_config` is a JSON blob whose shape is validated per `builtin_type` at the workflow layer. Built-ins can appear inside function sets (via `function_set_map` extended with a nullable `builtin_step_id` — a step is either a function or a built-in, never both).

Built-ins are exposed in two places in the UI:
- **Functions screen** — a **Built-ins** tab (between Functions and Sets tabs) renders each built-in type as a card using the same layout as the Functions tab. Clicking a card opens a detail drawer with description and parameter schema.
- **Builder screen** — the RightPalette has a **Built-ins** tab alongside Functions and Sets. Cards here are draggable onto the pipeline canvas.

**Join config shape:** `{ right_source_id, join_type: inner|left|right|full, on: [{left_col, right_col}], keep_columns: "all" | [col_id, ...] }`

**Pivot config shape:** `{ index_columns: [col_id], pivot_column: col_id, value_columns: [{col_id, aggregations: [sum|avg|count|min|max, ...]}] }` — multiple aggregation methods per value column, columns dragged into aggregation slots in the UI.

**Filter config shape:** `{ column: col_id, operator: eq|neq|gt|gte|lt|lte|contains|not_contains|is_null|is_not_null, value: string }` — single-condition row filter in v1; multi-condition (AND/OR) deferred.

## effective column set

The set of columns available to a pipeline step at a given position. For steps after a join, this includes the original source's columns plus any columns brought in by the join. Computed server-side at dry-run time and returned in the dry-run response as `available_columns: [{ column_id, column_name, column_type }]`. The frontend parameter mapping modal uses this list instead of the static source column list.

## join source picker

The first step of the built-in attach modal for a join step. The user selects the right-hand report to join against. All registered sources are shown (excluding the current source). For sources that have a pipeline with transform steps, a **"Use transformed output"** toggle is shown — off by default (joins against raw ingested data). When the toggle is on, the system runs that source's pipeline on-the-fly at join execution time and joins against the result.

The column picker in step 2 reflects the chosen mode: raw shows `column_registry` columns; transformed shows the post-pipeline output columns. The card and popup design for this flow goes through a Claude Design pass — the user needs enough context about the right-hand report (row count, column list, pipeline summary) to make an informed decision without the UI becoming cluttered.

## builtin_registry

A catalog table seeded at DB init time with one row per supported built-in type (`join`, `pivot`, `filter`). Mirrors `function_registry` in purpose: holds the name, display name, description, and parameter schema for each built-in type. The Functions screen Built-ins tab and the Builder RightPalette Built-ins tab both fetch from `GET /builtins` backed by this table. This is distinct from `source_builtin_map`, which records which built-in steps are attached to a specific source's pipeline.

---

## pipeline

The combination of a source + an ordered list of attached functions/sets with their column bindings. Not stored as a single entity — derived at query time from `source_function_map` + `alias_map` + `function_set_map`. A pipeline is per-source; the same function set can appear in many pipelines (one per source it's attached to).

## function attach

The act of tying a function (or all functions in a set) to a source. Creates `source_function_map` rows + `alias_map` rows in one transaction. Column→parameter bindings are per (source × function) pair. When attaching to a new source that shares `column_id` values with an existing binding, the UI auto-suggests the matching bindings. If a required parameter has no binding, the attach fails.

## alias_map auto-fill

When attaching a function to a source, the app checks whether any of the function's parameters already have alias_map bindings on a different source whose `column_id` matches a column in the new source (same `column_name + column_type` → same `column_id` via UUID5). If so, the binding is auto-suggested in the UI. The user can accept or override it. This works because `column_registry` deduplicates by content hash — shared column definitions share a single `column_id`.

## Results screen

A post-run screen showing outcomes for validation and transform functions. Ships as a nav placeholder in Phase E2. Each phase fills in its own screen independently — no shared tab shell between F1 and F2; F2 restructures the screen when it adds Transforms.

- **Validations screen** (F1) — two sub-tabs: **By Source** and **By Function** (see below). Results are ephemeral (session-only React state); export is the durable artifact.
- **Transforms screen** (F2) — post-run transformed tables ready to combine/export. In v1 the tables are session-only ephemeral DuckDB tables (not persisted to the main DB); in v2 a separate persistent user table may be introduced. Export (CSV/Excel) included.

Clicking through from a result tag on a pipeline canvas card in the Builder lands on the Validations screen, pre-scoped to that source.

## Validations screen

The F1 Results screen. Shows pass/fail outcomes for validation functions. Has two sub-tabs:

- **By Source** — analyst picks a source; sees every validation function attached to it with per-function pass/fail counts and a capped preview (≤200 rows) of the full failing rows. Per-function CSV export of all failing rows. Triggered from the Builder result tag (pre-selects the source) or manually from this screen.
- **By Function** — analyst picks a validation function; sees every source it is attached to with per-source pass/fail counts and failing rows preview. Per-source CSV export. Triggered via a dedicated endpoint `POST /validations/run?function_id={id}` that the backend fans out across all attached sources in one call.

**Granularity:** results are per-function (not per set). The set name is shown as a grouping label only. The backend collects failing rows (full row values, not just PKs) per function and returns them in the run payload alongside aggregate counts.

**Persistence:** ephemeral. Results live in React state for the session; lost on refresh. No run history is stored in DuckDB in v1.

## validation run (cross-source)

A run triggered from the Validations screen "By Function" sub-tab. Uses `POST /validations/run?function_id={id}`. The backend finds all sources the function is attached to via `source_function_map`, runs the function against each source's instance table, and returns a per-source result array in a single response. Distinct from `POST /pipelines/{source_id}/run` which is scoped to one source.

## result tag

A small status badge on each pipeline canvas card in the Builder showing the outcome of the last run for that set: `success`, `issues` (validation failures present), or `error` (worker crash or transform failure). Tapping/clicking navigates to the Results screen.

## source drawer collapsibility

The source detail drawer (Data screen) has three collapsible sections. Default states:
- **Details** (primary key, ingestion method, dates) — expanded by default.
- **Columns** (column type rows, migration UI) — collapsed by default.
- **Data** (row preview table) — collapsed by default.

All three toggle on header click, using the same `›` / `∨` indicator pattern already in place for Columns.

## module group collapsibility

On the Functions screen, functions are grouped by their `module_path` file. Each group renders a file header (filename, full path, function count) followed by its function cards. Groups are expanded by default and can be collapsed by clicking the header — only the header remains visible when collapsed. All groups share independent collapsed state.

## Settings nav placement

The Settings nav item is pinned to the bottom of the left sidebar via `marginTop: "auto"` on its nav button. All other nav items (Data, Functions, Builder, Results) flow from the top. No divider separates Settings from the rest — the visual gap from `marginTop: "auto"` is sufficient.

## Builder palette click-to-drawer

The Builder RightPalette function and set cards are draggable onto the pipeline canvas. Clicking a card (without dragging) opens a read-only detail drawer:
- **Function card** — opens the same `FunctionDrawer` used on the Functions screen (fetches `GET /functions/{id}`).
- **Set card** — opens a `SetDetailDrawer`: set name, description, ordered member functions with type badges and active/inactive status. Fetches `GET /function-sets/{set_id}`. Read-only — no edit/delete/run controls.

Drag affordance is preserved alongside click.

## source_scalar_map

A table that persists scalar parameter overrides per source. Shape: `source_scalar_map (scalar_map_id UUID4, source_id FK, param_id FK, value VARCHAR)`. One row per (source × param) pair; upserted when the user sets or changes a value. `value` is stored as VARCHAR and cast to the param's declared `param_type` at run time. If no row exists for a given (source, param) pair, the Python default for that parameter is used. Brings the v2 scalar store forward into v1.

## pipeline step editability

All pipeline steps on the Builder canvas are editable after attach. Each `StepCard` has an edit icon that re-opens the parameter mapping modal (`PendingStepCard`) pre-populated with the step's current column bindings and scalar values. Saving an edit calls `PATCH /pipelines/{source_id}/steps/{source_function_map_id}` extended to accept updated `bindings` and `scalar_values`. The scalar values are written to `source_scalar_map`; column bindings update `alias_map`.

## scalar param visibility in attach modal

Scalar parameters (`int`, `float`, `bool`) are included in the dry-run response (`GET /pipelines/{source_id}/steps?dry_run=true`) via a path separate from `_SUGGEST_TYPES`, which governs column-binding suggestions only and is not modified. The attach modal renders a free-text input for each scalar param with a type hint label (e.g. `int`) and a note that the entered value must match the expected type. If left blank, the Python default is used.

## numeric formatting cleanup (resolved)

When a user converts a column to a numeric type (`INTEGER`, `BIGINT`, `DOUBLE`), the
migration cleans common formatting noise before the `TRY_CAST` so US/UK-formatted
financial values survive instead of being nullified. US/UK number format is assumed —
comma = thousands separator, period = decimal; European decimal-comma input is **not**
supported in v1. The cleaning rules (`workflow/migration.py::numeric_cast_expr`):

- **strip** whitespace, thousands-separator commas, and currency symbols `$ € £ ¥` —
  `"$1,234.50"` → `1234.5`, `"1 234"` → `1234`;
- **percent** divides by 100 — `"50%"` → `0.5`, `"12.5%"` → `0.125`;
- **accounting parentheses** become a negative — `"(1,234)"` → `-1234`.

This is **migration-path only** — autodetection is unchanged, so a formatted column is
still inferred as `VARCHAR` on source creation and the user converts it explicitly when
ready. The same cleaning expression is used at all three cast sites (uncastable
pre-check, nullify collection, recreate-and-copy) so they agree on what is castable.
Genuinely non-numeric text (e.g. `"abc"`, a lone `"$"`) is still uncastable and follows
the existing `on_uncastable` (`abort` / `nullify`) path. Numeric arithmetic for the
percent/paren transforms runs through a `DOUBLE` intermediate, so a `BIGINT` value above
2^53 would lose precision — out of range for this domain. Aligns with Principle 6
(migrate over reject).

## five-screen app layout

The application has five nav items (post-Phase E2):
- **Data** — sources, schema, ingest
- **Functions** — function list (Functions tab) + function sets (Sets tab)
- **Builder** — assemble pipeline, bind columns, run
- **Results** — combined post-run screen: Validations tab (F1) + Transforms tab (F2); placeholder in E2
- **Settings** — appearance + app config
