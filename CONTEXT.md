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

## function scanning (rescan model)

Functions are registered by scanning `functions_path`, not by file upload. The registry does **not** auto-update on app startup or when files change on disk. A rescan is triggered explicitly in two ways: (1) saving a changed `functions_path` in Settings, or (2) pressing "Rescan" on the Functions screen. On rescan, the backend discovers all `.py` files in `functions_path`, inspects each function, and registers or re-registers it. Re-registration uses the function collapse rule (Principle 2): same `content_hash_id` → preserve surrogate `function_id`, overwrite mutables only.

## is_active

A mutable boolean column on `function_registry` (default `true`). Set to `false` when a rescan finds the function's `module_path` file no longer exists on disk; restored to `true` when the file reappears on a subsequent rescan. Does not contribute to `content_hash_id`. Inactive functions remain in the registry and in any existing `source_function_map` bindings — they are never auto-deleted.

## scan log

A session-only in-memory record of what changed during a rescan. Entries cover: functions added, re-registered, found missing (file gone → `is_active` flipped), and skipped with a reason (e.g. "missing return annotation", "untyped parameter `x`"). Not persisted to DuckDB. Shown in the Functions screen so the user can see the diff from the last rescan. Resets on server restart. Durable state is captured by `is_active` on `function_registry`.

## function detail drawer

The detail view for a registered function, opened from the Functions screen. Shows: signature, docstring, parameters and their types, `function_class`, `function_type`, `function_return_type`, active/inactive status, and the list of sources the function is currently attached to (joined from `source_function_map` → `source_registry`). Mirrors the drawer pattern used on the Data screen for source detail.

## function set (playlist)

A user-curated named ordered list of registered functions. Created and managed on the Functions screen. Functions execute in the order the user arranges them. A set contains only `function_id` references — no source binding and no column mappings. Column→parameter mapping (alias_map) happens at attach time per source. Stored in a `function_set` table (set_id, name) + `function_set_map` table (set_map_id, set_id, function_id, position). Built in Phase D2 (after Phase D ships function registration).

When any member function has `is_active = false`, the set card shows a warning marker ("N function(s) unavailable"). At attach time in Phase E, inactive functions in the set are skipped — they are not attached to the source. When the function's file reappears on a subsequent rescan and `is_active` is restored to `true`, the set automatically resolves (no user action needed — the function was never removed from the set).

## worker Python interpreter

The worker process uses `sys.executable` — the same Python interpreter running the app. Because the user installs pipeui into their project environment, their project's dependencies (pandas, numpy, etc.) are already available. No separate venv is created or managed by the app.

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

## five-screen app layout

The application has five nav items (post-Phase E2):
- **Data** — sources, schema, ingest
- **Functions** — function list (Functions tab) + function sets (Sets tab)
- **Builder** — assemble pipeline, bind columns, run
- **Results** — combined post-run screen: Validations tab (F1) + Transforms tab (F2); placeholder in E2
- **Settings** — appearance + app config
