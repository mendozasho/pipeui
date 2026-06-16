"""StepContext — the typed object carrying one pipeline step's execution
properties (the keys the step dict held), built via factory classmethods from the
existing map tables (CONTEXT.md -> StepContext; PRD Implementation Decisions ->
class-based Step/StepContext with factory constructors over the existing map
tables).

A step's execution shape is already assembled by the runner's fetchers
(``_fetch_steps`` over ``source_function_map`` / ``function_set_map`` ... and
``get_builtin_steps`` over ``source_builtin_map``). ``StepContext`` wraps that
fetched row so executors read the same fields the inline dispatch read — this is a
behavior-preserving refactor: it changes the *shape of dispatch*, not the data.

Factory classmethods (one per origin):
  - ``from_function`` / ``from_set`` — a ``source_function_map`` row (the function
    step; ``from_set`` is the set-origin name for the same path).
  - ``from_builtin`` — a ``source_builtin_map`` row (a join/pivot/filter step).

``step_type`` is the discriminator the step-type registry keys on; ``position`` is
the shared ordering key across both map tables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FUNCTION = "function"
BUILTIN = "builtin"
SET = "set"


@dataclass(frozen=True)
class StepContext:
    """One step's execution properties, tagged by ``step_type``.

    ``data`` is the fetched step dict (the exact keys the inline loop read); the
    context exposes it read-only via ``get`` / ``__getitem__`` so executors are
    decoupled from the dict literal while preserving behavior. ``position`` is
    lifted out for the runner's stable position sort.
    """

    step_type: str
    position: int
    data: dict[str, Any] = field(default_factory=dict)

    # -- read-through accessors (executors talk to the context, not the raw dict) --
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    # -- factory constructors over the existing map-table rows -----------------
    @classmethod
    def from_function(cls, step: dict) -> "StepContext":
        """Build a context from a ``source_function_map`` (function) step dict."""
        return cls(step_type=FUNCTION, position=step["position"], data=dict(step))

    @classmethod
    def from_set(cls, step: dict) -> "StepContext":
        """Build a context from a ``source_function_map`` row treated as a set.

        Every function step in this schema IS a set (its members live in
        ``function_set_map``), so a function-map row is tagged ``SET`` and routes to
        the function-set adapter, which flattens the set into per-member executions
        dispatched through the registry by each member's step type (slice 4). The
        wrapped data is identical to ``from_function``'s — only the dispatch tag
        differs (``SET`` -> adapter; the adapter re-dispatches each member as
        ``FUNCTION`` -> the per-member function executor)."""
        return cls(step_type=SET, position=step["position"], data=dict(step))

    @classmethod
    def from_builtin(cls, step: dict) -> "StepContext":
        """Build a context from a ``source_builtin_map`` (join/pivot/filter) row."""
        return cls(step_type=BUILTIN, position=step["position"], data=dict(step))

    @classmethod
    def for_step(cls, step: dict) -> "StepContext":
        """Pick the factory by the fetched step's ``step_type`` — the single entry
        the runner calls so no ``if/elif`` on step type survives in the loop. A
        built-in row routes to ``from_builtin``; any other (function-map) row is a
        set and routes to ``from_set`` (which tags ``SET`` for the adapter)."""
        if step.get("step_type") == BUILTIN:
            return cls.from_builtin(step)
        return cls.from_set(step)
