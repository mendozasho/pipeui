"""RunResult — the single backend source of truth for runner result data.

A ``RunResult`` holds the outcome of **one** normalized run (one scalar-run vector for
one argument bundle): its status, the function/source/bundle metadata, a readable
normalized label, and a deterministic identity = a shortened ``UUID5(function,
argument bundle, source)``. ``ValidationRunResult`` specializes it with pass/fail
counts. It replaces the bare result dicts the runner returned before, and is kept a
focused result-holder — not a catch-all (CONTEXT.md → RunResult; PRD Implementation
Decisions; CLAUDE_REFERENCE §2 for the two-level uuid5 identity).

``normalize_label`` is the pure label-normalization util: it strips leading
underscores and odd (non-alphanumeric) tokens and never returns an empty label, so
export columns and result cards stay well-formed.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from pipeui.ids import content_hash_id

# Table-namespace key for the RunResult identity (CLAUDE_REFERENCE §2 two-level uuid5).
_RESULT_NAMESPACE = "run_result"

# A short identity is a UUID5 truncated for display; the full UUID5 stays the
# canonical value so determinism/relationships hold.
_SHORT_ID_LEN = 8

# Fallback when normalization would otherwise yield an empty string.
_EMPTY_LABEL_FALLBACK = "result"


def normalize_label(raw: Optional[str]) -> str:
    """Normalize a raw label for clean file/card output.

    - Collapses every run of non-alphanumeric characters to a single underscore.
    - Strips leading and trailing underscores (so no leading ``_`` or ``__`` survives).
    - Never returns an empty string — falls back to ``"result"`` for all-odd/empty input.
    """
    text = "" if raw is None else str(raw)
    # Collapse non-alphanumeric runs to a single underscore.
    collapsed = re.sub(r"[^0-9A-Za-z]+", "_", text)
    cleaned = collapsed.strip("_")
    return cleaned if cleaned else _EMPTY_LABEL_FALLBACK


def transformed_result_id(source_id: "uuid.UUID", mode: str, staging_ts: str) -> str:
    """Deterministic short result_id for a consumed transformed snapshot.

    Built with the same identity scheme as ``RunResult`` (UUID5 over the
    ``run_result`` table namespace, then truncated for display), so a transformed
    output a step joins against is a first-class, traceable result like any run
    (CONTEXT.md → Transformed-output result_id; PRD Implementation Decisions).

    Equal inputs (same source + mode + staging timestamp) always produce the same
    id, which is what makes a snapshot's identity stable across resolutions.
    """
    full = content_hash_id(_RESULT_NAMESPACE, str(source_id), mode, str(staging_ts))
    return full.hex[:_SHORT_ID_LEN]


@dataclass(frozen=True)
class RunResult:
    """Outcome of one normalized run (one argument bundle).

    Identity is a deterministic ``UUID5(function_name, bundle_key, source_id)`` — equal
    inputs always produce the same ``result_id`` (the slice's UUID5-determinism guarantee).
    """

    function_name: str
    function_type: str  # "validation" | "transform"
    source_id: uuid.UUID
    bundle_key: str  # identifies the argument bundle (N=1: the bound column name or "")
    label: str
    status: str  # "ok" | "failed"
    error: Optional[str] = None
    # Result-data fields (not identity): rows the step's frame holds after a
    # transform/built-in run, and — for a built-in join — the transformed-output
    # result_id it consumed (lineage; CONTEXT.md → Module contracts: FrameRef flows
    # into RunResult.consumed_result_id). None for runs to which they don't apply.
    rows_affected: Optional[int] = None
    consumed_result_id: Optional[str] = None

    @property
    def full_id(self) -> uuid.UUID:
        """Full deterministic UUID5(function, argument bundle, source)."""
        return content_hash_id(
            _RESULT_NAMESPACE,
            self.function_name,
            self.bundle_key,
            str(self.source_id),
        )

    @property
    def result_id(self) -> str:
        """Shortened UUID5 identity for display (stable for equal inputs)."""
        return self.full_id.hex[:_SHORT_ID_LEN]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the base identity/metadata contract."""
        return {
            "result_id": self.result_id,
            "label": self.label,
            "function_name": self.function_name,
            "function_type": self.function_type,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class ValidationRunResult(RunResult):
    """A validation-specialized RunResult carrying pass/fail counts."""

    rows_passed: Optional[int] = None
    rows_failed: Optional[int] = None
    failing_rows: list[dict] = field(default_factory=list)

    @property
    def pass_rate(self) -> Optional[float]:
        passed = self.rows_passed or 0
        failed = self.rows_failed or 0
        total = passed + failed
        return (passed / total) if total > 0 else None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            {
                "rows_passed": self.rows_passed,
                "rows_failed": self.rows_failed,
                "pass_rate": self.pass_rate,
                "failing_rows": self.failing_rows,
            }
        )
        return d


@dataclass(frozen=True)
class StepResultRef:
    """Result provenance — *which* step/function produced a result.

    This is the typed identity carrier that owns the step/function routing metadata
    a step result carries to the wire (the keys the frontend uses to correlate a
    result to its pipeline card). It is a distinct responsibility from ``RunResult``
    (the result *data*) and from ``StepContext`` (the step's own *description* in
    ``step.py``); a ``StepResultEntry`` composes a ``RunResult`` with one of these.

    Fields are optional because the three result kinds reference different identity:
    a **validation** result carries ``function_id``/``function_name``/``set_id``; a
    **transform** result carries ``source_function_map_id``; a **built-in** result
    carries ``step_id``/``builtin_type``. ``step_type`` ("function" | "builtin") plus
    the result's ``function_type`` discriminate the kind at serialization time.
    """

    step_type: str  # "function" | "builtin"
    source_function_map_id: Optional[Any] = None
    set_id: Optional[Any] = None
    set_name: Optional[str] = None
    function_id: Optional[Any] = None
    function_name: Optional[str] = None
    step_id: Optional[Any] = None
    builtin_type: Optional[str] = None


@dataclass(frozen=True)
class StepResultEntry:
    """One step-result row an executor produces: a ``RunResult`` (the result data)
    paired with a ``StepResultRef`` (the result's provenance/identity).

    Replaces the raw result dicts executors used to append to
    ``StepExecResult.entries`` — keeping the executor→runner boundary typed end to
    end (no ad-hoc dict, per the ``StepExecResult`` carrier contract: "entries are
    RunResult-derived, never ad-hoc dicts"). Both halves are typed carriers; the only
    place a dict appears is ``to_dict`` at ``run_pipeline``'s published return (the
    api/export seam).

    ``to_dict`` is the **single owner of the wire shape**. The three result kinds
    have irregular key sets, so it assembles each kind's exact dict from typed
    ``ref``/``run_result`` fields — byte-identical to the pre-refactor emissions, so
    the external ``{"steps": [...]}`` contract is unchanged.
    """

    run_result: RunResult
    ref: StepResultRef

    def to_dict(self) -> dict[str, Any]:
        ref = self.ref
        rr = self.run_result
        if ref.step_type == "builtin":
            routing: dict[str, Any] = {
                "source_function_map_id": ref.source_function_map_id,
                "step_id": ref.step_id,
                "step_type": "builtin",
                "builtin_type": ref.builtin_type,
                "set_name": ref.set_name,
                "rows_affected": rr.rows_affected,
                "rows_passed": None,
                "rows_failed": None,
                "consumed_result_id": rr.consumed_result_id,
            }
        elif rr.function_type == "validation":
            routing = {
                "function_id": ref.function_id,
                "function_name": ref.function_name,
                "set_name": ref.set_name,
                "set_id": ref.set_id,
            }
        else:  # transform
            routing = {
                "source_function_map_id": ref.source_function_map_id,
                "set_name": ref.set_name,
                "rows_affected": rr.rows_affected,
                "rows_passed": None,
                "rows_failed": None,
            }
        # RunResult fields overlay routing (identical to the legacy
        # entry.update(rr.to_dict())): on the one shared key, function_name, both
        # carry the same value, so overlay direction is invisible.
        return {**routing, **rr.to_dict()}
