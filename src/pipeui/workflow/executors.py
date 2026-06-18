"""Step execution (L3) — the ``StepExecutor`` registry + per-type executors
(function, set-adapter, built-in) **and** the per-function execution mechanics that
turn a step's functions into ``RunResult``s.

A ``function step`` and a ``built-in step`` are resolved and run the same way: the
runner builds a ``StepContext``, looks the executor up in ``STEP_EXECUTORS`` by
``ctx.step_type``, and calls ``execute(...)``. This replaces the inline ``if/elif``
type branching that used to live in ``run_pipeline``'s loop.

This module owns the execution mechanics (``_execute_transform_step``,
``_execute_validation_step``, scalar resolution, the validation-result interpreter,
the ``RunResult`` builders, SQL-function execution) — they were previously in
``run.py`` and imported back here inside the executors, which formed the
``run ⇄ executors`` cycle. They now live here (L3) and ``run.py`` (L4) imports nothing
back; ``run.py`` re-exports the few symbols external tests still import by path.

Behavior-preserving refactor (CONTEXT.md → Runner module responsibilities): the
moved code is byte-for-byte the inline logic; only its home changed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

import duckdb
import pandas as pd

from pipeui.results import RunResult, StepResultEntry, ValidationRunResult, normalize_label
from pipeui.sql_user_table import instance_table_name
from pipeui.validation.fails import FailedFunctionEntry
from pipeui.workflow.builtins import execute_builtin_step
from pipeui.workflow.bundles import pair_bundles
from pipeui.workflow.staging import write_staging_table
from pipeui.workflow.step import (
    BUILTIN,
    FUNCTION,
    SET,
    BuiltinStepContext,
    FunctionSpec,
    FunctionStepContext,
    StepContext,
)
from pipeui.workflow.worker import call_function


# ---------------------------------------------------------------------------
# Scalar parameter resolution (#258)
# ---------------------------------------------------------------------------

class RequiredParamError(Exception):
    """A scalar param has no persisted value and no Python default — the function
    cannot run. Surfaced as a failed RunResult the frontend can pick up."""

    def __init__(self, param_name: str):
        self.param_name = param_name
        super().__init__(
            f"parameter '{param_name}' is required but no value or default was provided"
        )


def _coerce_scalar(value: str, param_type: str):
    """Coerce a source_scalar_map / default_value VARCHAR to the param's Python type."""
    if param_type == "int":
        return int(value)
    if param_type == "float":
        return float(value)
    if param_type == "bool":
        return str(value).strip().lower() in ("true", "1", "yes")
    return value  # str


def resolve_scalar_kwargs(params: list[dict]) -> dict:
    """Resolve non-column scalar params to ``{param_name: value}`` for broadcast into
    every argument bundle (#258).

    A scalar param is one whose type is int/float/str/bool and which has NO column
    bindings (a column-bound param is the bundle column, passed separately; pd.Series /
    pd.DataFrame are handled elsewhere). Its value is the persisted source_scalar_map
    value, else the captured Python default. A param with neither raises
    RequiredParamError — the function genuinely cannot run.
    """
    extra: dict = {}
    for p in params:
        if p.get("bindings"):
            continue  # column-bound — passed as the bundle column, not a scalar
        if p["param_type"] not in ("int", "float", "str", "bool"):
            continue  # pd.Series / pd.DataFrame
        raw = p.get("scalar_value")
        if raw is None and p.get("has_default"):
            raw = p.get("default_value")
        if raw is None:
            raise RequiredParamError(p["param_name"])
        extra[p["param_name"]] = _coerce_scalar(raw, p["param_type"])
    return extra


# ---------------------------------------------------------------------------
# SQL function execution
# ---------------------------------------------------------------------------

