# Addendum — Executor scalar-param resolution + broadcast (#258)
Issue: #258   Branch: feature/runner-execution-scalar

to-code addendum (NOT in frozen slice ledger). Critical correctness gap found in manual
testing of PR #253: a function with a scalar param could not run — the executor never read
`source_scalar_map`. Root cause was a missing/unverified acceptance criterion (the PRD
assumed `source_scalar_map → executor` was wired; it never was, even on beta). Built directly
(staged, red-green) at the user's direction; acceptance recorded here to close the gap.

## Behaviors (red-green)
1. Executor reads `source_scalar_map` and passes each scalar param's value to the worker —
   `tests/test_run_workflow.py::test_scalar_param_value_passed_from_source_scalar_map`
   (red: TypeError missing 'threshold' → failed; green: ok + real counts).
2. Scalar broadcasts into EVERY bundle (bundles still work) —
   `::test_scalar_param_broadcasts_into_every_bundle` (3 cols → 3 bundles, all ok).
3. Falls back to Python default when no persisted value —
   `::test_scalar_param_falls_back_to_python_default`.
4. Required param (no value, no default) → clean structured error, not TypeError —
   `::test_required_scalar_param_with_no_value_or_default_fails_cleanly`.
5. Defaults captured at registration —
   `test_functions.py::TestScanFunctions::test_scan_captures_param_defaults`;
   schema + migration `test_schema.py::test_parameter_table_has_default_columns` /
   `::test_run_migrations_adds_parameter_default_columns`.
6. Worker passes extra scalar kwargs — `test_worker.py::test_call_function_passes_extra_scalar_kwargs`.
7. Single-runner invariant — `test_run_workflow.py::test_run_entry_points_delegate_to_single_runner`
   (run_validation_across_sources funnels through run_pipeline).
8. Failed run surfaces its error, not 0/0 (frontend) —
   `screen-results.test.jsx :: "a failed run surfaces its error instead of 0/0 counts (#258)"`.

## Actual
- Data: `parameter.has_default` + `default_value` (DDL + `_REGISTRY_SCHEMA_MIGRATIONS`).
- Registration: `functions._inspect_function` captures defaults; insert writes them.
- Worker: `call_function(..., extra_kwargs=...)` — JSON block in the payload, merged into the call;
  wrapper accepts/forwards `**__extra` for the element-wise scalar-run path.
- Executor: `resolve_scalar_kwargs` (pure) + `RequiredParamError`; wired into `_execute_validation_step`
  and `_execute_transform_step` (df / no-bound / bundle paths), broadcast to every bundle.
- Frontend: `cardFailureError` + `SummaryLine` surfaces a failed run's error.
- Single runner confirmed: all entry points delegate to `run_pipeline`; `call_function` only called in run.py.

## Fixture fan-out (Phase-3 feedback)
Adding 2 columns to `parameter` broke 22 positional `INSERT INTO parameter VALUES(?,?,?,?,?)` across
8 test files → converted to explicit-column inserts (same pattern as slice 2's alias_map).

## Verification
- Full Python suite: 437 passed, 1 pre-existing macOS setrlimit failure. Full frontend: 80 passed.
- Live: `is_above_threshold` with `threshold` persisted runs and returns real pass/fail counts
  (verified against the running app at integration).

## Follow-ups
- Proactive frontend pre-submit block on required params not built (user accepted raise-and-surface).
- `has_default` available in `parameter` for a future proactive block.
- Tests promoted (stable execution behavior).
