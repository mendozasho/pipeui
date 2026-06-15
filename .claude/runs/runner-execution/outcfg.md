# Addendum — Per-function transform output config + fn_name_column auto-label (#264)
Branch: feature/runner-execution-outcfg

## Done (this PR — backend)
- Issue 1 (auto-label): append with no name -> normalize_label(f"{fn_name}_{bound_col}") (e.g. uppercase_email), distinct + readable; collision suffix only as last resort. pd.DataFrame/no-bound -> fn_name.
- Issue 2 (per-function model): new function_output_config(sfm_id, function_id, output_mode, append_name); _fetch_steps resolves output_mode/append_name/output_targets PER FUNCTION (legacy step-level fallback); _execute_transform_step uses per-function config; attach writes a per-function row; patch syncs function_output_config.output_mode (avoids edit-desync since runner reads it first).
- Tests: auto-label single + multicol (uppercase_email), per-function config override, schema table; updated 3 old tests that asserted the old <fn> name. 443 py green.

## Remaining (tracked on #264 — UI)
- Mapping modal: expose per-function output_mode + append-name inputs for a MULTI-function set (currently one per step; single-function sets are correct). Needs API per-function payload + edit round-trip (Principle 7).