def _execute_sql_function(
    conn: duckdb.DuckDBPyConnection,
    module_path: str,
    source_id: uuid.UUID,
) -> "pd.DataFrame | FailedFunctionEntry":
    """Execute a SQL function by substituting {source_table} and running on DuckDB.

    Returns a DataFrame on success or a FailedFunctionEntry on error.
    """
    try:
        sql_source = Path(module_path).read_text(encoding="utf-8")
    except OSError as exc:
        entry = FailedFunctionEntry()
        entry.add("sql_read", f"cannot read SQL file: {exc}")
        return entry

    # Strip leading comment header lines to get the actual SQL body
    body_lines = [ln for ln in sql_source.splitlines() if not ln.strip().startswith("--")]
    sql_body = "\n".join(body_lines).strip()

    if not sql_body:
        entry = FailedFunctionEntry()
        entry.add("sql_empty", "SQL file contains no query after header comments")
        return entry

    tname = instance_table_name(source_id)
    sql = sql_body.replace("{source_table}", f'"{tname}"')

    try:
        return conn.execute(sql).df()
    except Exception as exc:
        entry = FailedFunctionEntry()
        entry.add("sql_exec", str(exc))
        return entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_column_name(name: str, existing: set[str]) -> str:
    """Return a column name that does not collide with `existing`, suffixing _2, _3…

    Append mode must never clobber an existing column (the no-collision guarantee).
    """
    if name not in existing:
        return name
    i = 2
    while f"{name}_{i}" in existing:
        i += 1
    return f"{name}_{i}"


def _normalize_to_series(result, n_rows: int) -> pd.Series:
    """Normalize a worker result (pd.Series or scalar) to a row-aligned Series.

    A scalar is broadcast across all rows; a Series is index-reset for alignment.
    """
    if isinstance(result, pd.Series):
        return result.reset_index(drop=True)
    return pd.Series([result] * n_rows)


def step_has(step: "FunctionStepContext", function_type: str) -> bool:
    """#266: a function set is a transparent container — a step 'has' a type when ANY
    of its functions is of that type. Routing reads this, not a single dominant type,
    so a mixed set runs both its transforms and its validations.

    Built-in steps have no ``functions`` member; treat them as holding neither type."""
    return any(f.function_type == function_type for f in getattr(step, "functions", ()))


# ---------------------------------------------------------------------------
# Transform step execution
# ---------------------------------------------------------------------------

