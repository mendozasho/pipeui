"""Carrier contract tests — one per boundary carrier (CONTEXT.md → Module
contracts → carriers). Each carrier crossing a runner module boundary is a frozen,
behavior-free dataclass and is the sole legal shape for that boundary. These tests
assert the *enforcement* is real, not aspirational:

  (a) StepContext variants (FunctionStepContext / BuiltinStepContext / FunctionSpec)
      are frozen, typed, and built only via the from_* factories — mutation raises.
  (b) FrameRef enforces ``result_id is None ⟺ mode == RAW`` in __post_init__ —
      an illegal combination is unconstructable.
  (c) StepRunEnv / StepExecResult are frozen.
  (d) RunResult has a deterministic result_id (equal inputs → equal id).

§13 behavioral-guarantee pattern: each test asserts observable contract behavior.
"""
from __future__ import annotations

import uuid

import pandas as pd
import pytest
from dataclasses import FrozenInstanceError

from pipeui.results import RunResult, StepResultEntry, ValidationRunResult
from pipeui.workflow.executors import StepExecResult, StepRunEnv
from pipeui.workflow.resolve import RAW, TRANSFORMED, FrameRef
from pipeui.workflow.step import (
    BUILTIN,
    FUNCTION,
    SET,
    BuiltinStepContext,
    FunctionSpec,
    FunctionStepContext,
    StepContext,
)


def _fn_row(name="fn"):
    """A loader-shaped function row the from_* factories accept."""
    return {
        "function_id": "f1", "function_name": name, "function_type": "transform",
        "function_class": "pd.series", "function_return_type": "pd.Series",
        "module_path": "/tmp/x.py", "params": ({"param_name": "data"},),
        "output_mode": None, "append_name": None, "output_targets": (),
    }


def _function_row():
    return {
        "source_function_map_id": "sfm", "set_id": "s", "set_name": "set",
        "position": 0, "output_mode": None, "append_name": None,
        "output_targets": (), "functions": [_fn_row()],
    }


def _builtin_row():
    return {
        "step_id": "b1", "step_type": "builtin", "builtin_type": "filter",
        "builtin_config": {"column": "a"}, "position": 1,
    }


# ---------------------------------------------------------------------------
# (a) StepContext variants — frozen, typed, built via factories
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_function_step_context_is_frozen_typed_and_built_via_factory():
    """FunctionStepContext is built by from_function/from_set, exposes typed
    attributes (not a data dict), and is immutable."""
    ctx = StepContext.from_set(_function_row())
    assert isinstance(ctx, FunctionStepContext)
    assert ctx.step_type == SET
    assert ctx.set_id == "s"
    assert isinstance(ctx.functions, tuple)
    assert all(isinstance(m, FunctionSpec) for m in ctx.functions)
    # No legacy dict carrier survives.
    assert not hasattr(ctx, "data")
    assert not hasattr(ctx, "get")
    # Frozen: mutation raises (the enforcement, not a convention).
    with pytest.raises(FrozenInstanceError):
        ctx.set_id = "other"

    # from_function builds the same shape, FUNCTION-tagged.
    fn_ctx = StepContext.from_function(_function_row())
    assert isinstance(fn_ctx, FunctionStepContext)
    assert fn_ctx.step_type == FUNCTION


@pytest.mark.unit
def test_builtin_step_context_is_frozen_typed_and_built_via_factory():
    """BuiltinStepContext is built by from_builtin, exposes typed attributes, and is
    immutable; builtin_config stays the typed Mapping depth boundary."""
    ctx = StepContext.from_builtin(_builtin_row())
    assert isinstance(ctx, BuiltinStepContext)
    assert ctx.step_type == BUILTIN
    assert ctx.builtin_type == "filter"
    assert ctx.builtin_config["column"] == "a"  # Mapping depth boundary, read by key
    assert not hasattr(ctx, "data")
    with pytest.raises(FrozenInstanceError):
        ctx.builtin_type = "join"


@pytest.mark.unit
def test_function_spec_is_frozen_and_typed():
    """FunctionSpec is frozen and typed; params is the typed Mapping depth boundary."""
    spec = StepContext.from_set(_function_row()).functions[0]
    assert isinstance(spec, FunctionSpec)
    assert spec.function_name == "fn"
    assert spec.step_type == FUNCTION
    assert spec.params[0]["param_name"] == "data"  # Mapping, read by key
    with pytest.raises(FrozenInstanceError):
        spec.function_name = "other"


