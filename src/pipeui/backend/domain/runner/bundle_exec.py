"""Multi-parameter argument-bundle execution (L3 — runner execution mechanics).

When a function binds MORE THAN ONE column-backed parameter, the single-param executor
path can't express it: it selects one bound param and drops the rest. This module runs
the general case — it feeds *every* column-backed param through ``pair_bundles`` (which
already implements the varying/static/broadcast model, CLAUDE_REFERENCE §12 / ADR-0001)
and delivers each bundle's per-param columns to the user function.

The transport trick keeps the process-isolation worker (``worker.py``, §10) untouched:
the bundle's column-backed columns are packed into ONE ``pd.DataFrame`` (keyed by
``param_name``) and passed through the existing single-arg ``call_function``; a small
generated wrapper prepended to the user source unpacks the frame back into per-param
kwargs. A ``pd.DataFrame`` is already a first-class packable Arrow arg, so no new wire
framing is added.

Split out of ``executors.py`` so the validation and transform arms share ONE
implementation and cannot drift. Layer: imports ``pair_bundles`` from ``data/runner``
(down) and ``call_function`` from the ``domain/runner`` worker sibling; nothing in
``data/runner`` imports this.

Two dispatch shapes (a function's column-backed params must be homogeneous):
  - all ``pd.Series`` params → the whole column per param; fn called ONCE per bundle.
  - all scalar-shaped params (str/int/float/bool bound) → per-row dispatch (the
    "scalar run"); fn called once per ROW per bundle.
Mixing the two is rejected (``MixedShapeError``) — you cannot call fn once (Series) and
per-row (scalar) at the same time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.data.runner.bundles import ArgumentBundle, pair_bundles
from pipeui.backend.domain.runner.worker import call_function

# Scalar-shaped param types: bound to a column, they receive a per-row value (the scalar
# run), unlike a pd.Series param which receives the whole column.
_SCALAR_TYPES = ("str", "int", "float", "bool")

# The keyword the packed DataFrame is delivered under. Prefixed/suffixed to avoid
# colliding with a real user param name (same latent risk build_scalar_wrapper carries).
_FRAME_KWARG = "__pipeui_frame__"


class MixedShapeError(Exception):
    """A function mixes ``pd.Series`` and scalar-shaped column-backed params. The two
    dispatch models (once-per-bundle vs once-per-row) are incompatible in a single call,
    so the run is rejected before any worker is spawned."""


@dataclass(frozen=True)
class BundleOutcome:
    """One bundle's result. ``key`` is the composite bundle_key/label seed; ``result``
    is the worker's return (``pd.Series`` / scalar) or a ``FailedFunctionEntry``."""

    key: str
    result: Any


def collect_column_backed_params(params: list[dict]) -> list[dict]:
    """Return every column-backed param — one with non-empty ``bindings`` whose type is a
    column-eligible shape (``pd.Series``, or a scalar-shaped str/int/float/bool bound to a
    column). ``pd.DataFrame`` params (whole-table) and unbound scalars are excluded.

    Order is preserved from ``params`` — the step loader orders params alphabetically by
    ``param_name`` (``ORDER BY p.param_name``), so the pairing feed is deterministic.
    """
    return [
        p for p in params
        if p.get("bindings") and p["param_type"] in ("pd.Series", *_SCALAR_TYPES)
    ]


def composite_key(bundle: ArgumentBundle, ordered_params: list[dict]) -> str:
    """Stable, unique-per-bundle key seed for a multi-param bundle.

    Uses the bundle's *varying* columns when any param varies (mirrors the single-column
    semantics — the varying column is the label; broadcast/static params don't perturb
    it). When every param is static (the single N=1 bundle), joins every param's bound
    column so the one bundle still gets a descriptive, stable key. The separator is the
    unit separator (``\\x1f``) so no real column name can contain it; ``normalize_label``
    collapses it for display.
    """
    if bundle.varying_columns:
        return "\x1f".join(str(c) for c in bundle.varying_columns)
    return "\x1f".join(str(bundle.columns[p["param_id"]]) for p in ordered_params)


