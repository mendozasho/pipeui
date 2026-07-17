"""Step execution (L3) — the ``StepExecutor`` registry + per-type executors
(function, set-adapter, built-in) **and** the per-function execution mechanics that
turn a step's functions into ``RunResult``s.

A ``function step`` and a ``built-in step`` are resolved and run the same way: the
runner builds a ``StepContext``, looks the executor up in ``STEP_EXECUTORS`` by
``ctx.step_type``, and calls ``execute(...)``. This replaces the inline ``if/elif``
type branching that used to live in ``run_pipeline``'s loop.

This module owns the execution mechanics (``_execute_transform_step``,
``_execute_validation_step``, the ``RunResult`` builders) — they were previously in
``run.py`` and imported back here inside the executors, which formed the
``run ⇄ executors`` cycle. They now live here (L3) and ``run.py`` (L4) imports them from
here (one-way, no back-import); external tests import these symbols directly from
``executors`` by path.

Binding and execution are split across three seams the executors depend **down** on:
``contract.bind(binding)`` (data/functions — literals, shape homogeneity, bundle
pairing), ``realize`` (one BoundCall → one worker call), and ``interpret``
(validation-result normalization); ``sql_exec`` runs ``.sql`` functions. This module
keeps the ``StepExecutor`` registry, the executor classes, the run carriers, and the
per-function write-back mechanics that drive them (Phase 3 of the FunctionContract
redesign — the former per-arm binding logic collapsed onto bind()+realize()).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

import duckdb
import pandas as pd

from pipeui.backend.data.base.results import (
    BuiltinResultEntry,
    RunResult,
    StepResultEntry,
    TransformResultEntry,
    ValidationResultEntry,
    ValidationRunResult,
    normalize_label,
)
from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.data.functions.binding import (
    MixedShapeError,
    RequiredParamError,
    StepBinding,
)
from pipeui.backend.data.functions.contract import FunctionContract
from pipeui.backend.domain.functions.builtins import execute_builtin_step
from pipeui.backend.data.runner.bundles import BundleLengthError
from pipeui.backend.data.runner.staging import write_staging_table
from pipeui.backend.data.runner.steps import (
    BUILTIN,
    FUNCTION,
    SET,
    BuiltinStepContext,
    FunctionSpec,
    FunctionStepContext,
    StepContext,
)
from pipeui.backend.domain.runner.interpret import interpret_validation_result
from pipeui.backend.domain.runner.realize import realize
from pipeui.backend.domain.runner.sql_exec import execute_sql_function


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fail_msg(result: "FailedFunctionEntry", fallback: str) -> str:
    """Render a FailedFunctionEntry's reasons as a single error string for a RunResult."""
    return "; ".join(reason for _, reason in result.failures) if result.failures else fallback


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

def _unbound_series_param(contract: FunctionContract, binding: StepBinding) -> str | None:
    """Return the name of the first ``pd.Series`` param with no column binding.

    Checked across ALL params (not just the first) so a later unbound Series
    alongside bound ones is not silently dropped. An unbound Series is fatal for
    the transform step and a per-function failure for validations.
    """
    for pc in contract.params:
        if pc.type_str != "pd.Series":
            continue
        pb = binding.get(pc.name)
        if pb is None or not pb.columns:
            return pc.name
    return None


