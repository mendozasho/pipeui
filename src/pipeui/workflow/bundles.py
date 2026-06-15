"""Pure argument-bundle pairing — the load-bearing seam of multi-select execution.

This module is **pure**: no DuckDB, no worker, no app state. It turns a function's
per-parameter ordered column bindings into an ordered list of `argument bundle`s, or
raises a validation error — so the bundle model (CLAUDE_REFERENCE §12, ADR-0001) gets
exhaustive `unit` coverage independent of the executor, and the *same* function is reused
end-to-end by `workflow/run.py`.

Model (CONTEXT.md → argument bundle / static param / multi_select_eligible):
  - The runner pairs the bound columns of every `multi_select_eligible` parameter
    **positionally**, in user-placed order (the `position` on `alias_map`): bundle ``i``
    takes each **varying param**'s ``i``-th column.
  - A **static param** (bound to exactly one column) **broadcasts** its single column into
    every bundle.
  - All *varying* params (>1 bound column) must share one length ``N`` — the
    **equal-length-among-varying** rule. ``N`` is the bundle count. Two or more distinct
    lengths among the varying params is rejected (no silent zip-shortest truncation).
  - The single-column / all-static path is the ``N = 1`` special case.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BundleLengthError(ValueError):
    """Unequal column counts among varying parameters (the equal-length rule violated).

    A ``ValueError`` subclass so callers that already translate ``ValueError`` into a
    structured 4xx failure (rather than a 500) catch it without new handling.
    """


@dataclass(frozen=True)
class ArgumentBundle:
    """One positionally-paired group of column arguments for a single run.

    ``columns`` maps ``param_id`` -> the bound column for this bundle (a varying param's
    ``i``-th column, or a static param's single broadcast column). ``varying_columns`` is
    the ordered list of columns contributed by *varying* params only — it drives the
    result label, so a card reads as the column that actually varies across bundles.
    """

    columns: dict[str, object] = field(default_factory=dict)
    varying_columns: list[object] = field(default_factory=list)


def pair_bundles(params: list[dict]) -> list[ArgumentBundle]:
    """Pair per-parameter ordered column bindings into an ordered list of bundles.

    ``params`` is an ordered list of dicts, each ``{"param_id": <id>, "columns": [col, ...]}``
    where ``columns`` is already in user-placed (``alias_map.position``) order. A param
    with zero columns contributes nothing (e.g. a pd.DataFrame param resolved elsewhere).

    Returns an ordered list of :class:`ArgumentBundle` (length ``N``). Raises
    :class:`BundleLengthError` when two or more *varying* params (len > 1) disagree on ``N``.
    """
    # Split into varying (>1) and static (==1); empty params contribute nothing.
    varying: list[dict] = []
    static: list[dict] = []
    for p in params:
        cols = p.get("columns") or []
        if len(cols) > 1:
            varying.append(p)
        elif len(cols) == 1:
            static.append(p)
        # len == 0 → no binding to pair; skipped.

    # Equal-length-among-varying rule. Length-1 params broadcast and never participate
    # in this check (they are static, by construction above).
    varying_lengths = {len(p["columns"]) for p in varying}
    if len(varying_lengths) > 1:
        counts = sorted(varying_lengths)
        raise BundleLengthError(
            "Varying parameters bind different column counts "
            f"({', '.join(str(c) for c in counts)}); all varying parameters must bind "
            "the same number of columns (a single-column parameter broadcasts)."
        )

    # N = the shared varying length, or 1 when there are no varying params.
    n = varying_lengths.pop() if varying_lengths else 1

    bundles: list[ArgumentBundle] = []
    for i in range(n):
        columns: dict[str, object] = {}
        varying_cols: list[object] = []
        for p in varying:
            col = p["columns"][i]
            columns[p["param_id"]] = col
            varying_cols.append(col)
        for p in static:
            columns[p["param_id"]] = p["columns"][0]
        bundles.append(ArgumentBundle(columns=columns, varying_columns=varying_cols))

    return bundles
