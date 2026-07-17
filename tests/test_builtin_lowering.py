"""Builtin lowering tests — filter + date_range as FunctionContracts (#142, Phase 5a).

Guarantees covered (CLAUDE.md rule 10):
  - Every filter operator family runs through its per-operator contract with the
    retired `_execute_filter` semantics: value bound as `?` (DuckDB casts to the
    column type), contains/not_contains via VARCHAR LIKE with lowering-time
    wildcards, is_null/is_not_null nullary. Hostile values are inert.
  - date_range runs as predicate-contract calls combined in DNF: inclusive
    bounds, DATE-granularity casts, NULL dates fail their condition but the row
    may pass via another OR group, AND within group / OR across groups.
  - Masks are row-aligned even for frames with non-default indexes (messy data).
  - A bound column missing from the working frame fails the step with the
    renderer's refresh-the-binding rejection (never quoted-and-hoped).
  - dnf.normalize_mask / combine_dnf: NULL→False, misalignment rejected.

The pre-existing test_builtins.py execution suite (which runs through
execute_builtin_step) is the golden-parity guard for the lowered executors.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipeui.backend.domain.functions.builtin_lowering import (
    FILTER_CONTRACTS,
    execute_date_range_lowered,
    execute_filter_lowered,
)
from pipeui.backend.domain.functions.builtins import (
    _run_lowered_date_range,
    _run_lowered_filter,
)
from pipeui.backend.domain.runner.dnf import combine_dnf, normalize_mask


@pytest.fixture
def sales_df():
    return pd.DataFrame({
        "id": ["r1", "r2", "r3", "r4"],
        "amount": [10, 20, 30, None],
        "region": ["east", "west", None, "northeast"],
    })


# ---------------------------------------------------------------------------
# dnf — unit
# ---------------------------------------------------------------------------

class TestDnf:
    @pytest.mark.unit
    def test_normalize_nulls_fail_their_condition(self):
        raw = pd.Series([True, None, False, True])
        assert normalize_mask(raw, 4).tolist() == [True, False, False, True]

    @pytest.mark.unit
    def test_normalize_rejects_misaligned_mask(self):
        with pytest.raises(ValueError, match="row-aligned"):
            normalize_mask(pd.Series([True]), 3)

    @pytest.mark.unit
    def test_and_within_group_or_across_groups(self):
        a = pd.Series([True, True, False, False])
        b = pd.Series([True, False, True, False])
        c = pd.Series([False, False, False, True])
        # (a AND b) OR (c) → [True, False, False, True]
        assert combine_dnf([[a, b], [c]], 4).tolist() == [True, False, False, True]

    @pytest.mark.unit
    def test_empty_frame_combines_to_empty_mask(self):
        assert combine_dnf([[pd.Series([], dtype=bool)]], 0).tolist() == []

    @pytest.mark.unit
    def test_empty_groups_rejected(self):
        with pytest.raises(ValueError, match="at least one group"):
            combine_dnf([], 3)
        with pytest.raises(ValueError, match="at least one group"):
            combine_dnf([[]], 3)


# ---------------------------------------------------------------------------
# filter — per-operator contracts
# ---------------------------------------------------------------------------

class TestFilterLowered:
    @pytest.mark.unit
    def test_contracts_stay_minimal(self):
        # The operator selects the contract; no contract carries an enum/operator
        # param (plan: builtins adapt to the contract, never the reverse).
        for op, contract in FILTER_CONTRACTS.items():
            names = [p.name for p in contract.params]
            assert names in (["column"], ["column", "value"]), (op, names)
            assert contract.engine == "sql"

    @pytest.mark.integration
    @pytest.mark.parametrize("operator,value,expected_ids", [
        ("eq", "20", ["r2"]),
        ("neq", "20", ["r1", "r3"]),      # NULL amount fails != (SQL semantics)
        ("gt", "15", ["r2", "r3"]),
        ("gte", "20", ["r2", "r3"]),
        ("lt", "20", ["r1"]),
        ("lte", "20", ["r1", "r2"]),
    ])
    def test_comparison_operators(self, db, sales_df, operator, value, expected_ids):
        out = execute_filter_lowered(db, sales_df, {
            "column": "amount", "operator": operator, "value": value,
        })
        assert out["id"].tolist() == expected_ids

    @pytest.mark.integration
    def test_contains_and_not_contains_on_messy_strings(self, db, sales_df):
        out = execute_filter_lowered(db, sales_df, {
            "column": "region", "operator": "contains", "value": "east",
        })
        assert out["id"].tolist() == ["r1", "r4"]  # east + northeast; NULL fails LIKE
        out = execute_filter_lowered(db, sales_df, {
            "column": "region", "operator": "not_contains", "value": "east",
        })
        assert out["id"].tolist() == ["r2"]  # NULL fails NOT LIKE too

    @pytest.mark.integration
    def test_nullary_operators(self, db, sales_df):
        out = execute_filter_lowered(db, sales_df, {
            "column": "amount", "operator": "is_null",
        })
        assert out["id"].tolist() == ["r4"]
        out = execute_filter_lowered(db, sales_df, {
            "column": "region", "operator": "is_not_null",
        })
        assert out["id"].tolist() == ["r1", "r2", "r4"]

    @pytest.mark.integration
    def test_hostile_value_is_inert(self, db, sales_df):
        out = execute_filter_lowered(db, sales_df, {
            "column": "region", "operator": "eq",
            "value": "east'; DROP TABLE function_registry; --",
        })
        assert len(out) == 0  # matches nothing, drops nothing

    @pytest.mark.integration
    def test_missing_column_rejected_with_refresh_diagnostic(self, db, sales_df):
        with pytest.raises(ValueError, match="not a column of the input"):
            execute_filter_lowered(db, sales_df, {
                "column": "ghost", "operator": "eq", "value": "1",
            })

    @pytest.mark.integration
    def test_bad_config_still_raises_value_error(self, db, sales_df):
        # Validation lives with its owner (builtins.py) and runs before delegation.
        with pytest.raises(ValueError, match="operator must be one of"):
            _run_lowered_filter(db, sales_df, {
                "column": "amount", "operator": "between", "value": "1",
            })


# ---------------------------------------------------------------------------
# date_range — predicate contracts + DNF
# ---------------------------------------------------------------------------

@pytest.fixture
def dated_df():
    return pd.DataFrame({
        "id": ["r1", "r2", "r3", "r4", "r5"],
        "opened": pd.to_datetime([
            "2026-01-01", "2026-02-15", "2026-03-31", None, "2026-06-01",
        ]),
        "closed": pd.to_datetime([
            "2026-01-10", "2026-02-20", None, "2026-04-05", "2026-06-30",
        ]),
    })


class TestDateRangeLowered:
    @pytest.mark.integration
    def test_inclusive_both_bounds(self, db, dated_df):
        out = execute_date_range_lowered(db, dated_df, {"groups": [{"conditions": [
            {"column": "opened", "start": "2026-01-01", "end": "2026-02-15"},
        ]}]})
        assert out["id"].tolist() == ["r1", "r2"]  # both boundary days inside

    @pytest.mark.integration
    def test_one_sided_bounds(self, db, dated_df):
        out = execute_date_range_lowered(db, dated_df, {"groups": [{"conditions": [
            {"column": "opened", "start": "2026-03-01", "end": None},
        ]}]})
        assert out["id"].tolist() == ["r3", "r5"]
        out = execute_date_range_lowered(db, dated_df, {"groups": [{"conditions": [
            {"column": "opened", "start": "", "end": "2026-02-15"},
        ]}]})
        assert out["id"].tolist() == ["r1", "r2"]

    @pytest.mark.integration
    def test_null_fails_condition_but_row_passes_via_or_group(self, db, dated_df):
        # r4 has NULL opened (fails group 1) but closed 2026-04-05 (passes group 2).
        out = execute_date_range_lowered(db, dated_df, {"groups": [
            {"conditions": [{"column": "opened", "start": "2026-01-01", "end": "2026-01-31"}]},
            {"conditions": [{"column": "closed", "start": "2026-04-01", "end": "2026-04-30"}]},
        ]})
        assert out["id"].tolist() == ["r1", "r4"]

    @pytest.mark.integration
    def test_and_within_group(self, db, dated_df):
        # opened in Jan–Mar AND closed in Feb → only r2 (r3's closed is NULL).
        out = execute_date_range_lowered(db, dated_df, {"groups": [{"conditions": [
            {"column": "opened", "start": "2026-01-01", "end": "2026-03-31"},
            {"column": "closed", "start": "2026-02-01", "end": "2026-02-28"},
        ]}]})
        assert out["id"].tolist() == ["r2"]

    @pytest.mark.integration
    def test_mask_alignment_survives_messy_index(self, db, dated_df):
        # A frame with a shuffled, non-default index (mid-pipeline reality) must
        # still filter by POSITION-aligned masks, not index labels.
        shuffled = dated_df.copy()
        shuffled.index = [10, 3, 99, 3, 7]  # duplicate + non-monotonic labels
        out = execute_date_range_lowered(db, shuffled, {"groups": [{"conditions": [
            {"column": "opened", "start": "2026-01-01", "end": "2026-02-15"},
        ]}]})
        assert out["id"].tolist() == ["r1", "r2"]
        assert list(out.index) == [0, 1]  # fresh RangeIndex, like the retired executor

    @pytest.mark.integration
    def test_empty_frame_returns_empty(self, db, dated_df):
        empty = dated_df.iloc[0:0]
        out = execute_date_range_lowered(db, empty, {"groups": [{"conditions": [
            {"column": "opened", "start": "2026-01-01", "end": "2026-12-31"},
        ]}]})
        assert len(out) == 0

    @pytest.mark.integration
    def test_missing_column_rejected(self, db, dated_df):
        with pytest.raises(ValueError, match="not a column of the input"):
            execute_date_range_lowered(db, dated_df, {"groups": [{"conditions": [
                {"column": "ghost", "start": "2026-01-01", "end": None},
            ]}]})

    @pytest.mark.integration
    def test_bad_config_still_raises_value_error(self, db, dated_df):
        # Validation lives with its owner (builtins.py) and runs before delegation.
        with pytest.raises(ValueError, match="at least one bound"):
            _run_lowered_date_range(db, dated_df, {"groups": [{"conditions": [
                {"column": "opened", "start": None, "end": ""},
            ]}]})


# ---------------------------------------------------------------------------
# rename — python-engine contract + simultaneous-apply orchestration (#144)
# ---------------------------------------------------------------------------

from pipeui.backend.domain.functions.builtin_lowering import (  # noqa: E402
    RENAME_COLUMN,
    _rename_schedule,
    execute_rename_lowered,
)


class TestRenameLowered:
    @pytest.mark.unit
    def test_contract_stays_minimal(self):
        # One plain python-engine function; batch semantics live in orchestration.
        assert RENAME_COLUMN.engine == "python"
        assert [p.name for p in RENAME_COLUMN.params] == ["df", "old_name", "new_name"]
        assert RENAME_COLUMN.body is not None  # inline source, executed via realize

    @pytest.mark.unit
    def test_schedule_direct_when_no_overlap(self):
        assert _rename_schedule(["a", "b"], {"a": "x", "b": "y"}) == [("a", "x"), ("b", "y")]

    @pytest.mark.unit
    def test_schedule_two_pass_for_swap_and_chain(self):
        swap = _rename_schedule(["a", "b"], {"a": "b", "b": "a"})
        assert swap == [("a", "__pipeui_ren_0"), ("b", "__pipeui_ren_1"),
                        ("__pipeui_ren_0", "b"), ("__pipeui_ren_1", "a")]
        chain = _rename_schedule(["a", "b"], {"a": "b", "b": "c"})
        assert chain[:2] == [("a", "__pipeui_ren_0"), ("b", "__pipeui_ren_1")]
        assert chain[2:] == [("__pipeui_ren_0", "b"), ("__pipeui_ren_1", "c")]

    @pytest.mark.unit
    def test_schedule_temp_names_dodge_real_columns(self):
        schedule = _rename_schedule(["a", "b", "__pipeui_ren_0"], {"a": "b", "b": "a"})
        used_temps = [tmp for _, tmp in schedule[:2]]
        assert "__pipeui_ren_0" not in used_temps  # reserved name occupied → dodged

    @pytest.mark.integration
    def test_rename_through_worker_preserves_messy_data(self):
        df = pd.DataFrame({
            "amount": [10.5, None, 30.0],
            "region": ["east", None, ""],
        })
        out = execute_rename_lowered(df, {"renames": {"amount": "total"}})
        assert list(out.columns) == ["total", "region"]
        assert out["total"].tolist()[0] == 10.5 and pd.isna(out["total"].tolist()[1])
        # Arrow roundtrip may represent an object-column None as nan — both are
        # pandas nulls (the same representation every worker-run pd.DataFrame
        # function returns); values and null positions are what's guaranteed.
        region = out["region"].tolist()
        assert region[0] == "east" and pd.isna(region[1]) and region[2] == ""

    @pytest.mark.integration
    def test_chain_applies_simultaneously(self):
        # {a→b, b→c}: original a becomes b, original b becomes c — NOT a→b→c.
        df = pd.DataFrame({"a": [1], "b": [2]})
        out = execute_rename_lowered(df, {"renames": {"a": "b", "b": "c"}})
        assert sorted(out.columns) == ["b", "c"]
        assert out["b"].iloc[0] == 1 and out["c"].iloc[0] == 2

    @pytest.mark.integration
    def test_error_leaves_checks_before_any_call(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        with pytest.raises(ValueError, match="not found"):
            execute_rename_lowered(df, {"renames": {"ghost": "x", "a": "y"}})
        assert list(df.columns) == ["a", "b"]  # untouched
