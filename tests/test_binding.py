"""Tests for StepBinding / BoundCall / FunctionContract.bind() — the #136 shadow layer.

Guarantees covered (CLAUDE.md rule 10):
  - bind-parity: for every executor-arm shape, bind()'s BoundCalls carry the same
    bundle keys, per-bundle columns, and broadcast scalars that the current
    ``collect_column_backed_params`` + ``pair_bundles`` + ``composite_key`` +
    ``resolve_scalar_kwargs`` path computes — including the failure modes
    (BundleLengthError / MixedShapeError / RequiredParamError).
  - iter_row_args is the bound-args semantic spec: N rows → N argument sets,
    column-backed params take their row's value (pandas NULL → None, exactly like
    the scalar-run wrapper), literals broadcast into every set, empty frame → no
    sets. Messy data (NaN / None / empty strings) behaves like the wrapper.
  - Multi-select composes above: c columns tied to a param → c BoundCalls, each
    spanning the table's rows.
  - Loader hydration: fetch_steps assembles FunctionSpec.contract / .binding from
    the persisted rows, params in loader order, bindings in alias_map.position order.
"""
from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import pytest

from pipeui.backend.data.base.ids import content_hash_id
from pipeui.backend.data.functions.binding import (
    MixedShapeError,
    ParamBinding,
    RequiredParamError,
    StepBinding,
    composite_key,
)
from pipeui.backend.data.functions.contract import ENGINE_PYTHON, FunctionContract, ParamContract
from pipeui.backend.data.runner.bundles import BundleLengthError, pair_bundles
from pipeui.backend.data.runner.step_loader import fetch_steps
from pipeui.backend.domain.runner.bundle_exec import collect_column_backed_params
from pipeui.backend.domain.runner.param_resolve import resolve_scalar_kwargs
from tests.conftest import make_registered_source


# ---------------------------------------------------------------------------
# Helpers — one param description, two consumers (legacy dicts vs contract+binding)
# ---------------------------------------------------------------------------

def _legacy_params(specs: list[dict]) -> list[dict]:
    """Loader-shaped param dicts, as the executor arms consume them."""
    return [
        {
            "param_id": s["name"],
            "param_name": s["name"],
            "param_type": s["type"],
            "bindings": list(s.get("columns", ())),
            "has_default": s.get("default") is not None,
            "default_value": s.get("default"),
            "scalar_value": s.get("value"),
        }
        for s in specs
    ]


def _contract_and_binding(specs: list[dict], *, return_type="pd.Series") -> tuple[FunctionContract, StepBinding]:
    contract = FunctionContract(
        name="f",
        engine=ENGINE_PYTHON,
        params=tuple(
            ParamContract(
                name=s["name"], type_str=s["type"], position=i,
                has_default=s.get("default") is not None,
                default_value=s.get("default"),
            )
            for i, s in enumerate(specs)
        ),
        return_type=return_type,
        signature="(…)",
    )
    binding = StepBinding(params=tuple(
        ParamBinding(
            param_name=s["name"],
            kind=(
                "table" if s["type"] == "pd.DataFrame"
                else "columns" if s.get("columns")
                else "literal"
            ),
            columns=tuple(s.get("columns", ())),
            value=s.get("value"),
        )
        for s in specs
    ))
    return contract, binding


def _legacy_bundle_view(specs: list[dict]) -> tuple[list[str], list[dict[str, str]], dict]:
    """What the executor arms compute today: bundle keys, per-bundle column maps
    (param_name → column), and the broadcast scalar kwargs."""
    params = _legacy_params(specs)
    cb = collect_column_backed_params(params)
    bundles = pair_bundles([{"param_id": p["param_id"], "columns": p["bindings"]} for p in cb])
    keys = [composite_key(b, cb) for b in bundles]
    columns = [{p["param_name"]: b.columns[p["param_id"]] for p in cb} for b in bundles]
    scalars = resolve_scalar_kwargs(params)
    return keys, columns, scalars


# ---------------------------------------------------------------------------
# bind-parity — executor-arm shapes
# ---------------------------------------------------------------------------