# ---------------------------------------------------------------------------
# (b) FrameRef raw ⟺ no-id invariant
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_frameref_raw_must_not_carry_result_id():
    """A raw frame is the source's own data, not a produced result — carrying a
    result_id is an illegal carrier and is unconstructable."""
    # Legal raw frame: no result_id.
    ref = FrameRef(source_id=uuid.uuid4(), mode=RAW)
    assert ref.result_id is None
    # Illegal: raw + result_id.
    with pytest.raises(ValueError):
        FrameRef(source_id=uuid.uuid4(), mode=RAW, result_id="abc123")


@pytest.mark.unit
def test_frameref_transformed_must_carry_result_id():
    """A transformed frame is a produced result — it MUST carry a result_id; the
    no-id transformed combination is unconstructable."""
    # Legal transformed frame: has a result_id.
    ref = FrameRef(source_id=uuid.uuid4(), mode=TRANSFORMED, result_id="abc123",
                   staging_table="staging_x")
    assert ref.result_id == "abc123"
    # Illegal: transformed + no result_id.
    with pytest.raises(ValueError):
        FrameRef(source_id=uuid.uuid4(), mode=TRANSFORMED)


@pytest.mark.unit
def test_frameref_is_frozen():
    ref = FrameRef(source_id=uuid.uuid4(), mode=RAW)
    with pytest.raises(FrozenInstanceError):
        ref.mode = TRANSFORMED


# ---------------------------------------------------------------------------
# (c) StepRunEnv / StepExecResult frozen
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_step_run_env_is_frozen():
    env = StepRunEnv(
        conn=None, source_id=uuid.uuid4(), original_df=pd.DataFrame({"a": [1]}),
        ts=0, want_transforms=True, want_validations=True, run_transforms=None,
    )
    with pytest.raises(FrozenInstanceError):
        env.want_transforms = False


@pytest.mark.unit
def test_step_exec_result_is_frozen():
    res = StepExecResult(working=pd.DataFrame({"a": [1]}), entries=[], wrote_staging=False)
    with pytest.raises(FrozenInstanceError):
        res.wrote_staging = True


# ---------------------------------------------------------------------------
# (e) StepResultEntry — frozen carrier; to_dict reproduces the legacy entry shape
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_step_result_entry_is_frozen():
    rr = RunResult(
        function_name="f", function_type="transform", source_id=uuid.UUID(int=0),
        bundle_key="", label="f", status="ok", error=None,
    )
    entry = StepResultEntry(run_result=rr, routing={"set_name": "s"})
    with pytest.raises(FrozenInstanceError):
        entry.run_result = rr


@pytest.mark.unit
def test_step_result_entry_to_dict_lays_routing_then_runresult():
    """to_dict() == {**routing, **run_result.to_dict()} — routing first, RunResult
    fields overlay (the exact legacy ``entry.update(rr.to_dict())`` shape), so the
    external {"steps": [...]} wire contract is unchanged."""
    rr = ValidationRunResult(
        function_name="check_x", function_type="validation", source_id=uuid.UUID(int=0),
        bundle_key="amount", label="amount", status="ok", error=None,
        rows_passed=3, rows_failed=1, failing_rows=[{"amount": -1}],
    )
    routing = {"function_id": "fid", "function_name": "STALE", "set_name": "s", "set_id": "sid"}
    entry = StepResultEntry(run_result=rr, routing=routing)
    d = entry.to_dict()
    # routing keys present...
    assert d["function_id"] == "fid"
    assert d["set_id"] == "sid"
    # ...but RunResult's value wins on overlap (function_name), and identity/counts come from rr
    assert d["function_name"] == "check_x"
    assert d["result_id"] == rr.result_id
    assert d["rows_passed"] == 3 and d["rows_failed"] == 1
    assert d == {**routing, **rr.to_dict()}


# ---------------------------------------------------------------------------
# (d) RunResult deterministic result_id
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_run_result_result_id_is_deterministic():
    """Equal (function_name, bundle_key, source_id) inputs always produce the same
    result_id; a differing bundle_key produces a different id."""
    sid = uuid.uuid4()

    def mk(bundle_key):
        return RunResult(
            function_name="fn", function_type="transform", source_id=sid,
            bundle_key=bundle_key, label="fn", status="ok",
        )

    a = mk("col_a")
    b = mk("col_a")
    c = mk("col_b")
    assert a.result_id == b.result_id          # deterministic
    assert a.result_id != c.result_id          # bundle-key sensitive
