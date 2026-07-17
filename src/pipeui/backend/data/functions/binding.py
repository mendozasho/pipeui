"""Binding carriers + bound-args semantics for FunctionContract (data/functions). #136.

``StepBinding`` is the in-memory shape of one function's persisted binding on a source
(``alias_map`` columns + ``source_scalar_map`` literals + Python defaults).
``FunctionContract.bind(binding)`` turns it into ordered ``BoundCall``s — the semantic
model of a run:

  - **Multi-select is the outer axis**: every column-backed param's ordered columns go
    through ``pair_bundles`` (varying/static/broadcast, equal-length-among-varying);
    c columns tied to a param → c ``BoundCall``s.
  - **Bound args are the inner, per-row semantics**: for a ``row``-mode call over an
    N-row table, ``iter_row_args`` yields N argument sets — each column-backed param
    takes its row's value (pandas NULL sentineled to ``None``), each literal broadcasts
    into every set. Executors are free to realize all N sets as ONE vectorized worker
    call — the semantics live here, the strategy lives in the executor.

``bind()`` is frame-free and pure: it resolves literals (persisted value → Python
default → ``RequiredParamError``), enforces shape homogeneity (``MixedShapeError``),
and pairs columns — no pandas data touched until a ``BoundCall`` meets a frame.

This module is the canonical home of ``RequiredParamError`` / ``coerce_scalar`` /
``MixedShapeError`` / ``composite_key`` — the executors and ``realize`` import them
from here.

Layer: data-layer leaf beside ``contract.py``; imports only its published peers
(``data/runner/bundles``). No DB, no worker, no app object.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Literal, Mapping
from types import MappingProxyType

from pipeui.backend.data.runner.bundles import ArgumentBundle, pair_bundles

if TYPE_CHECKING:  # pragma: no cover — type-only; avoids a contract ⇄ binding cycle
    import pandas as pd
    from pipeui.backend.data.functions.contract import FunctionContract

BindingKind = Literal["columns", "literal", "table", "source_ref"]

# Scalar-shaped param types (bound to a column they receive per-row values; unbound
# they resolve to a literal). The one vocabulary the executors and realize key off.
# `date` (#140) is sql-engine-only — .py scans cannot declare it, so a date literal
# never crosses the worker's JSON extra_kwargs boundary.
SCALAR_TYPES = ("str", "int", "float", "bool", "date")


class RequiredParamError(Exception):
    """A scalar param has no persisted value and no Python default — the function
    cannot run. Surfaced as a failed RunResult the frontend can pick up."""

    def __init__(self, param_name: str):
        self.param_name = param_name
        super().__init__(
            f"parameter '{param_name}' is required but no value or default was provided"
        )


class MixedShapeError(Exception):
    """A function mixes ``pd.Series`` and scalar-shaped column-backed params. The two
    dispatch models (once-per-bundle vs once-per-row) are incompatible in a single call,
    so the run is rejected before any worker is spawned."""


def coerce_scalar(value: str, param_type: str):
    """Coerce a source_scalar_map / default_value VARCHAR to the param's Python type."""
    if param_type == "int":
        return int(value)
    if param_type == "float":
        return float(value)
    if param_type == "bool":
        return str(value).strip().lower() in ("true", "1", "yes")
    if param_type == "date":
        import datetime

        return datetime.date.fromisoformat(str(value).strip())
    return value  # str


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


# ---------------------------------------------------------------------------
# Carriers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamBinding:
    """One param's binding: ordered columns, a literal, the whole table, or (Phase 4)
    a source reference. ``columns`` order is ``alias_map.position`` order — the user's
    placed order, which drives bundle pairing."""

    param_name: str
    kind: BindingKind
    columns: tuple[str, ...] = ()
    value: str | None = None          # persisted literal (VARCHAR); coerced at bind
    source_ref: str | None = None     # Phase 4 (sql engine): referenced source_id


@dataclass(frozen=True)
class StepBinding:
    """One function's full binding on a source, in loader param order.

    NOTE (parity): param order is the step loader's order — alphabetical by
    ``param_name`` today. Phase 3 flips loader ordering to ``parameter.position``
    (signature order); pairing follows whatever order this carries.
    """

    params: tuple[ParamBinding, ...] = ()

    def get(self, param_name: str) -> ParamBinding | None:
        for p in self.params:
            if p.param_name == param_name:
                return p
        return None


