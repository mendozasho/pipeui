"""StepExecutor — the uniform per-step-type execution contract the runner
dispatches through a step-type registry (CONTEXT.md -> StepExecutor; PRD
Implementation Decisions -> uniform StepExecutor contract + step-type registry).

A ``function step`` and a ``built-in step`` are resolved and run the same way: the
runner builds a ``StepContext``, looks the executor up in ``STEP_EXECUTORS`` by
``ctx.step_type``, and calls ``execute(...)``. This replaces the inline ``if/elif``
type branching that used to live in ``run_pipeline``'s loop.

This is a behavior-preserving refactor — each executor wraps the EXISTING run.py /
builtins.py helpers and emits the exact step-result entries the inline loop emitted,
so output is unchanged before and after. The contract is deliberately shaped so a
heterogeneous member (slice 4's function-set adapter) can slot in later, but no new
execution path is built here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

import duckdb
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pipeui.workflow.context import StepContext

from pipeui.workflow.context import BUILTIN, FUNCTION, SET


@dataclass
class StepExecResult:
    """The uniform result an executor returns to the runner.

    ``working`` is the (possibly reshaped) working frame after the step.
    ``entries`` is the list of step-result dicts to splice into ``run_pipeline``'s
    output (identical in shape to the pre-refactor inline emissions).
    ``wrote_staging`` tells the runner the step staged the working frame.
    """

    working: pd.DataFrame
    entries: list[dict] = field(default_factory=list)
    wrote_staging: bool = False


@dataclass
class StepRunEnv:
    """Per-run inputs an executor needs that are not part of the step itself.

    Carries the connection, the source id, the original (pre-transform) instance
    frame validations read against, the staging timestamp, and the run_type-derived
    ``want_transforms`` / ``want_validations`` gates.
    """

    conn: duckdb.DuckDBPyConnection
    source_id: uuid.UUID
    original_df: pd.DataFrame
    ts: int
    want_transforms: bool
    want_validations: bool


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
        # Imported here to avoid an import cycle (run.py imports this module).
        from pipeui.workflow.run import (
            _execute_transform_step,
            _execute_validation_step,
            _step_has,
            _transform_runresult,
            _write_staging_table,
        )

        step = ctx.data
        sfm_id = step["source_function_map_id"]
        set_name = step["set_name"]
        entries: list[dict] = []
        wrote_staging = False

        if env.want_transforms and _step_has(step, "transform"):
            new_working, error, run_results = _execute_transform_step(
                working, step, conn=env.conn, source_id=env.source_id
            )
            if error:
                tr = _transform_runresult(step, env.source_id, status="failed", error=error)
                entry = {
                    "source_function_map_id": sfm_id,
                    "set_name": set_name,
                    "rows_affected": None,
                    "rows_passed": None,
                    "rows_failed": None,
                }
                entry.update(tr.to_dict())
                entries.append(entry)
            else:
                working = new_working
                _write_staging_table(env.conn, env.source_id, working, env.ts)
                wrote_staging = True
                emitted = run_results or [
                    _transform_runresult(step, env.source_id, status="ok", error=None).to_dict()
                ]
                for rr in emitted:
                    entry = {
                        "source_function_map_id": sfm_id,
                        "set_name": set_name,
                        "rows_affected": len(working),
                        "rows_passed": None,
                        "rows_failed": None,
                    }
                    entry.update(rr)
                    entries.append(entry)

        if env.want_validations and _step_has(step, "validation"):
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
        members = ctx.get("functions") or []
        entries: list[dict] = []
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
    def _member_context(set_ctx: "StepContext", member: dict) -> "StepContext":
        """Wrap one set member as a single-member step context, via the factory trio.

        The convergence model builds every context through a ``StepContext`` factory:
        a function member through ``from_function``, a built-in member through
        ``from_builtin`` — never a bare constructor. Routing by the member's own step
        type (``member['step_type']``, defaulting to ``FUNCTION``) keeps dispatch
        type-agnostic: a function member still resolves to ``FUNCTION`` ->
        ``FunctionStepExecutor`` (behavior-preserving), and a built-in member becomes
        runnable additively.

        The sub-context carries the set's step-level keys with a one-element
        ``functions`` list and the set's ``position``, so the per-member executor sees
        the exact step shape it saw when the whole set ran.
        """
        from pipeui.workflow.context import BUILTIN as _BUILTIN
        from pipeui.workflow.context import FUNCTION as _FUNCTION
        from pipeui.workflow.context import StepContext

        sub = dict(set_ctx.data)
        sub["functions"] = [member]
        sub["position"] = set_ctx.position
        if member.get("step_type", _FUNCTION) == _BUILTIN:
            return StepContext.from_builtin(sub)
        return StepContext.from_function(sub)


class BuiltinStepExecutor:
    """Executes a built-in step (join/pivot/filter).

    Mirrors the pre-refactor inline built-in branch exactly: reshape the working
    frame via ``execute_builtin_step``, stage it, and record one ``_builtin_result``
    entry; on exception record a failed ``_builtin_result`` and leave the frame.
    """

    def execute(self, ctx, working, env):
        from pipeui.workflow.builtins import execute_builtin_step
        from pipeui.workflow.run import _builtin_result, _write_staging_table

        step = ctx.data
        try:
            working, consumed_result_id = execute_builtin_step(env.conn, working, step)
            _write_staging_table(env.conn, env.source_id, working, env.ts)
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
