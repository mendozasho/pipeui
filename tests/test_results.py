"""Behavioral guarantees for src/pipeui/results.py (slice runner-execution/1).

Guarantees under test (slice acceptance):
  #5 — label normalization is a pure function: strips leading underscores and odd
       tokens, never returns an empty label (unit-tested directly).
  #0 — RunResult identity is a deterministic UUID5(function, argument bundle, source).

The pairing with the executor/API/UI seams lives in test_run_workflow.py,
test_api_pipelines_run.py, test_api_validations.py, and screen-results.test.jsx.
"""
from __future__ import annotations

import uuid

import pytest

from pipeui.backend.data.base.results import RunResult, ValidationRunResult, normalize_label


# ---------------------------------------------------------------------------
# Guarantee #5 — label normalization (pure)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_normalize_label_strips_leading_underscores():
    """Leading underscores are stripped (no '__' or leading '_' survives)."""
    assert normalize_label("__amount") == "amount"
    assert normalize_label("_region") == "region"


@pytest.mark.unit
def test_normalize_label_strips_odd_tokens():
    """Odd (non-alphanumeric) tokens are collapsed/stripped, not passed through."""
    out = normalize_label("amount!!  (usd)")
    assert "!" not in out
    assert "(" not in out
    assert ")" not in out
    assert out.strip("_") == out  # no leading/trailing underscore
    assert out  # non-empty


@pytest.mark.unit
def test_normalize_label_never_empty():
    """An all-odd or empty input still yields a non-empty label."""
    assert normalize_label("") != ""
    assert normalize_label("___") != ""
    assert normalize_label("!!!") != ""
    assert normalize_label(None) != ""


# ---------------------------------------------------------------------------
# Guarantee #0 — RunResult UUID5 identity determinism
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_run_result_identity_is_deterministic_uuid5():
    """Equal (function, argument bundle, source) -> equal result_id; different -> different."""
    sid = uuid.uuid4()
    a = RunResult(
        function_name="check_positive", function_type="validation",
        source_id=sid, bundle_key="amount", label="amount", status="ok",
    )
    b = RunResult(
        function_name="check_positive", function_type="validation",
        source_id=sid, bundle_key="amount", label="amount", status="ok",
    )
    assert a.result_id == b.result_id  # deterministic

    c = RunResult(
        function_name="check_positive", function_type="validation",
        source_id=sid, bundle_key="region", label="region", status="ok",
    )
    assert c.result_id != a.result_id  # different bundle -> different id


@pytest.mark.unit
def test_validation_run_result_to_dict_carries_identity_and_counts():
    """A ValidationRunResult serializes its UUID5 identity, label, type, and counts."""
    sid = uuid.uuid4()
    r = ValidationRunResult(
        function_name="check_positive", function_type="validation",
        source_id=sid, bundle_key="amount", label="amount", status="ok",
        rows_passed=8, rows_failed=2, failing_rows=[{"id": "r3"}],
    )
    d = r.to_dict()
    assert d["result_id"] == r.result_id
    assert d["label"] == "amount"
    assert d["function_type"] == "validation"
    assert d["rows_passed"] == 8
    assert d["rows_failed"] == 2
    assert d["pass_rate"] == pytest.approx(0.8)