@dataclass(frozen=True)
class BoundCall:
    """One argument bundle, fully resolved — the semantic unit of execution.

    ``column_kwargs`` maps each column-backed param to its bound source column for
    this bundle; ``literal_kwargs`` are the coerced broadcast scalars;
    ``table_params`` receive the whole working frame. ``mode`` is the contract's
    ``execution_mode`` under this binding.
    """

    bundle_key: str
    mode: str  # ExecutionMode: table | column | row | value
    column_kwargs: Mapping[str, str] = MappingProxyType({})
    literal_kwargs: Mapping[str, Any] = MappingProxyType({})
    table_params: tuple[str, ...] = ()
    source_refs: Mapping[str, str] = MappingProxyType({})

    def iter_row_args(self, frame: "pd.DataFrame") -> Iterator[dict[str, Any]]:
        """Yield the per-row argument sets — the bound-args semantic spec.

        For an N-row frame: N dicts, one per row, where each column-backed param
        carries its row's cell (pandas NULL sentineled to ``None``, matching the
        scalar-run wrapper) and every literal is broadcast into each set. An empty
        frame yields nothing. Executors realize these vectorized; this iterator is
        the meaning, not the strategy.
        """
        import pandas as pd  # local: keep module import-light for pure-binding users

        cols = {name: frame[col] for name, col in self.column_kwargs.items()}
        for i in range(len(frame)):
            args = {
                name: (None if pd.isna(series.iloc[i]) else series.iloc[i])
                for name, series in cols.items()
            }
            args.update(self.literal_kwargs)
            yield args


# ---------------------------------------------------------------------------
# bind() — the contract's binding resolution (called via FunctionContract.bind)
# ---------------------------------------------------------------------------

def bind_contract(contract: "FunctionContract", binding: StepBinding) -> list[BoundCall]:
    """Resolve a persisted binding into ordered ``BoundCall``s (frame-free, pure).

    1. **Literals first**: every scalar param with no columns takes its persisted
       value, else its Python default, else raises ``RequiredParamError``; values are
       coerced to the param's type.
    2. **Shape homogeneity**: column-backed scalar-shaped params and ``pd.Series``
       params cannot mix (``MixedShapeError``) — one call cannot dispatch both
       once-per-bundle and once-per-row.
    3. **Outer axis**: every column-backed param's ordered columns feed
       ``pair_bundles`` (in ``binding.params`` order — parity with the executor's
       loader-order feed); ``BundleLengthError`` propagates. Each bundle becomes one
       ``BoundCall`` keyed by ``composite_key``.
    """
    by_name = {p.name: p for p in contract.params}

    literal_kwargs: dict[str, Any] = {}
    column_backed: list[dict] = []   # pair_bundles feed, in binding order
    table_params: list[str] = []
    source_refs: dict[str, str] = {}
    series_backed: list[str] = []
    scalar_backed: list[str] = []

    for pb in binding.params:
        pc = by_name.get(pb.param_name)
        if pc is None:
            continue  # binding row for a param the contract no longer has
        if pc.type_str == "pd.DataFrame":
            table_params.append(pc.name)
            continue
        if pc.type_str == "source_ref":
            ref = pb.source_ref if pb.source_ref is not None else pb.value
            if ref is None:
                raise RequiredParamError(pc.name)
            source_refs[pc.name] = ref
            continue
        if pb.columns:
            column_backed.append({"param_id": pc.name, "columns": list(pb.columns)})
            (scalar_backed if pc.type_str in SCALAR_TYPES else series_backed).append(pc.name)
            continue
        if pc.type_str in SCALAR_TYPES:
            raw = pb.value
            if raw is None and pc.has_default:
                raw = pc.default_value
            if raw is None:
                raise RequiredParamError(pc.name)
            literal_kwargs[pc.name] = coerce_scalar(raw, pc.type_str)
        # unbound pd.Series: no binding to resolve — same as the executor arms, the
        # missing argument surfaces at call time, not here.

    if series_backed and scalar_backed:
        raise MixedShapeError(
            "cannot mix pd.Series and scalar (str/int/float/bool) column-backed params "
            f"in one function ({', '.join(series_backed)} vs {', '.join(scalar_backed)}) "
            "— split them into separate functions"
        )

    mode = contract.execution_mode(
        column_backed=frozenset(scalar_backed) | frozenset(series_backed)
    )

    bundles = pair_bundles(column_backed) if column_backed else [ArgumentBundle()]
    calls: list[BoundCall] = []
    for bundle in bundles:
        calls.append(BoundCall(
            bundle_key=composite_key(bundle, column_backed),
            mode=mode,
            column_kwargs=MappingProxyType({
                p["param_id"]: bundle.columns[p["param_id"]] for p in column_backed
            }),
            literal_kwargs=MappingProxyType(dict(literal_kwargs)),
            table_params=tuple(table_params),
            source_refs=MappingProxyType(dict(source_refs)),
        ))
    return calls