def _execute_transform_step(
    working: pd.DataFrame,
    step: "FunctionStepContext",
    conn: duckdb.DuckDBPyConnection | None = None,
    source_id: uuid.UUID | None = None,
) -> tuple[pd.DataFrame, str | None, list[RunResult]]:
    """Execute all functions in a transform step against the working table.

    Returns (new_working_table, error_message_or_None, run_results).

    Per function: ``contract.bind(binding)`` resolves the persisted binding into
    ordered ``BoundCall``s (literals -> homogeneity -> pair_bundles), and ``realize``
    executes each against the pre-step frame; this dispatcher owns only write-back:

      - ``table`` -> a DataFrame return replaces the frame (one call, no expansion).
      - ``value`` -> a DataFrame return replaces; any other return appends a column.
      - ``column`` / ``row`` -> one Series per BoundCall; **append** adds a new
        column per bundle (``append_name`` or fn+bundle-key auto-label), **replace**
        overwrites ``output_targets[i]`` (single-param default: the input column).

    On a worker/binding error the original working table is returned unchanged.
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
        # #264: this function's own output config (per-function, legacy step fallback).
        output_mode = fn.output_mode if fn.output_mode is not None else step_output_mode
        append_name = fn.append_name
        output_targets = list(fn.output_targets) or []

        # SQL functions: execute directly on DuckDB connection (whole-table, one run).
        if module_path and module_path.endswith(".sql"):
            if conn is None or source_id is None:
                return working, "SQL function execution requires conn and source_id", run_results
            result = execute_sql_function(conn, module_path, source_id)
            if isinstance(result, FailedFunctionEntry):
                return working, _fail_msg(result, "SQL execution failed"), run_results
            current = result
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            return working, f"cannot read module: {exc}", run_results

        contract, binding = fn.contract_binding()

        # bind(): literals (a required param with no value/default fails this function
        # cleanly, the step continues) -> shape homogeneity -> bundle pairing (a config
        # error fails the function cleanly too).
        try:
            calls = contract.bind(binding)
        except RequiredParamError as exc:
            _emit(fn_name=fn_name, bound_col=None, status="failed", error=str(exc))
            continue
        except (BundleLengthError, MixedShapeError) as exc:
            _emit(fn_name=fn_name, bound_col=None, status="failed", error=str(exc))
            continue

        # Any pd.Series param left unbound is a hard fail for the step.
        unbound = _unbound_series_param(contract, binding)
        if unbound is not None:
            return working, (
                f"param '{unbound}' is unbound — attach a column binding first"
            ), run_results

        mode = calls[0].mode
        multi = len(calls[0].column_kwargs) >= 2

        if mode in ("table", "value"):
            result = realize(contract, calls[0], fn_source=fn_source, frame=current)
            if isinstance(result, FailedFunctionEntry):
                return working, _fail_msg(result, "worker failed"), run_results
            if isinstance(result, pd.DataFrame):
                current = result
            elif mode == "value":
                current = current.copy()
                series = _normalize_to_series(result, len(current))
                new_col = _unique_column_name(
                    append_name or normalize_label(fn_name), set(current.columns)
                )
                current[new_col] = series.values
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        # column / row: one realize per BoundCall. No single input column exists to
        # overwrite by default when several params are column-backed — require targets.
        if multi and output_mode == "replace" and not output_targets:
            _emit(
                fn_name=fn_name, bound_col=None, status="failed",
                error="replace mode with multiple column-backed params requires "
                      "explicit output targets — set an output target per bundle",
            )
            continue

        # Bundles read their INPUT columns from the table as it was at the start of
        # this function's run — not from the progressively-mutated `current`. A
        # replace target can overlap another bundle's input column, so reading from
        # `current` would feed an already-overwritten value into a later bundle.
        fn_input = current.copy()

        for i, call in enumerate(calls):
            result = realize(contract, call, fn_source=fn_source, frame=fn_input)
            if isinstance(result, FailedFunctionEntry):
                return working, _fail_msg(result, "worker failed"), run_results

            current = current.copy()
            series = _normalize_to_series(result, len(current))

            if output_mode == "replace":
                # bundle i -> target i (output_targets in position order); a single
                # column-backed param defaults to overwriting its input column.
                if i < len(output_targets):
                    target_col = output_targets[i]
                elif not multi:
                    target_col = call.bundle_key
                else:
                    return working, (
                        "replace mode with multiple column-backed params needs one "
                        f"output target per bundle — got {len(output_targets)} "
                        f"target(s) for {len(calls)} bundle(s)"
                    ), run_results
                current[target_col] = series.values
            else:
                # append: a new column per bundle, never clobbering an existing one.
                # #264: default auto-label = function name + the bundle's key (its
                # varying column(s)), so N appends are self-describing and distinct;
                # a user-provided append name overrides it. Collision suffix is a
                # last resort only (same fn+col twice).
                base_name = append_name or normalize_label(f"{fn_name}_{call.bundle_key}")
                new_col = _unique_column_name(base_name, set(current.columns))
                current[new_col] = series.values

            _emit(fn_name=fn_name, bound_col=call.bundle_key, status="ok", error=None)

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
    """Build a step-results entry for a built-in step.

    Reuses the transform RunResult shape (function_type='transform') so the Results
    screen renders it like any other transform step; the built-in type is the label.

    ``consumed_result_id`` is the resolved transformed-output ``result_id`` a join
    consumed (lineage — PRD User Story 7); None for raw joins and for all non-join built-ins.
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
        rows_affected=rows_affected,
        consumed_result_id=consumed_result_id,
    )
    return BuiltinResultEntry(
        run_result=rr,
        step_id=step.step_id,
        builtin_type=btype,
        set_name=btype,
    )


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

    Per function: ``contract.bind(binding)`` resolves the persisted binding into
    ordered ``BoundCall``s and ``realize`` executes each against the original frame;
    ``interpret_validation_result`` normalizes every worker result (boolean vector,
    bare bool, or FailedFunctionEntry) into one ValidationRunResult per bundle.
    A binding/config error (required param, unequal varying counts, mixed shapes,
    unbound Series) fails the function cleanly; the step continues so others run.

    The returned entries preserve the legacy wire keys (function_id, function_name,
    set_name, set_id, status, rows_passed, rows_failed, pass_rate, failing_rows,
    error) and additively carry the RunResult identity (result_id) and normalized
    label via ValidationRunResult.to_dict().
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
        # the ValidationResultEntry variant carries the validation step's provenance.
        return ValidationResultEntry(
            run_result=rr,
            function_id=fn_id,
            function_name=fn_name,
            set_name=set_name,
            set_id=set_id,
        )

    for fn in step.functions:
        if fn.function_type != "validation":
            continue

        fn_id = fn.function_id
        fn_name = fn.function_name
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
            sql_result = execute_sql_function(conn, module_path, source_id)
            results.append(interpret_validation_result(
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

        contract, binding = fn.contract_binding()

        try:
            calls = contract.bind(binding)
        except RequiredParamError as exc:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[], error=str(exc),
            ))
            continue
        except (BundleLengthError, MixedShapeError) as exc:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[], error=str(exc),
            ))
            continue

        # Any pd.Series param left unbound fails this function (the step continues).
        unbound = _unbound_series_param(contract, binding)
        if unbound is not None:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[],
                error=f"param '{unbound}' is unbound — attach a column binding first",
            ))
            continue

        mode = calls[0].mode
        if mode in ("table", "value"):
            # Whole-table call: the full original frame, once (no column expansion).
            result = realize(contract, calls[0], fn_source=fn_source, frame=original)
            results.append(interpret_validation_result(
                result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        # column / row: an eligible param bound to N columns expands into N argument
        # bundles (multi_select_eligible), and a function may bind MULTIPLE
        # column-backed params — every param's bundle column is delivered
        # (§12 / ADR-0001). The function runs once per BoundCall.
        for call in calls:
            result = realize(contract, call, fn_source=fn_source, frame=original)
            results.append(interpret_validation_result(
                result, original, fn_id=fn_id, fn_name=fn_name,
                bound_col=call.bundle_key, emit=_emit,
            ))

    return results


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
                # rows_affected stays None on a failed transform (frame unchanged).
                entries.append(TransformResultEntry(
                    run_result=tr, source_function_map_id=sfm_id, set_name=set_name
                ))
            else:
                working = new_working
                write_staging_table(env.conn, env.source_id, working, env.ts)
                wrote_staging = True
                emitted = run_results or [
                    _transform_runresult(step, env.source_id, status="ok", error=None)
                ]
                rows = len(working)
                for rr in emitted:
                    entries.append(TransformResultEntry(
                        run_result=replace(rr, rows_affected=rows),
                        source_function_map_id=sfm_id,
                        set_name=set_name,
                    ))

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
    individually, and a built-in member becomes additive later (#41) without
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
        type-expressible yet (storing built-ins in a set is #41, which will widen the
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
    """Executes a built-in step.

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
