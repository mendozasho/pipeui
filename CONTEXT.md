# CONTEXT.md — Domain Glossary

Terms resolved during grilling sessions. Implementation details live in CLAUDE_REFERENCE.md; design intent lives in CLAUDE.md. This file is a glossary only.

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

## Staging screen

A per-session screen where post-run transformed tables are stored after a pipeline execution. In v1 the tables are session-only (in-memory or ephemeral DuckDB tables — not persisted to the main DB). The Staging screen is where sources that have been run through transformations and validations are held before being combined with other reports or exported to stakeholders. In v2 a separate persistent user table may be introduced. Ships as a nav placeholder in Phase E2; full implementation is Phase F2.

## Validations screen

A screen that shows pass/fail summaries generated by validation functions (those with `function_type = validation`) after a pipeline run. Each entry shows which source, which function, and which rows passed/failed. Exportable. Ships as a nav placeholder in Phase E2; full implementation is Phase F1.

## six-screen app layout

The application has six nav items (post-Phase D2):
- **Data** — sources, schema, ingest
- **Functions** — function list (Functions tab) + function sets (Sets tab)
- **Builder** — assemble pipeline, bind columns, run
- **Validations** — pass/fail results from validation functions (placeholder in E2, full in F1)
- **Staging** — post-run transformed tables ready to combine/export (placeholder in E2, full in F2)
- **Settings** — appearance + app config