def _execute_transform_step(
    working: pd.DataFrame,
    step: "FunctionStepContext",
    conn: duckdb.DuckDBPyConnection | None = None,
    source_id: uuid.UUID | None = None,
) -> tuple[pd.DataFrame, str | None, list[RunResult]]:
    """Execute all functions in a transform step against the working table.

    Returns (new_working_table, error_message_or_None, run_results).

    A column-bound transform expands into argument bundles (§12 / ADR-0001): the
    function runs once per bundle's column, and `output_mode` decides write-back:
      - **append** → each bundle adds a NEW column, named by the user-provided append
        name (collision-suffixed) or a cleaned auto-label of the varying column.
      - **replace** → bundle i overwrites the i-th output-target column
        (the function's ``output_targets``, in position order); with no explicit
        targets a single-varying step defaults to its input column.
    A `pd.DataFrame` transform runs once over the whole table regardless of
    output_mode (no bundle expansion). Each run yields one RunResult dict.
    On error the original working table is returned unchanged.
    conn and source_id are required for SQL function execution.
    """
    current = working
    run_results: list[RunResult] = []
    # #264: output config (output_mode / append_name / output_targets) is per-FUNCTION,
    # resolved inside the loop from each FunctionSpec; the step-level value is the legacy fallback.
    step_output_mode = step.output_mode

    def _emit(*, fn_name, bound_col, status, error):
        bundle_key = bound_col or ""
        label_seed = bound_col if bound_col else fn_name
        rr = RunResult(
            function_name=fn_name,
            function_type="transform",
            source_id=source_id if source_id is not None else uuid.UUID(int=0),
            bundle_key=bundle_key,
            label=normalize_label(label_seed),
            status=status,
            error=error,
        )
        run_results.append(rr)

    for fn in step.functions:
        if fn.function_type != "transform":
            continue

        module_path = fn.module_path
        fn_name = fn.function_name
        fn_class = fn.function_class
        # #264: this function's own output config (per-function, legacy step fallback).
        output_mode = fn.output_mode if fn.output_mode is not None else step_output_mode
        append_name = fn.append_name
        output_targets = list(fn.output_targets) or []

        # SQL functions: execute directly on DuckDB connection (whole-table, one run).
        if module_path and module_path.endswith(".sql"):
            if conn is None or source_id is None:
                return working, "SQL function execution requires conn and source_id", run_results
            result = _execute_sql_function(conn, module_path, source_id)
            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "SQL execution failed"
                return working, errors, run_results
            current = result
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            return working, f"cannot read module: {exc}", run_results

        # #258: resolve scalar params once; broadcast into every call/bundle. A
        # required param with no value/default fails this function cleanly (the step
        # continues so other functions still run).
        params = list(fn.params)
        try:
            extra_kwargs = resolve_scalar_kwargs(params)
        except RequiredParamError as exc:
            _emit(fn_name=fn_name, bound_col=None, status="failed", error=str(exc))
            continue

        # pd.DataFrame transform: whole table in ONE run, regardless of output_mode.
        if fn_class == "pd.dataframe":
            kwarg_name = "df"
            for p in params:
                if p["param_type"] == "pd.DataFrame":
                    kwarg_name = p["param_name"]
                    break
            result = call_function(fn_source, fn_name, kwarg_name, current, extra_kwargs=extra_kwargs)
            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
                return working, errors, run_results
            if isinstance(result, pd.DataFrame):
                current = result
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        # Column-bound transform: resolve the bound param + its ordered columns.
        bound_param = None
        kwarg_name = params[0]["param_name"] if params else "data"
        for p in params:
            if p["param_type"] == "pd.Series":
                if not p["bindings"]:
                    return working, f"param '{p['param_name']}' is unbound — attach a column binding first", run_results
                bound_param = p
                kwarg_name = p["param_name"]
                break
            elif p["param_type"] in ("str", "int", "float", "bool") and p["bindings"]:
                bound_param = p
                kwarg_name = p["param_name"]
                break

        if bound_param is None:
            # No bound column — pass the full working table once (scalar param defaults).
            result = call_function(fn_source, fn_name, kwarg_name, current, extra_kwargs=extra_kwargs)
            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
                return working, errors, run_results
            if isinstance(result, pd.DataFrame):
                current = result
            else:
                current = current.copy()
                series = _normalize_to_series(result, len(current))
                new_col = _unique_column_name(
                    append_name or normalize_label(fn_name), set(current.columns)
                )
                current[new_col] = series.values
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        # Pair the bound columns into argument bundles (one varying param → N bundles,
        # in user-placed/position order). N=1 is the single-column special case.
        bundles = pair_bundles([
            {"param_id": bound_param["param_id"], "columns": list(bound_param["bindings"])}
        ])
        is_scalar_shape = bound_param["param_type"] in ("str", "int", "float", "bool")

        # Bundles read their INPUT columns from the table as it was at the start of
        # this function's run — not from the progressively-mutated `current`. A
        # replace target can overlap another bundle's input column, so reading from
        # `current` would feed an already-overwritten value into a later bundle.
        fn_input = current.copy()

        for i, bundle in enumerate(bundles):
            bound_col = bundle.columns[bound_param["param_id"]]
            if bound_col not in fn_input.columns:
                return (
                    working,
                    f"bound column '{bound_col}' not found in source data — detach and re-attach the function to refresh the binding",
                    run_results,
                )

            column_series = fn_input[bound_col]
            if is_scalar_shape:
                # #258: forward the broadcast scalar kwargs through the element-wise wrapper.
                wrapper = (
                    "import pandas as _pd\n"
                    "def __wrapper__(series, **__extra):\n"
                    f"    return series.apply(lambda v: {fn_name}(**{{'{kwarg_name}': None if _pd.isna(v) else v}}, **__extra))\n"
                )
                result = call_function(wrapper + "\n" + fn_source, "__wrapper__", "series", column_series, extra_kwargs=extra_kwargs)
            else:
                result = call_function(fn_source, fn_name, kwarg_name, column_series, extra_kwargs=extra_kwargs)

            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
                return working, errors, run_results

            current = current.copy()
            series = _normalize_to_series(result, len(current))

            if output_mode == "replace":
                # bundle i -> target i (output_targets in position order); with no
                # explicit target, default to overwriting the input varying column.
                target_col = output_targets[i] if i < len(output_targets) else bound_col
                if target_col in current.columns:
                    current[target_col] = series.values
                else:
                    current[target_col] = series.values
            else:
                # append: a new column per bundle, never clobbering an existing one.
                # #264: default auto-label = function name + the column it ran on
                # (e.g. uppercase_email), so N appends are self-describing and distinct;
                # a user-provided append name overrides it. Collision suffix is a last
                # resort only (same fn+col twice).
                base_name = append_name or normalize_label(f"{fn_name}_{bound_col}")
                new_col = _unique_column_name(base_name, set(current.columns))
                current[new_col] = series.values

            _emit(fn_name=fn_name, bound_col=bound_col, status="ok", error=None)

    return current, None, run_results


