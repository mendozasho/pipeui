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

from pipeui.workflow.context import BUILTIN, FUNCTION


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
            working = execute_builtin_step(env.conn, working, step)
            _write_staging_table(env.conn, env.source_id, working, env.ts)
            entry = _builtin_result(
                step, env.source_id, status="ok", error=None,
                rows_affected=len(working),
            )
            return StepExecResult(working=working, entries=[entry], wrote_staging=True)
        except Exception as exc:  # noqa: BLE001 - preserve inline behavior (record + continue)
            entry = _builtin_result(
                step, env.source_id, status="failed", error=str(exc),
                rows_affected=None,
            )
            return StepExecResult(working=working, entries=[entry], wrote_staging=False)


# The step-type registry the runner dispatches through. Keyed by StepContext
# step_type; both function and set steps resolve to the function executor (a set is
# the function-step container — slice 4 expands its members on top of this).
STEP_EXECUTORS: dict[str, StepExecutor] = {
    FUNCTION: FunctionStepExecutor(),
    BUILTIN: BuiltinStepExecutor(),
}
