# Addendum — Worker compiles user source with future-annotations (#268)
Branch: feature/runner-execution-anno
Found testing customers (#266 follow-up): a function whose file has `-> pd.Series[bool]`
and no `from __future__ import annotations` crashed the worker (`type Series is not
subscriptable`) at module exec, so every function in that file failed. Exposed once #266
made mixed-set validations (within_range, is_positive) actually run.

## Fix (red-green)
- Worker compiles user source with `__future__.annotations.compiler_flag` (PEP 563) so
  annotations are strings, never evaluated. No source manipulation.
- Test `test_worker.py::test_call_function_tolerates_subscripted_generic_annotation`
  (red: TypeError Series not subscriptable; green: runs, returns [T,F,T]). 446 py green.