# ---------------------------------------------------------------------------
# RunResult builders
# ---------------------------------------------------------------------------

def _validation_runresult(
    *,
    fn_name: str,
    source_id: uuid.UUID | None,
    bound_col: str | None,
    status: str,
    rows_passed: int | None,
    rows_failed: int | None,
    failing_rows: list[dict],
    error: str | None,
) -> ValidationRunResult:
    """Build a ValidationRunResult for one validation function run (one N=1 bundle).

    The argument bundle for the single-column path is the bound column name (or "")
    so the UUID5 identity is stable per (function, bound column, source). The label is
    the normalized bound column name, falling back to the function name.
    """
    bundle_key = bound_col or ""
    label_seed = bound_col if bound_col else fn_name
    return ValidationRunResult(
        function_name=fn_name,
        function_type="validation",
        source_id=source_id if source_id is not None else uuid.UUID(int=0),
        bundle_key=bundle_key,
        label=normalize_label(label_seed),
        status=status,
        error=error,
        rows_passed=rows_passed,
        rows_failed=rows_failed,
        failing_rows=failing_rows,
    )


def _step_transform_function(step: "FunctionStepContext") -> "FunctionSpec | None":
    """Return the first transform function in a step (the N=1 step's function)."""
    for fn in getattr(step, "functions", ()):
        if fn.function_type == "transform":
            return fn
    return None


def _first_bound_column(fn: "FunctionSpec | None") -> str | None:
    """Return the first bound column across a function's params (N=1 bundle key)."""
    if not fn:
        return None
    for p in fn.params:
        if p.get("bindings"):
            return p["bindings"][0]
    return None


def _transform_runresult(
    step: "FunctionStepContext",
    source_id: uuid.UUID | None,
    *,
    status: str,
    error: str | None,
) -> RunResult:
    """Build the RunResult for a transform step (one N=1 argument bundle).

    The bundle key is the first bound column (or the function name when the step
    binds no column, e.g. a pd.DataFrame transform). The label is normalized.
    """
    fn = _step_transform_function(step)
    fn_name = fn.function_name if fn else (step.set_name or "transform")
    bound_col = _first_bound_column(fn)
    bundle_key = bound_col or ""
    label_seed = bound_col if bound_col else fn_name
    return RunResult(
        function_name=fn_name,
        function_type="transform",
        source_id=source_id if source_id is not None else uuid.UUID(int=0),
        bundle_key=bundle_key,
        label=normalize_label(label_seed),
        status=status,
        error=error,
    )