class TestBindParity:
    @pytest.mark.unit
    @pytest.mark.parametrize("specs", [
        # single column-backed pd.Series param
        [{"name": "data", "type": "pd.Series", "columns": ["a"]}],
        # multi pd.Series params, 1 column each → one bundle carrying both
        [{"name": "x", "type": "pd.Series", "columns": ["a"]},
         {"name": "y", "type": "pd.Series", "columns": ["b"]}],
        # two varying params (2 cols each) + broadcast static (1 col)
        [{"name": "x", "type": "pd.Series", "columns": ["a", "b"]},
         {"name": "y", "type": "pd.Series", "columns": ["c", "d"]},
         {"name": "z", "type": "pd.Series", "columns": ["e"]}],
        # multi-select on ONE param: 3 columns → 3 bundles
        [{"name": "data", "type": "pd.Series", "columns": ["a", "b", "c"]}],
        # column-backed scalar-shaped params (the scalar run)
        [{"name": "v", "type": "str", "columns": ["a"]},
         {"name": "w", "type": "float", "columns": ["b"]}],
        # column-backed + unbound scalar with persisted value
        [{"name": "data", "type": "pd.Series", "columns": ["a", "b"]},
         {"name": "factor", "type": "float", "value": "2.5"}],
        # unbound scalars: persisted value beats default; default fills the gap
        [{"name": "data", "type": "pd.Series", "columns": ["a"]},
         {"name": "n", "type": "int", "value": "7", "default": "3"},
         {"name": "flag", "type": "bool", "default": "True"},
         {"name": "label", "type": "str", "value": "hi"}],
        # pd.DataFrame param excluded from bundling
        [{"name": "df", "type": "pd.DataFrame"},
         {"name": "data", "type": "pd.Series", "columns": ["a"]}],
    ])
    def test_bound_calls_match_executor_arms(self, specs):
        keys, columns, scalars = _legacy_bundle_view(specs)
        contract, binding = _contract_and_binding(specs)
        calls = contract.bind(binding)
        assert [c.bundle_key for c in calls] == keys
        assert [dict(c.column_kwargs) for c in calls] == columns
        for c in calls:
            assert dict(c.literal_kwargs) == scalars

    @pytest.mark.unit
    def test_unequal_varying_raises_like_pair_bundles(self):
        specs = [
            {"name": "x", "type": "pd.Series", "columns": ["a", "b"]},
            {"name": "y", "type": "pd.Series", "columns": ["c", "d", "e"]},
        ]
        with pytest.raises(BundleLengthError):
            _legacy_bundle_view(specs)
        contract, binding = _contract_and_binding(specs)
        with pytest.raises(BundleLengthError):
            contract.bind(binding)

    @pytest.mark.unit
    def test_mixed_shapes_raise_mixed_shape_error(self):
        specs = [
            {"name": "x", "type": "pd.Series", "columns": ["a"]},
            {"name": "v", "type": "str", "columns": ["b"]},
        ]
        contract, binding = _contract_and_binding(specs)
        with pytest.raises(MixedShapeError):
            contract.bind(binding)

    @pytest.mark.unit
    def test_missing_required_scalar_raises_like_param_resolve(self):
        specs = [
            {"name": "data", "type": "pd.Series", "columns": ["a"]},
            {"name": "threshold", "type": "float"},  # no value, no default
        ]
        with pytest.raises(RequiredParamError):
            resolve_scalar_kwargs(_legacy_params(specs))
        contract, binding = _contract_and_binding(specs)
        with pytest.raises(RequiredParamError):
            contract.bind(binding)

    @pytest.mark.unit
    def test_scalar_coercion_matches(self):
        specs = [
            {"name": "data", "type": "pd.Series", "columns": ["a"]},
            {"name": "n", "type": "int", "value": "42"},
            {"name": "rate", "type": "float", "value": "0.25"},
            {"name": "on", "type": "bool", "value": "yes"},
            {"name": "off", "type": "bool", "value": "false"},
        ]
        contract, binding = _contract_and_binding(specs)
        lit = dict(contract.bind(binding)[0].literal_kwargs)
        assert lit == {"n": 42, "rate": 0.25, "on": True, "off": False}
        assert lit == resolve_scalar_kwargs(_legacy_params(specs))

    @pytest.mark.unit
    def test_bound_scalar_param_is_not_a_literal(self):
        # A column-bound scalar param rides the bundle, never literal_kwargs —
        # mirrors resolve_scalar_kwargs skipping params with bindings.
        specs = [{"name": "v", "type": "str", "columns": ["a"], "value": "ignored"}]
        contract, binding = _contract_and_binding(specs)
        call = contract.bind(binding)[0]
        assert dict(call.column_kwargs) == {"v": "a"}
        assert dict(call.literal_kwargs) == {}

    @pytest.mark.unit
    def test_modes_derived_per_shape(self):
        c1, b1 = _contract_and_binding([{"name": "data", "type": "pd.Series", "columns": ["a"]}])
        assert c1.bind(b1)[0].mode == "column"
        c2, b2 = _contract_and_binding([{"name": "v", "type": "str", "columns": ["a"]}])
        assert c2.bind(b2)[0].mode == "row"
        c3, b3 = _contract_and_binding([
            {"name": "df", "type": "pd.DataFrame"},
            {"name": "n", "type": "int", "value": "1"},
        ])
        call = c3.bind(b3)[0]
        assert call.mode == "table"
        assert call.table_params == ("df",)
        c4, b4 = _contract_and_binding([{"name": "n", "type": "int", "value": "1"}])
        assert c4.bind(b4)[0].mode == "value"


