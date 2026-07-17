"""BoundCall realization (L3 — runner execution mechanics). #136 → Phase 3.

``realize`` is the single seam between the contract's bound-args semantics and the
process-isolation worker: it takes ONE ``BoundCall`` (produced by
``FunctionContract.bind``) and executes it against a frame, returning the worker's
result or a ``FailedFunctionEntry``.

Realization strategy per ``BoundCall.mode`` (bound args are the *semantics*; this
module owns the *strategy*):

  - ``column`` — every column-backed param's bound column is packed into ONE
    ``pd.DataFrame`` keyed by ``param_name`` and delivered through the existing
    single-arg ``call_function``; a generated wrapper unpacks the frame into
    per-param ``pd.Series`` kwargs and calls the user function ONCE.
  - ``row`` — same packing; the wrapper dispatches per ROW (the scalar run),
    null-sentineling pandas NULL to ``None`` per cell — the vectorized realization
    of ``BoundCall.iter_row_args``'s N per-row argument sets.
  - ``table`` — the whole working frame under the ``pd.DataFrame`` param's name.
  - ``value`` — the legacy no-bound-column quirk, made explicit: the whole frame is
    passed under the first param's name (flagged for cleanup in the plan).

The transport keeps the worker (``worker.py``, §10) untouched: a packed frame is a
first-class Arrow arg, resolved literals travel as ``extra_kwargs`` JSON, and the
wrapper is prepended source — no new wire framing. (This module is the Phase-3
refactor of ``bundle_exec.py``: the per-bundle loop moved up into the executors,
which iterate ``contract.bind(...)`` and call ``realize`` per ``BoundCall``.)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.domain.runner.worker import call_function

if TYPE_CHECKING:  # pragma: no cover
    from pipeui.backend.data.functions.binding import BoundCall
    from pipeui.backend.data.functions.contract import FunctionContract

# The keyword the packed DataFrame is delivered under. Prefixed/suffixed to avoid
# colliding with a real user param name.
_FRAME_KWARG = "__pipeui_frame__"


def build_series_frame_wrapper(fn_name: str) -> str:
    """Codegen a wrapper that unpacks the packed frame into per-param ``pd.Series`` kwargs
    and calls the user function ONCE. Each frame column is already named by ``param_name``,
    so the split is keyword-correct (§12). No null-sentinel — a ``pd.Series`` param
    receives the raw column, matching the historical single-param ``pd.Series`` path."""
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
    explicitly."""
    return (
        "import pandas as _pd\n"
        f"def __wrapper__({_FRAME_KWARG}, **__extra):\n"
        f"    if len({_FRAME_KWARG}) == 0:\n"
        "        return _pd.Series([], dtype=object)\n"
        "    def __row(__r):\n"
        f"        return {fn_name}(**{{__c: (None if _pd.isna(__r[__c]) else __r[__c]) for __c in __r.index}}, **__extra)\n"
        f"    return {_FRAME_KWARG}.apply(__row, axis=1)\n"
    )


def realize(
    contract: "FunctionContract",
    call: "BoundCall",
    *,
    fn_source: str,
    frame: pd.DataFrame,
) -> Any:
    """Execute one ``BoundCall`` against ``frame``; return the worker result or a
    ``FailedFunctionEntry``.

    ``frame`` is the frame this call reads its inputs from — the executor decides
    which (the pre-step copy for transforms, the original for validations). A bound
    column missing from the frame fails this call with the refresh-the-binding
    diagnostic; the executor decides whether that fails the step or just the call.
    """
    fn_name = contract.name
    extra = dict(call.literal_kwargs)

    if call.mode == "table":
        kwarg = call.table_params[0] if call.table_params else "df"
        return call_function(fn_source, fn_name, kwarg, frame, extra_kwargs=extra)

    if call.mode == "value":
        kwarg = contract.params[0].name if contract.params else "data"
        return call_function(fn_source, fn_name, kwarg, frame, extra_kwargs=extra)

    # column / row: pack each bound column into one frame keyed by param_name
    # (assigned by key — never select-then-rename — so two params binding the same
    # source column don't collide).
    frame_data: dict[str, pd.Series] = {}
    for param_name, col in call.column_kwargs.items():
        if col not in frame.columns:
            fail = FailedFunctionEntry()
            fail.add(
                fn_name,
                f"bound column '{col}' not found in source data — detach and "
                "re-attach the function to refresh the binding",
            )
            return fail
        frame_data[param_name] = frame[col].reset_index(drop=True)
    packed = pd.DataFrame(frame_data)

    wrapper = (
        build_scalar_frame_wrapper(fn_name) if call.mode == "row"
        else build_series_frame_wrapper(fn_name)
    )
    return call_function(
        wrapper + "\n" + fn_source, "__wrapper__", _FRAME_KWARG, packed,
        extra_kwargs=extra,
    )
