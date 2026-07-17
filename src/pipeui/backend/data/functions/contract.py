"""FunctionContract — the universal function interface carrier (data/functions).

One frozen object describing any function-shaped thing in the app: user ``.py``
functions, user ``.sql`` functions, and (in later phases) built-in steps. It is the
single shape produced by extraction (``domain/functions/discovery``), persisted by
registration, and consumed by binding/execution — replacing the three disconnected
forms the same information used to take (inspection dict → registry rows → loader
``Mapping``s).

Design rules (plan: FunctionContract redesign):
  - **Minimal and fixed.** No consumer-specific fields; built-ins adapt to the
    contract in their own lowering layer, never the reverse.
  - **Derived facts stay derived** (Principle 4). ``function_class`` /
    ``function_return_type`` / ``function_type`` / ``execution_mode`` are properties
    over ``classification.py``'s descriptor table, never stored independently.
    The one carried non-derivable is ``signature`` — the raw ``str(inspect.signature)``
    captured at extraction (its annotation spellings are not reconstructible).
  - **Serializable.** ``to_dict``/``from_dict`` are JSON-safe; ``to_registry_dict``
    emits exactly the legacy registration payload (parity-tested);
    ``from_registry_rows`` re-assembles a contract from ``function_registry`` +
    ``parameter`` rows. Round-tripping through registry rows is
    *derivation-faithful*, not annotation-faithful: the registry stores the
    ``function_return_type`` vocabulary value (e.g. ``boolean``), so a rebuilt
    contract carries that as its ``return_type`` — every derived property is stable
    across the round trip, which is what consumers read.

This module is a DB-free data-layer leaf: it imports only ``classification`` (its
sibling) and holds no connection, no filesystem, no app object.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pipeui.backend.data.functions.classification import (
    binding_kind as _binding_kind,
    derive_function_class,
    derive_function_return_type,
    derive_function_type,
    granularity as _granularity,
)

Engine = Literal["python", "sql"]
ExecutionMode = Literal["table", "column", "row", "value"]

ENGINE_PYTHON: Engine = "python"
ENGINE_SQL: Engine = "sql"


@dataclass(frozen=True)
class ParamContract:
    """One parameter of a contract, in signature order.

    ``position`` is the 0-based signature index — the authoritative parameter order
    (``parameter.position`` in the DB). ``default_value`` is the persisted VARCHAR
    form (``str()`` of the Python default; lossy for ``None``-vs-``"None"`` — kept
    for hash stability, see plan risks).
    """

    name: str
    type_str: str
    position: int
    has_default: bool = False
    default_value: str | None = None

    @property
    def binding_kind(self) -> str:
        """How this param may receive its argument (``classification.binding_kind``)."""
        return _binding_kind(self.type_str)

    @property
    def granularity(self) -> int:
        """§11 granularity index of this param's type."""
        return _granularity(self.type_str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_str": self.type_str,
            "position": self.position,
            "has_default": self.has_default,
            "default_value": self.default_value,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ParamContract":
        return cls(
            name=d["name"],
            type_str=d["type_str"],
            position=d["position"],
            has_default=d.get("has_default", False),
            default_value=d.get("default_value"),
        )


@dataclass(frozen=True)
class FunctionContract:
    """The one interface every function-shaped thing is described by.

    ``engine`` says how the body executes: ``python`` (worker subprocess) or ``sql``
    (DuckDB template — ``body`` carries the SQL text). ``return_type`` is the
    canonical return annotation string for python functions; for sql functions it is
    the return shape implied by the declared header type (``pd.DataFrame`` /
    ``pd.Series[bool]`` / ``unknown``).
    """

    name: str
    engine: Engine
    params: tuple[ParamContract, ...]
    return_type: str
    signature: str
    doc: str | None = None
    source_path: str | None = None
    body: str | None = None

    # -- derived facts (Principle 4 — never stored independently) ---------------

    @property
    def function_class(self) -> str:
        """Class of the least-granular param (§11).

        A param-less contract is whole-table by definition — its only possible
        input is the implicit source frame (the legacy ``.sql`` shape).
        """
        if not self.params:
            return "pd.dataframe"
        return derive_function_class([p.type_str for p in self.params])

    @property
    def function_return_type(self) -> str:
        """CONTEXT.md return-type vocabulary value (e.g. ``bool`` → ``boolean``).

        Idempotent on vocabulary values, so a contract rebuilt from registry rows
        (which store the vocabulary form) derives the same value. ``unknown``
        (untyped ``.sql`` headers) passes through unchanged.
        """
        return derive_function_return_type(self.return_type) or self.return_type

    @property
    def function_type(self) -> str:
        """``transform`` | ``validation`` | ``unknown``.

        ``unknown`` is the one non-derivable case: an untyped ``.sql`` header
        declares no type, and the legacy scanner records ``unknown`` verbatim.
        """
        if self.return_type == "unknown":
            return "unknown"
        return derive_function_type(self.function_return_type)

    def execution_mode(self, column_backed: frozenset[str] = frozenset()) -> ExecutionMode:
        """How a run of this contract consumes data — derived from signature shape.

        ``column_backed`` is the set of param names bound to columns for a given
        step (from the binding; empty = shape-only answer).

          - any ``pd.DataFrame`` param (or no params at all) → ``table``: one call,
            whole working frame.
          - any ``pd.Series`` param → ``column``: vectorized, one call per argument
            bundle, each Series param receiving a whole column.
          - all-scalar signature with at least one column-backed param → ``row``:
            semantically one bound-args set per table row (column-backed params take
            their row's value, literals broadcast). Executors are free to realize
            all N sets as ONE vectorized call — bound args are the semantic model,
            not the execution strategy.
          - all-scalar, nothing column-backed → ``value``: one call.
        """
        types = [p.type_str for p in self.params]
        if not types or "pd.DataFrame" in types:
            return "table"
        if any(t in ("pd.Series", "pd.Series[bool]") for t in types):
            return "column"
        if column_backed and any(p.name in column_backed for p in self.params):
            return "row"
        return "value"

    def bind(self, binding: "Any") -> "list[Any]":
        """Resolve a ``StepBinding`` into ordered ``BoundCall``s (#136).

        Semantics live in ``binding.bind_contract`` (literals → homogeneity check →
        ``pair_bundles`` outer axis); this method is the contract-side entry point.
        Local import: ``binding.py`` imports this module for its types, so the edge
        must point one way at import time.
        """
        from pipeui.backend.data.functions.binding import bind_contract

        return bind_contract(self, binding)

    # -- serialization -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe full form (params nested); inverse of ``from_dict``."""
        return {
            "name": self.name,
            "engine": self.engine,
            "params": [p.to_dict() for p in self.params],
            "return_type": self.return_type,
            "signature": self.signature,
            "doc": self.doc,
            "source_path": self.source_path,
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FunctionContract":
        return cls(
            name=d["name"],
            engine=d["engine"],
            params=tuple(ParamContract.from_dict(p) for p in d["params"]),
            return_type=d["return_type"],
            signature=d["signature"],
            doc=d.get("doc"),
            source_path=d.get("source_path"),
            body=d.get("body"),
        )

    def to_registry_dict(self) -> dict[str, Any]:
        """The registration payload — the legacy inspection-dict keys, unchanged
        (parity-tested against the pre-contract scanner), plus the additive
        ``engine`` / ``function_body`` / ``param_positions`` fields registration
        now persists."""
        params = sorted(self.params, key=lambda p: p.position)
        return {
            "param_names": [p.name for p in params],
            "param_types": [p.type_str for p in params],
            "param_has_default": [p.has_default for p in params],
            "param_default_values": [p.default_value for p in params],
            "param_positions": [p.position for p in params],
            "function_class": self.function_class,
            "function_return_type": self.function_return_type,
            "function_type": self.function_type,
            "function_signature": self.signature,
            "function_doc": self.doc,
            "engine": self.engine,
            "function_body": self.body,
        }

    @classmethod
    def from_registry_rows(
        cls, fn_row: Mapping[str, Any], param_rows: list[Mapping[str, Any]]
    ) -> "FunctionContract":
        """Re-assemble a contract from ``function_registry`` + ``parameter`` rows.

        ``param_rows`` need ``param_name`` / ``param_type`` / ``position`` /
        ``has_default`` / ``default_value``; order is taken from ``position``.
        ``return_type`` carries the registry's vocabulary value — derived
        properties are stable, raw annotation spelling is not (documented above).
        """
        params = tuple(
            ParamContract(
                name=r["param_name"],
                type_str=r["param_type"],
                position=r.get("position", i),
                has_default=r.get("has_default", False),
                default_value=r.get("default_value"),
            )
            for i, r in enumerate(sorted(param_rows, key=lambda r: r.get("position", 0)))
        )
        return cls(
            name=fn_row["function_name"],
            engine=fn_row.get("engine") or ENGINE_PYTHON,
            params=params,
            return_type=fn_row["function_return_type"],
            signature=fn_row["function_signature"],
            doc=fn_row.get("function_doc"),
            source_path=fn_row.get("module_path"),
            body=fn_row.get("function_body"),
        )
