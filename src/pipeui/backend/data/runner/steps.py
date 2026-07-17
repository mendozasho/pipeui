"""StepContext (L0) — the typed, logic-free description of one pipeline step.

A step's execution shape is assembled by the loader (``step_loader.fetch_steps``
over ``source_function_map`` / ``function_set_map`` / ``parameter`` …, and
``get_builtin_steps`` over ``source_builtin_map``). This module turns each fetched
row into a **frozen, typed** carrier — the sole legal shape crossing the
``step_loader → run / executors`` boundary (CONTEXT.md → Module contracts).

The carrier is **variant-typed**, not a ``data`` dict:
  - ``StepContext`` — the base (``step_type`` + ``position``).
  - ``FunctionStepContext`` — a function step (a function set). Built via
    ``from_function`` (tags ``FUNCTION``) or ``from_set`` (tags ``SET`` for the
    set adapter); both carry the same fields, only the dispatch tag differs.
  - ``BuiltinStepContext`` — a built-in step. Built via ``from_builtin``.
  - ``FunctionSpec`` — one function member of a set.

``params`` (on ``FunctionSpec``) and ``builtin_config`` (on ``BuiltinStepContext``)
stay typed ``Mapping`` — the agreed depth boundary. Inner ``param["…"]`` /
``cfg.get("…")`` access is unchanged; the carrier is typed only at the step level.

Factories are the only legal constructors (the variant returned **is** the
contract): ``from_function`` / ``from_set`` → ``FunctionStepContext``;
``from_builtin`` → ``BuiltinStepContext``. ``step_loader`` is the sole producer —
it reads each map table and calls the matching factory directly (function-map rows
→ ``from_set``; built-in-map rows → ``from_builtin``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pipeui.backend.data.functions.binding import ParamBinding, StepBinding
from pipeui.backend.data.functions.contract import FunctionContract, ParamContract

FUNCTION = "function"
BUILTIN = "builtin"
SET = "set"


@dataclass(frozen=True)
class FunctionSpec:
    """One function member of a function step (a set member).

    ``params`` is the typed depth boundary — a tuple of ``Mapping`` param rows the
    executor reads by key (``param["param_name"]`` etc.); it is not deepened into a
    dataclass. ``output_mode`` / ``append_name`` / ``output_targets`` are this
    function's own output config (#264; legacy step-level fallback resolved by the
    loader). ``step_type`` lets the set adapter dispatch each member by its own type
    (today always ``FUNCTION``; a built-in member is #41).
    """

    function_id: str
    function_name: str
    function_type: str
    function_class: str
    function_return_type: str
    module_path: str
    params: tuple[Mapping[str, Any], ...]
    output_mode: str | None
    append_name: str | None
    output_targets: tuple[str, ...]
    step_type: str = FUNCTION
    # #136: the universal interface + this source's persisted binding, assembled by
    # the loader from the same rows ``params`` carries. The executors consume these
    # via ``contract_binding()``.
    contract: FunctionContract | None = None
    binding: StepBinding | None = None

    def contract_binding(self) -> tuple[FunctionContract, StepBinding]:
        """Return this member's (contract, binding), deriving them from the legacy
        ``params`` rows when the spec was built without loader hydration (direct
        construction in tests / adapters). The derived pair carries exactly the
        information the executors need to bind — param types, order, defaults,
        bindings, scalar values."""
        if self.contract is not None and self.binding is not None:
            return self.contract, self.binding
        params = list(self.params)
        contract = FunctionContract(
            name=self.function_name,
            engine="python",
            params=tuple(
                ParamContract(
                    name=p["param_name"],
                    type_str=p["param_type"],
                    position=p.get("position") if p.get("position") is not None else i,
                    has_default=bool(p.get("has_default")),
                    default_value=p.get("default_value"),
                )
                for i, p in enumerate(params)
            ),
            return_type=self.function_return_type or "pd.Series",
            signature="",
            source_path=self.module_path or None,
        )
        binding = StepBinding(params=tuple(
            ParamBinding(
                param_name=p["param_name"],
                kind=(
                    "table" if p["param_type"] == "pd.DataFrame"
                    else "source_ref" if p["param_type"] == "source_ref"
                    else "columns" if p.get("bindings")
                    else "literal"
                ),
                columns=tuple(p.get("bindings") or ()),
                value=p.get("scalar_value"),
                source_ref=(
                    p.get("scalar_value") if p["param_type"] == "source_ref" else None
                ),
            )
            for p in params
        ))
        return contract, binding


@dataclass(frozen=True)
class StepContext:
    """Base: one step's dispatch discriminator and ordering key.

    ``step_type`` is what the step-type registry keys on; ``position`` is the shared
    ordering key across both map tables. The concrete variant returned by a factory
    is the contract — base ``StepContext`` is never constructed directly for a run.
    """

    step_type: str
    position: int

    # -- factory constructors over the existing map-table rows -----------------
    @classmethod
    def from_function(cls, step: Mapping[str, Any]) -> "FunctionStepContext":
        """Build a function-step context (tagged ``FUNCTION``) from a loader row."""
        return _function_context(step, FUNCTION)

    @classmethod
    def from_set(cls, step: Mapping[str, Any]) -> "FunctionStepContext":
        """Build a function-step context tagged ``SET`` (routes to the set adapter).

        Every function step IS a set (its members live in ``function_set_map``), so a
        function-map row is tagged ``SET`` and routes to the function-set adapter,
        which re-dispatches each member as ``FUNCTION``. The wrapped data is identical
        to ``from_function``'s — only the dispatch tag differs."""
        return _function_context(step, SET)

    @classmethod
    def from_builtin(cls, step: Mapping[str, Any]) -> "BuiltinStepContext":
        """Build a built-in-step context from a ``source_builtin_map`` loader row."""
        return BuiltinStepContext(
            step_type=BUILTIN,
            position=step["position"],
            step_id=step["step_id"],
            builtin_type=step["builtin_type"],
            builtin_config=step["builtin_config"],
        )


@dataclass(frozen=True)
class FunctionStepContext(StepContext):
    """A function step (a function set) and its members.

    Carries the ``source_function_map`` row's identity (``source_function_map_id`` /
    ``set_id`` / ``set_name``), its members as a tuple of ``FunctionSpec``, and the
    step-level output config (legacy fallback for ``FunctionSpec``-level config)."""

    source_function_map_id: str = ""
    set_id: str = ""
    set_name: str = ""
    functions: tuple[FunctionSpec, ...] = ()
    output_mode: str | None = None
    append_name: str | None = None
    output_targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuiltinStepContext(StepContext):
    """A built-in step.

    ``builtin_config`` stays a typed ``Mapping`` — the depth boundary the built-in
    executor reads by key (``cfg.get("…")``)."""

    step_id: str = ""
    builtin_type: str = ""
    builtin_config: Mapping[str, Any] = None  # type: ignore[assignment]


def _function_spec(member: Mapping[str, Any]) -> FunctionSpec:
    """Build a ``FunctionSpec`` from one loader function row (or set member)."""
    return FunctionSpec(
        function_id=member["function_id"],
        function_name=member["function_name"],
        function_type=member["function_type"],
        function_class=member.get("function_class", ""),
        function_return_type=member.get("function_return_type", ""),
        module_path=member.get("module_path", ""),
        params=tuple(member.get("params", ())),
        output_mode=member.get("output_mode"),
        append_name=member.get("append_name"),
        output_targets=tuple(member.get("output_targets", ())),
        step_type=member.get("step_type", FUNCTION),
        contract=member.get("contract"),
        binding=member.get("binding"),
    )


def _function_context(step: Mapping[str, Any], step_type: str) -> FunctionStepContext:
    """Build a ``FunctionStepContext`` (tagged ``step_type``) from a loader row.

    Members already typed as ``FunctionSpec`` (the set adapter's per-member rewrap)
    pass through unchanged; raw loader dicts are converted via ``_function_spec``."""
    functions = tuple(
        f if isinstance(f, FunctionSpec) else _function_spec(f)
        for f in step.get("functions", ())
    )
    return FunctionStepContext(
        step_type=step_type,
        position=step["position"],
        source_function_map_id=step["source_function_map_id"],
        set_id=step["set_id"],
        set_name=step["set_name"],
        functions=functions,
        output_mode=step.get("output_mode"),
        append_name=step.get("append_name"),
        output_targets=tuple(step.get("output_targets", ())),
    )
