# Addendum — Function-set steps routed per-function, not by dominant type (#266)
Branch: feature/runner-execution-setiter

Bug: a function set was routed to ONE executor by its dominant type, so a validation
inside a transform-containing set never ran (and a validations run excluded the set).
Sets must be transparent containers — every function processed by its own type.

## Fix (red-green)
- New `_step_has(step, type)`; run_pipeline filters active_steps by CONTAINED type and
  runs `_execute_transform_step` and/or `_execute_validation_step` per step as applicable
  (transforms chain working_df + staging, then validations read original_df).
- Tests: `test_mixed_set_validations_run_not_dropped` (red: validation dropped on a
  validations run), `test_mixed_set_all_run_processes_every_function` (red: `all` ran only
  the transform). Green after the fix. 445 py pass (1 pre-existing setrlimit).