# ---------------------------------------------------------------------------
# iter_row_args — the bound-args semantic spec (messy data included)
# ---------------------------------------------------------------------------

class TestIterRowArgs:
    @pytest.mark.unit
    def test_fifty_rows_yield_fifty_sets_with_broadcast(self):
        specs = [
            {"name": "amount", "type": "float", "columns": ["amt"]},
            {"name": "region", "type": "str", "columns": ["reg"]},
            {"name": "rate", "type": "float", "value": "0.1"},
        ]
        contract, binding = _contract_and_binding(specs, return_type="float")
        calls = contract.bind(binding)
        assert len(calls) == 1 and calls[0].mode == "row"
        frame = pd.DataFrame({
            "amt": [float(i) for i in range(50)],
            "reg": [f"r{i}" for i in range(50)],
        })
        rows = list(calls[0].iter_row_args(frame))
        assert len(rows) == 50
        assert rows[0] == {"amount": 0.0, "region": "r0", "rate": 0.1}
        assert rows[49] == {"amount": 49.0, "region": "r49", "rate": 0.1}
        # the literal is broadcast into every set
        assert all(r["rate"] == 0.1 for r in rows)

    @pytest.mark.unit
    def test_messy_data_null_sentinel_matches_scalar_run_wrapper(self):
        # NaN and None become None (like build_scalar_frame_wrapper's pd.isna
        # sentinel); empty strings are real values and pass through untouched.
        specs = [
            {"name": "a", "type": "str", "columns": ["x"]},
            {"name": "b", "type": "float", "columns": ["y"]},
        ]
        contract, binding = _contract_and_binding(specs, return_type="str")
        frame = pd.DataFrame({
            "x": ["ok", None, "", "end"],
            "y": [1.5, np.nan, 0.0, np.nan],
        })
        rows = list(contract.bind(binding)[0].iter_row_args(frame))
        assert rows == [
            {"a": "ok", "b": 1.5},
            {"a": None, "b": None},
            {"a": "", "b": 0.0},
            {"a": "end", "b": None},
        ]

    @pytest.mark.unit
    def test_multi_select_three_columns_gives_three_calls_of_n_rows(self):
        specs = [{"name": "v", "type": "str", "columns": ["c1", "c2", "c3"]}]
        contract, binding = _contract_and_binding(specs, return_type="str")
        calls = contract.bind(binding)
        assert [c.bundle_key for c in calls] == ["c1", "c2", "c3"]
        frame = pd.DataFrame({
            "c1": [f"a{i}" for i in range(50)],
            "c2": [f"b{i}" for i in range(50)],
            "c3": [f"c{i}" for i in range(50)],
        })
        per_call = [list(c.iter_row_args(frame)) for c in calls]
        assert [len(rows) for rows in per_call] == [50, 50, 50]
        assert per_call[0][7] == {"v": "a7"}
        assert per_call[1][7] == {"v": "b7"}
        assert per_call[2][7] == {"v": "c7"}

    @pytest.mark.unit
    def test_empty_frame_yields_nothing(self):
        specs = [{"name": "v", "type": "str", "columns": ["x"]}]
        contract, binding = _contract_and_binding(specs, return_type="str")
        frame = pd.DataFrame({"x": pd.Series([], dtype=object)})
        assert list(contract.bind(binding)[0].iter_row_args(frame)) == []


# ---------------------------------------------------------------------------
# Loader hydration — FunctionSpec.contract / .binding from persisted rows
# ---------------------------------------------------------------------------

