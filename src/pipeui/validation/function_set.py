"""Pydantic validation objects for function_set registry rows — §3.

FunctionSetEntry: validates fields and computes content_hash_id at construction.
FunctionSetUpdate: all-optional; recomputes content_hash_id only when set_name changes.

Neither object holds a DB handle or reads other rows — collision enforcement
is the workflow layer's responsibility (CLAUDE.md Principle 1, §2).
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, model_validator

from pipeui.ids import content_hash_id as _content_hash_id, new_id

_TABLE = "function_set"


class FunctionSetEntry(BaseModel):
    """Validated representation of a new function_set registry row.

    content_hash_id is computed from set_name only (decided: grilling session D2).
    set_id is a uuid4 surrogate — the only value maps reference.
    """
    set_id: uuid.UUID = Field(default_factory=new_id)
    content_hash_id: uuid.UUID = Field(default=None)  # type: ignore[assignment]
    set_name: str
    set_description: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def _compute_hash(self) -> "FunctionSetEntry":
        self.content_hash_id = _content_hash_id(_TABLE, self.set_name)
        return self


class FunctionSetUpdate(BaseModel):
    """Partial update for a function_set registry row.

    content_hash_id is recomputed only when set_name is provided.
    The collision check is enforced at the write boundary in the workflow layer.
    """
    set_id: uuid.UUID
    content_hash_id: uuid.UUID | None = None
    set_name: str | None = None
    set_description: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_existing(
        cls,
        existing: FunctionSetEntry,
        **updates,
    ) -> "FunctionSetUpdate":
        """Build an update from an existing entry, recomputing hash only if set_name changes."""
        new_name = updates.get("set_name", existing.set_name)
        new_hash = _content_hash_id(_TABLE, new_name)
        return cls(
            set_id=existing.set_id,
            content_hash_id=new_hash,
            set_name=updates.get("set_name"),
            set_description=updates.get("set_description"),
        )