def _builtin_result(
    step: "BuiltinStepContext",
    source_id: uuid.UUID | None,
    *,
    status: str,
    error: str | None,
    rows_affected: int | None,
    consumed_result_id: str | None = None,
) -> StepResultEntry:
    """Build a step-results entry for a built-in step (join/pivot/filter).

    Reuses the transform RunResult shape (function_type='transform') so the Results
    screen renders it like any other transform step; the built-in type is the label.

    ``consumed_result_id`` is the resolved transformed-output ``result_id`` a join
    consumed (lineage — PRD User Story 7); None for raw joins and for pivot/filter.
    """
    btype = step.builtin_type
    rr = RunResult(
        function_name=btype,
        function_type="transform",
        source_id=source_id if source_id is not None else uuid.UUID(int=0),
        bundle_key=step.step_id,
        label=normalize_label(btype),
        status=status,
        error=error,
    )
    return StepResultEntry(run_result=rr, routing={
        "source_function_map_id": None,
        "step_id": step.step_id,
        "step_type": "builtin",
        "builtin_type": btype,
        "set_name": btype,
        "rows_affected": rows_affected,
        "rows_passed": None,
        "rows_failed": None,
        "consumed_result_id": consumed_result_id,
    })


# ---------------------------------------------------------------------------
# Validation step execution
# ---------------------------------------------------------------------------