def build_series_frame_wrapper(fn_name: str) -> str:
    """Codegen a wrapper that unpacks the packed frame into per-param ``pd.Series`` kwargs
    and calls the user function ONCE. Each frame column is already named by ``param_name``,
    so the split is keyword-correct (§12). No null-sentinel — a ``pd.Series`` param
    receives the raw column, matching the single-param ``pd.Series`` path."""
    return (
        f"def __wrapper__({_FRAME_KWARG}, **__extra):\n"
        f"    return {fn_name}(**{{__c: {_FRAME_KWARG}[__c] for __c in {_FRAME_KWARG}.columns}}, **__extra)\n"
    )


def build_scalar_frame_wrapper(fn_name: str) -> str:
    """Codegen a wrapper that dispatches a scalar-shaped function per ROW across the packed
    frame (the scalar run), zipping every column-backed param's cell into one call. Pandas
    NULL is null-sentineled to ``None`` per cell so user functions get a proper null.

    Guards the empty frame: ``DataFrame.apply(axis=1)`` on 0 rows returns a DataFrame (not
    a Series), which would break downstream normalization — so an empty Series is returned
    explicitly. Mirrors ``build_scalar_wrapper``'s per-value null-sentinel, generalized to
    every cell in the row."""
    return (
        "import pandas as _pd\n"
        f"def __wrapper__({_FRAME_KWARG}, **__extra):\n"
        f"    if len({_FRAME_KWARG}) == 0:\n"
        "        return _pd.Series([], dtype=object)\n"
        "    def __row(__r):\n"
        f"        return {fn_name}(**{{__c: (None if _pd.isna(__r[__c]) else __r[__c]) for __c in __r.index}}, **__extra)\n"
        f"    return {_FRAME_KWARG}.apply(__row, axis=1)\n"
    )


def run_multi_param_bundles(
    *,
    fn_source: str,
    fn_name: str,
    column_backed_params: list[dict],
    source_frame: pd.DataFrame,
    extra_kwargs: dict | None = None,
) -> list[BundleOutcome]:
    """Run a function whose column-backed params span 2+ params, once per argument bundle,
    delivering every param's bundle column.

    Partitions the params into ``pd.Series`` vs scalar-shaped; mixing raises
    ``MixedShapeError``. Calls ``pair_bundles`` (may raise ``BundleLengthError``) to get
    the ordered bundles, picks the matching wrapper, and per bundle packs each param's
    column into a DataFrame keyed by ``param_name`` (assigned by key — never
    select-then-rename — so two params binding the same source column don't collide). A
    bundle whose column is missing from the frame yields a ``FailedFunctionEntry`` for
    that bundle; the remaining bundles still run.
    """
    series_params = [p for p in column_backed_params if p["param_type"] == "pd.Series"]
    scalar_params = [p for p in column_backed_params if p["param_type"] in _SCALAR_TYPES]
    if series_params and scalar_params:
        raise MixedShapeError(
            "cannot mix pd.Series and scalar (str/int/float/bool) column-backed params "
            f"in one function ({', '.join(p['param_name'] for p in series_params)} vs "
            f"{', '.join(p['param_name'] for p in scalar_params)}) — split them into "
            "separate functions"
        )

    all_scalar = bool(scalar_params)
    bundles = pair_bundles([
        {"param_id": p["param_id"], "columns": list(p["bindings"])}
        for p in column_backed_params
    ])
    wrapper = (
        build_scalar_frame_wrapper(fn_name) if all_scalar
        else build_series_frame_wrapper(fn_name)
    )

    outcomes: list[BundleOutcome] = []
    for bundle in bundles:
        key = composite_key(bundle, column_backed_params)
        frame_data: dict[str, pd.Series] = {}
        missing: str | None = None
        for p in column_backed_params:
            col = bundle.columns[p["param_id"]]
            if col not in source_frame.columns:
                missing = col
                break
            frame_data[p["param_name"]] = source_frame[col].reset_index(drop=True)
        if missing is not None:
            fail = FailedFunctionEntry()
            fail.add(
                fn_name,
                f"bound column '{missing}' not found in source data — detach and "
                "re-attach the function to refresh the binding",
            )
            outcomes.append(BundleOutcome(key=key, result=fail))
            continue
        frame = pd.DataFrame(frame_data)
        result = call_function(
            wrapper + "\n" + fn_source, "__wrapper__", _FRAME_KWARG, frame,
            extra_kwargs=extra_kwargs,
        )
        outcomes.append(BundleOutcome(key=key, result=result))
    return outcomes
