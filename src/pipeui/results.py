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