def _execute_validation_step(
    original: pd.DataFrame,
    step: "FunctionStepContext",
    conn: duckdb.DuckDBPyConnection | None = None,
    source_id: uuid.UUID | None = None,
) -> list[StepResultEntry]:
    """Execute all validation functions in a step against the original table.

    Each validation function produces one ValidationRunResult (the N=1 argument
    bundle). The returned dicts preserve the legacy wire keys (function_id,
    function_name, set_name, set_id, status, rows_passed, rows_failed, pass_rate,
    failing_rows, error) and additively carry the RunResult identity (result_id)
    and normalized label via ValidationRunResult.to_dict().
    conn and source_id are required for SQL function execution.
    """
    results = []
    set_name = step.set_name
    set_id = step.set_id

    def _emit(*, fn_id, fn_name, bound_col, status, rows_passed, rows_failed,
              failing_rows, error):
        rr = _validation_runresult(
            fn_name=fn_name, source_id=source_id, bound_col=bound_col,
            status=status, rows_passed=rows_passed, rows_failed=rows_failed,
            failing_rows=failing_rows, error=error,
        )
        # RunResult is the source of truth for type/status/counts/identity/label;
        # the routing dict carries the wire keys RunResult does not hold.
        return StepResultEntry(run_result=rr, routing={
            "function_id": fn_id,
            "function_name": fn_name,
            "set_name": set_name,
            "set_id": set_id,
        })

    for fn in step.functions:
        if fn.function_type != "validation":
            continue

        fn_id = fn.function_id
        fn_name = fn.function_name
        fn_class = fn.function_class
        module_path = fn.module_path

        # SQL functions: execute directly on DuckDB connection (no column expansion).
        if module_path and module_path.endswith(".sql"):
            if conn is None or source_id is None:
                results.append(_emit(
                    fn_id=fn_id, fn_name=fn_name, bound_col=None,
                    status="failed", rows_passed=None, rows_failed=None,
                    failing_rows=[],
                    error="SQL function execution requires conn and source_id",
                ))
                continue
            sql_result = _execute_sql_function(conn, module_path, source_id)
            results.append(_interpret_validation_result(
                sql_result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[], error=f"cannot read module: {exc}",
            ))
            continue

        params = list(fn.params)

        # #258: resolve scalar params once and broadcast them into every call/bundle.
        # A required param with no value and no default fails the function cleanly.
        try:
            extra_kwargs = resolve_scalar_kwargs(params)
        except RequiredParamError as exc:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[], error=str(exc),
            ))
            continue

        # pd.DataFrame functions: the full table is passed once (no column expansion).
        if fn_class == "pd.dataframe":
            kwarg_name = "df"
            for p in params:
                if p["param_type"] == "pd.DataFrame":
                    kwarg_name = p["param_name"]
                    break
            df_result = call_function(fn_source, fn_name, kwarg_name, original, extra_kwargs=extra_kwargs)
            results.append(_interpret_validation_result(
                df_result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        # Column-bound function: resolve the column-eligible param and its bindings.
        # An eligible param bound to N columns expands into N argument bundles
        # (multi_select_eligible); the function runs once per bundle's column,
        # producing one RunResult per bundle (§12 / ADR-0001). N=1 is the single-
        # column path. A scalar-shaped param (str/int/float/bool) still runs per
        # record (the scalar run) for each of its bound columns.
        kwarg_name = params[0]["param_name"] if params else "data"
        bound_param = None
        unbound_series_param = None

        for p in params:
            if p["param_type"] == "pd.Series":
                if p["bindings"]:
                    kwarg_name = p["param_name"]
                    bound_param = p
                else:
                    unbound_series_param = p
                break
            elif p["param_type"] in ("str", "int", "float", "bool") and p["bindings"]:
                kwarg_name = p["param_name"]
                bound_param = p
                break

        if unbound_series_param is not None:
            # pd.Series with no binding — hard fail.
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[],
                error=f"param '{unbound_series_param['param_name']}' is unbound — attach a column binding first",
            ))
            continue

        if bound_param is None:
            # No bound column param — pass the full original table once.
            scalar_result = call_function(fn_source, fn_name, kwarg_name, original, extra_kwargs=extra_kwargs)
            results.append(_interpret_validation_result(
                scalar_result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        # Pair the bound columns into argument bundles. A single eligible param with
        # N columns yields N single-column bundles, in user-placed (position) order.
        bundles = pair_bundles([
            {"param_id": bound_param["param_id"], "columns": list(bound_param["bindings"])}
        ])
        is_scalar_shape = bound_param["param_type"] in ("str", "int", "float", "bool")

        for bundle in bundles:
            bound_col = bundle.columns[bound_param["param_id"]]
            results.append(_run_validation_bundle(
                fn_source=fn_source, fn_name=fn_name, fn_id=fn_id,
                kwarg_name=kwarg_name, bound_col=bound_col, is_scalar_shape=is_scalar_shape,
                original=original, emit=_emit, extra_kwargs=extra_kwargs,
            ))

    return results


def _run_validation_bundle(
    *, fn_source, fn_name, fn_id, kwarg_name, bound_col, is_scalar_shape, original, emit,
    extra_kwargs=None,
):
    """Run a validation function for one argument bundle (one bound column) and emit.

    A scalar-shaped param dispatches element-wise via the .apply() wrapper (the scalar
    run — once per record); a pd.Series param receives the column as a Series. A bound
    column missing from the loaded table hard-fails with a refresh-the-binding diagnostic.
    """
    if bound_col not in original.columns:
        return emit(
            fn_id=fn_id, fn_name=fn_name, bound_col=bound_col,
            status="failed", rows_passed=None, rows_failed=None, failing_rows=[],
            error=f"bound column '{bound_col}' not found in source data — detach and re-attach the function to refresh the binding",
        )

    column_series = original[bound_col]
    if is_scalar_shape:
        # Element-wise dispatch (scalar run). Pandas NULL is float NaN in object/string
        # columns; convert to None so user functions receive a proper null sentinel.
        # #258: __wrapper__ accepts the broadcast scalar kwargs and forwards them
        # into the element-wise call so scalar params reach the user function.
        wrapper = (
            "import pandas as _pd\n"
            "def __wrapper__(series, **__extra):\n"
            f"    return series.apply(lambda v: {fn_name}(**{{'{kwarg_name}': None if _pd.isna(v) else v}}, **__extra))\n"
        )
        result = call_function(wrapper + "\n" + fn_source, "__wrapper__", "series", column_series, extra_kwargs=extra_kwargs)
    else:
        result = call_function(fn_source, fn_name, kwarg_name, column_series, extra_kwargs=extra_kwargs)

    return _interpret_validation_result(
        result, original, fn_id=fn_id, fn_name=fn_name, bound_col=bound_col, emit=emit,
    )


def _interpret_validation_result(result, original, *, fn_id, fn_name, bound_col, emit):
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


# ---------------------------------------------------------------------------
# StepExecutor registry + per-type executors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StepExecResult:
    """The uniform result an executor returns to the runner.

    ``working`` is the (possibly reshaped) working frame after the step.
    ``entries`` is the list of ``StepResultEntry`` carriers to splice into
    ``run_pipeline``'s output; the runner serializes each via ``to_dict()`` at its
    published return, so the external ``{"steps": [...]}`` shape is unchanged.
    ``wrote_staging`` tells the runner the step staged the working frame.
    """

    working: pd.DataFrame
    entries: list[StepResultEntry] = field(default_factory=list)
    wrote_staging: bool = False


@dataclass(frozen=True)
class StepRunEnv:
    """Per-run inputs an executor needs that are not part of the step itself.

    Carries the connection, the source id, the original (pre-transform) instance
    frame validations read against, the staging timestamp, the run_type-derived
    ``want_transforms`` / ``want_validations`` gates, and the injected
    ``run_transforms`` runner threaded to a transformed join's materialize path.
    """

    conn: duckdb.DuckDBPyConnection
    source_id: uuid.UUID
    original_df: pd.DataFrame
    ts: int
    want_transforms: bool
    want_validations: bool
    run_transforms: Optional[Callable[[duckdb.DuckDBPyConnection, uuid.UUID], None]] = None


@runtime_checkable
class StepExecutor(Protocol):
    """The per-step-type execution contract resolved from the registry."""

    def execute(self, ctx: "StepContext", working: pd.DataFrame,
                env: StepRunEnv) -> StepExecResult:
        ...


class FunctionStepExecutor:
    """Executes a function step (a function set — the transparent container).

    Mirrors the pre-refactor inline function branch exactly: a step runs every
    function it holds by that function's own type — transforms (chain the working
    frame, write staging) then validations (read the original frame) — so a mixed
    set runs both (#266).
    """

    def execute(self, ctx, working, env):
        step = ctx
        sfm_id = step.source_function_map_id
        set_name = step.set_name
        entries: list[StepResultEntry] = []
        wrote_staging = False

        if env.want_transforms and step_has(step, "transform"):
            new_working, error, run_results = _execute_transform_step(
                working, step, conn=env.conn, source_id=env.source_id
            )
            if error:
                tr = _transform_runresult(step, env.source_id, status="failed", error=error)
                entries.append(StepResultEntry(run_result=tr, routing={
                    "source_function_map_id": sfm_id,
                    "set_name": set_name,
                    "rows_affected": None,
                    "rows_passed": None,
                    "rows_failed": None,
                }))
            else:
                working = new_working
                write_staging_table(env.conn, env.source_id, working, env.ts)
                wrote_staging = True
                emitted = run_results or [
                    _transform_runresult(step, env.source_id, status="ok", error=None)
                ]
                for rr in emitted:
                    entries.append(StepResultEntry(run_result=rr, routing={
                        "source_function_map_id": sfm_id,
                        "set_name": set_name,
                        "rows_affected": len(working),
                        "rows_passed": None,
                        "rows_failed": None,
                    }))

        if env.want_validations and step_has(step, "validation"):
            entries.extend(
                _execute_validation_step(
                    env.original_df, step, conn=env.conn, source_id=env.source_id
                )
            )

        return StepExecResult(working=working, entries=entries, wrote_staging=wrote_staging)


class FunctionSetExecutor:
    """The function-set adapter (slice 4 — CONTEXT.md -> Function-set adapter).

    A ``function set`` is flattened into a stream of uniform per-member executions:
    the adapter expands the set's members into one single-member sub-context each and
    dispatches it through ``STEP_EXECUTORS`` *by the member's own step type* — not
    hardcoded to function — so a set behaves exactly like its members placed
    individually, and a built-in member becomes additive later (#275) without
    re-plumbing the contract (heterogeneous-member readiness).

    Behavior preservation: a plain function member resolves to the per-member
    ``FunctionStepExecutor`` (registered under ``FUNCTION``), which runs that one
    function's transform/validation exactly as the pre-refactor whole-set executor
    ran each function. The adapter threads the working frame member-to-member and
    concatenates their entries; members share the run's timestamp, so the final
    staging table holds the fully chained frame — identical to the single-step write.
    """

    def execute(self, ctx, working, env):
        members = ctx.functions or ()
        entries: list[StepResultEntry] = []
        wrote_staging = False

        for member in members:
            member_ctx = self._member_context(ctx, member)
            executor = STEP_EXECUTORS.get(member_ctx.step_type)
            if executor is None:
                # No executor registered for this member's step type — skip it (the
                # registry is the sole dispatch authority). A future built-in member
                # registers under its step type to become runnable here.
                continue
            outcome = executor.execute(member_ctx, working, env)
            working = outcome.working
            entries.extend(outcome.entries)
            wrote_staging = wrote_staging or outcome.wrote_staging

        return StepExecResult(working=working, entries=entries, wrote_staging=wrote_staging)

    @staticmethod
    def _member_context(set_ctx: "FunctionStepContext", member: "FunctionSpec") -> "FunctionStepContext":
        """Wrap one set member as a single-member ``FunctionStepContext`` via the
        ``StepContext.from_function`` factory — never a bare constructor.

        Members are ``FunctionSpec`` (function members). A built-in member is not
        type-expressible yet (storing built-ins in a set is #275, which will widen the
        member type to a union); until then every member routes to ``FUNCTION`` ->
        ``FunctionStepExecutor`` (behavior-preserving). The sub-context reuses the
        set's step-level fields with a one-element ``functions`` tuple and the set's
        ``position``, so the per-member executor sees the exact step shape it saw when
        the whole set ran.
        """
        return StepContext.from_function({
            "source_function_map_id": set_ctx.source_function_map_id,
            "set_id": set_ctx.set_id,
            "set_name": set_ctx.set_name,
            "position": set_ctx.position,
            "output_mode": set_ctx.output_mode,
            "append_name": set_ctx.append_name,
            "output_targets": set_ctx.output_targets,
            "functions": (member,),
        })


class BuiltinStepExecutor:
    """Executes a built-in step (join/pivot/filter).

    Mirrors the pre-refactor inline built-in branch exactly: reshape the working
    frame via ``execute_builtin_step``, stage it, and record one ``_builtin_result``
    entry; on exception record a failed ``_builtin_result`` and leave the frame. The
    injected ``run_transforms`` runner is threaded so a transformed join can
    materialize a never-run right source.
    """

    def execute(self, ctx, working, env):
        step = ctx
        try:
            working, consumed_result_id = execute_builtin_step(
                env.conn, working, step, run_transforms=env.run_transforms
            )
            write_staging_table(env.conn, env.source_id, working, env.ts)
            entry = _builtin_result(
                step, env.source_id, status="ok", error=None,
                rows_affected=len(working),
                consumed_result_id=consumed_result_id,
            )
            return StepExecResult(working=working, entries=[entry], wrote_staging=True)
        except Exception as exc:  # noqa: BLE001 - preserve inline behavior (record + continue)
            entry = _builtin_result(
                step, env.source_id, status="failed", error=str(exc),
                rows_affected=None,
            )
            return StepExecResult(working=working, entries=[entry], wrote_staging=False)


# The step-type registry the runner dispatches through, keyed by StepContext
# step_type. A function-map row is a SET -> the function-set adapter, which flattens
# the set and re-dispatches each member through this same registry by the member's
# step type: a plain function member -> FUNCTION -> the per-member executor; a
# built-in step -> BUILTIN. (Slice 4 — heterogeneous-member-ready dispatch.)
STEP_EXECUTORS: dict[str, StepExecutor] = {
    SET: FunctionSetExecutor(),
    FUNCTION: FunctionStepExecutor(),
    BUILTIN: BuiltinStepExecutor(),
}
