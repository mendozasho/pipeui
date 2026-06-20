"""Validation-result interpretation (L3 — runner execution mechanics).

Normalizes a validation worker's raw output (a boolean ``pd.Series``/``pd.DataFrame``
vector, a bare ``bool``, or a ``FailedFunctionEntry``) into pass/fail counts plus the
list of failing rows, then hands those to the ``emit`` callback the executor supplies.

Split out of ``executors.py`` (#45): one responsibility, imported **down** by the
validation execution mechanics. It owns the *shape interpretation* of a result; the
caller owns *what to do* with it (the ``emit`` closure builds the result entry).
"""
from __future__ import annotations

import pandas as pd

from pipeui.backend.data.base.fails import FailedFunctionEntry


def interpret_validation_result(result, original, *, fn_id, fn_name, bound_col, emit):
    """Normalize a validation worker result to pass/fail counts + failing rows, then emit.

    Accepts a pd.Series/pd.DataFrame boolean vector (the scalar-run-normalized output),
    a bare bool, or a FailedFunctionEntry. Returns the emit-dict for one RunResult.
    """
    if isinstance(result, FailedFunctionEntry):
        error_msg = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
        return emit(
            fn_id=fn_id, fn_name=fn_name, bound_col=bound_col,
            status="failed", rows_passed=None, rows_failed=None,
            failing_rows=[], error=error_msg,
        )

    failing_mask = None
    if isinstance(result, pd.Series):
        bool_series = result.reset_index(drop=True).astype(bool)
        passed = int(bool_series.sum())
        failed = len(bool_series) - passed
        failing_mask = ~bool_series
    elif isinstance(result, pd.DataFrame):
        bool_col = result.iloc[:, 0].astype(bool).reset_index(drop=True)
        passed = int(bool_col.sum())
        failed = len(bool_col) - passed
        failing_mask = ~bool_col
    elif isinstance(result, bool):
        passed = 1 if result else 0
        failed = 0 if result else 1
        failing_mask = None  # scalar: no individual rows to surface
    else:
        passed = 0
        failed = 0
        failing_mask = None

    # Collect failing rows (full row dicts, uncapped). DuckDB's .df() converts NULL
    # to float NaN; replace with None for JSON safety.
    if failing_mask is not None and failed > 0:
        original_reset = original.reset_index(drop=True)
        raw_rows = original_reset[failing_mask].to_dict(orient="records")
        failing_rows = [
            {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()}
            for row in raw_rows
        ]
    else:
        failing_rows = []

    return emit(
        fn_id=fn_id, fn_name=fn_name, bound_col=bound_col,
        status="ok", rows_passed=passed, rows_failed=failed,
        failing_rows=failing_rows, error=None,
    )
