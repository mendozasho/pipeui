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

A user-curated named group of registered functions. Created and managed on the Functions screen, where the user has full visibility of signatures, types, and docs. A set is a bag of `function_id` references with no source binding — compatibility is validated at attach time (Phase E). Stored in a `function_set` table (set_id, name) + `function_set_map` table (set_id, function_id). Built in Phase D2 (after Phase D ships function registration).

When any member function has `is_active = false`, the set card shows a warning marker ("N function(s) unavailable"). At attach time in Phase E, inactive functions in the set are skipped — they are not attached to the source. When the function's file reappears on a subsequent rescan and `is_active` is restored to `true`, the set automatically resolves (no user action needed — the function was never removed from the set).

## worker Python interpreter

The worker process uses `sys.executable` — the same Python interpreter running the app. Because the user installs pipeui into their project environment, their project's dependencies (pandas, numpy, etc.) are already available. No separate venv is created or managed by the app.
