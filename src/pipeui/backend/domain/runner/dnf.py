"""DNF mask combination (L3 — runner execution mechanics). #142.

One-level disjunctive normal form over boolean row masks: conditions AND within a
group, groups OR across. This is the orchestration layer ABOVE the contract that
the date_range built-in lowers onto — each condition is one predicate-contract
call producing a mask; the grouping semantics live here, never on the contract
(plan: builtins adapt to the contract, not the reverse).

Pure module: pandas only. Masks are normalized defensively — SQL predicates
return NULL for NULL inputs, and a NULL condition must FAIL its condition (the
row may still pass via another OR group), so nulls become False before combining.
"""
from __future__ import annotations

import pandas as pd


def normalize_mask(raw: pd.Series, n_rows: int) -> pd.Series:
    """Normalize one predicate result column to a row-aligned boolean mask.

    Index-reset for positional alignment with the working frame; SQL NULL
    (a condition over a NULL date) becomes False — fails the condition.
    """
    mask = raw.reset_index(drop=True)
    if len(mask) != n_rows:
        raise ValueError(
            f"predicate returned {len(mask)} rows for a {n_rows}-row frame — "
            "masks must be row-aligned"
        )
    return mask.fillna(False).astype(bool)


def combine_dnf(group_masks: list[list[pd.Series]], n_rows: int) -> pd.Series:
    """Combine per-condition masks in DNF: AND within each group, OR across groups.

    ``group_masks`` is a non-empty list of groups, each a non-empty list of
    row-aligned boolean masks (already normalized). Returns the final keep-mask.
    """
    if not group_masks or any(not conds for conds in group_masks):
        raise ValueError("DNF combination needs at least one group with one condition")
    total = pd.Series([False] * n_rows, dtype=bool)
    for conds in group_masks:
        group = pd.Series([True] * n_rows, dtype=bool)
        for mask in conds:
            group &= mask
        total |= group
    return total