def _seed_fn(db, source_id, fn_name, param_specs, *, module_path="/tmp/f.py"):
    """Seed function_registry/parameter/set/map rows + alias_map bindings.

    param_specs: list of (param_name, param_type, [column_id, ...], scalar_value).
    Returns fn_id.
    """
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry (function_id, content_hash_id, function_class, "
        "function_name, function_doc, function_return_type, function_signature, "
        "function_type, module_path, is_active, engine, function_body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, 'python', NULL)",
        [fn_id, uuid.uuid4(), "pd.Series", fn_name, "docstring here", "pd.Series",
         "(zeta: pd.Series, alpha: pd.Series, mid: float = 0.5) -> pd.Series",
         "transform", module_path],
    )
    for pos, (p_name, p_type, col_ids, scalar_value) in enumerate(param_specs):
        param_id = content_hash_id("parameter", "param_id", str(fn_id), p_name)
        db.execute(
            "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, "
            "function_id, has_default, default_value, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [param_id, uuid.uuid4(), p_name, p_type, fn_id,
             scalar_value is None and p_name == "mid", "0.5" if p_name == "mid" else None, pos],
        )
        for cpos, col_id in enumerate(col_ids):
            db.execute(
                "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) "
                "VALUES (?, ?, ?, ?, ?)",
                [uuid.uuid4(), col_id, param_id, source_id, cpos],
            )
        if scalar_value is not None:
            db.execute(
                "INSERT INTO source_scalar_map (scalar_map_id, source_id, param_id, value) "
                "VALUES (?, ?, ?, ?)",
                [uuid.uuid4(), source_id, param_id, scalar_value],
            )
    set_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, uuid.uuid4(), fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, "
        "output_mode, append_name) VALUES (?, ?, ?, ?, 'append', NULL)",
        [uuid.uuid4(), source_id, set_id, 0],
    )
    return fn_id


class TestLoaderHydration:
    @pytest.mark.integration
    def test_fetch_steps_hydrates_contract_and_binding(self, db):
        source_id, col_ids = make_registered_source(db, n_columns=3)
        # signature order: zeta, alpha, mid — alphabetical would be alpha, mid, zeta
        _seed_fn(db, source_id, "blend", [
            ("zeta", "pd.Series", [col_ids[0], col_ids[1]], None),
            ("alpha", "pd.Series", [col_ids[2]], None),
            ("mid", "float", [], "0.7"),
        ])
        steps = fetch_steps(db, source_id)
        assert len(steps) == 1
        spec = steps[0].functions[0]

        contract = spec.contract
        assert isinstance(contract, FunctionContract)
        # contract params in signature order (position), regardless of loader order
        assert [p.name for p in contract.params] == ["zeta", "alpha", "mid"]
        assert [p.position for p in contract.params] == [0, 1, 2]
        assert contract.engine == "python"
        assert contract.function_class == "pd.Series"
        assert contract.doc == "docstring here"

        binding = spec.binding
        assert isinstance(binding, StepBinding)
        # binding params in loader order (alphabetical until Phase 3)
        assert [p.param_name for p in binding.params] == ["alpha", "mid", "zeta"]
        zeta = binding.get("zeta")
        assert zeta.kind == "columns" and zeta.columns == ("col_0", "col_1")
        alpha = binding.get("alpha")
        assert alpha.kind == "columns" and alpha.columns == ("col_2",)
        mid = binding.get("mid")
        assert mid.kind == "literal" and mid.value == "0.7"

        # end-to-end shadow: the hydrated pair binds without touching the executors
        calls = contract.bind(binding)
        assert len(calls) == 2  # zeta varies (2 cols), alpha broadcasts
        assert all(c.mode == "column" for c in calls)
        assert dict(calls[0].column_kwargs) == {"zeta": "col_0", "alpha": "col_2"}
        assert dict(calls[1].column_kwargs) == {"zeta": "col_1", "alpha": "col_2"}
        assert dict(calls[0].literal_kwargs) == {"mid": 0.7}

    @pytest.mark.integration
    def test_hydration_survives_unbound_and_default_params(self, db):
        source_id, col_ids = make_registered_source(db, n_columns=1)
        _seed_fn(db, source_id, "solo", [
            ("data", "pd.Series", [col_ids[0]], None),
            ("mid", "float", [], None),  # no scalar_value → default 0.5 at bind time
        ])
        spec = fetch_steps(db, source_id)[0].functions[0]
        calls = spec.contract.bind(spec.binding)
        assert dict(calls[0].literal_kwargs) == {"mid": 0.5}
