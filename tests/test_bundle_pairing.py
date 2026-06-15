"""Behavioral guarantees for the pure bundle-pairing function — slice runner-execution/3 (§13).

The pure-logic seam (PRD Testing Decisions). `pair_bundles` is extracted from the
executor so the argument-bundle model is tested directly and exhaustively, with no
DuckDB and no worker — this file imports nothing app-stateful.

Guarantees under test (slice acceptance #0/#1):
  - zip-by-position yields N ordered bundles, in user-placed column order
  - a length-1 static param broadcasts its single column into every bundle
  - matrix: 3,3,1 -> 3 bundles; single-param N -> N bundles; all-static -> 1 bundle
  - unequal lengths among VARYING params (3,2) raise a validation error
"""
from __future__ import annotations

import pytest

from pipeui.workflow.bundles import BundleLengthError, pair_bundles


def _param(param_id, columns):
    return {"param_id": param_id, "columns": list(columns)}


# ---------------------------------------------------------------------------
# Acceptance #0 — pure pairing
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_single_param_n_columns_yields_n_bundles():
    """A single varying param bound to N columns -> N ordered bundles."""
    bundles = pair_bundles([_param("p1", ["a", "b", "c"])])
    assert len(bundles) == 3
    assert [b.columns["p1"] for b in bundles] == ["a", "b", "c"]


@pytest.mark.unit
def test_bundles_preserve_user_placed_column_order():
    """Bundle i takes each varying param's i-th column in the given (position) order."""
    bundles = pair_bundles([_param("p1", ["c", "a", "b"])])
    assert [b.columns["p1"] for b in bundles] == ["c", "a", "b"]


@pytest.mark.unit
def test_two_varying_params_pair_positionally():
    """Two varying params of equal length N pair i-th with i-th -> N bundles."""
    bundles = pair_bundles([
        _param("p1", ["a1", "a2", "a3"]),
        _param("p2", ["b1", "b2", "b3"]),
    ])
    assert len(bundles) == 3
    assert bundles[0].columns == {"p1": "a1", "p2": "b1"}
    assert bundles[1].columns == {"p1": "a2", "p2": "b2"}
    assert bundles[2].columns == {"p1": "a3", "p2": "b3"}


@pytest.mark.unit
def test_static_param_broadcasts_into_every_bundle():
    """A length-1 static param's single column is broadcast into every bundle (3,3,1 -> 3)."""
    bundles = pair_bundles([
        _param("p1", ["a1", "a2", "a3"]),
        _param("p2", ["b1", "b2", "b3"]),
        _param("p3", ["k"]),  # static — broadcasts
    ])
    assert len(bundles) == 3
    assert all(b.columns["p3"] == "k" for b in bundles)
    assert [b.columns["p1"] for b in bundles] == ["a1", "a2", "a3"]


@pytest.mark.unit
def test_all_static_yields_one_bundle():
    """All params bound to exactly one column -> a single bundle (N=1)."""
    bundles = pair_bundles([
        _param("p1", ["a"]),
        _param("p2", ["b"]),
    ])
    assert len(bundles) == 1
    assert bundles[0].columns == {"p1": "a", "p2": "b"}


@pytest.mark.unit
def test_empty_param_list_yields_one_empty_bundle():
    """No params (e.g. pd.DataFrame-only) -> a single empty bundle (the N=1 base case)."""
    bundles = pair_bundles([])
    assert len(bundles) == 1
    assert bundles[0].columns == {}


@pytest.mark.unit
def test_varying_label_lists_only_varying_columns_for_that_bundle():
    """Each bundle reports the varying column(s) it carries — drives the result label.

    Static (broadcast) columns are excluded from the label so the card reads as the
    column that actually varies across bundles.
    """
    bundles = pair_bundles([
        _param("p1", ["a1", "a2"]),
        _param("p3", ["k"]),  # static
    ])
    assert bundles[0].varying_columns == ["a1"]
    assert bundles[1].varying_columns == ["a2"]


# ---------------------------------------------------------------------------
# Acceptance #1 — equal-length-among-varying rule (pure side)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_unequal_lengths_among_varying_raise():
    """3,2 among varying params is rejected — no silent zip-shortest truncation."""
    with pytest.raises(BundleLengthError):
        pair_bundles([
            _param("p1", ["a1", "a2", "a3"]),
            _param("p2", ["b1", "b2"]),
        ])


@pytest.mark.unit
def test_unequal_error_names_the_conflicting_counts():
    """The raised error names the distinct lengths so attach can surface a clear message."""
    with pytest.raises(BundleLengthError) as exc:
        pair_bundles([
            _param("p1", ["a1", "a2", "a3"]),
            _param("p2", ["b1", "b2"]),
        ])
    msg = str(exc.value)
    assert "3" in msg and "2" in msg


@pytest.mark.unit
def test_length_one_never_triggers_the_unequal_rule():
    """A length-1 param alongside a varying param broadcasts; it is not 'unequal'."""
    bundles = pair_bundles([
        _param("p1", ["a1", "a2", "a3"]),
        _param("p2", ["k"]),
    ])
    assert len(bundles) == 3


@pytest.mark.unit
def test_bundle_length_error_is_a_value_error():
    """BundleLengthError is a ValueError subclass so existing 422-on-ValueError paths catch it."""
    assert issubclass(BundleLengthError, ValueError)
