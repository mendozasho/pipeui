from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from pipeui.ids import new_id, content_hash_id as _content_hash_id


class ColumnRegistryEntry(BaseModel):
    column_id: uuid.UUID = Field(default_factory=new_id)
    content_hash_id: uuid.UUID = Field(default=None)  # type: ignore[assignment]
    column_name: str
    column_type: str

    @field_validator("column_type")
    @classmethod
    def _validate_column_type(cls, v: str) -> str:
        if not v:
            raise ValueError("column_type must not be empty")
        return v

    @model_validator(mode="after")
    def _compute_content_hash_id(self) -> "ColumnRegistryEntry":
        self.content_hash_id = _content_hash_id(
            "column_registry",
            self.column_name,
            self.column_type,
        )
        return self


class ColumnRegistryUpdate(BaseModel):
    column_id: uuid.UUID
    content_hash_id: uuid.UUID | None = None
    column_name: str | None = None
    column_type: str | None = None

    @classmethod
    def from_existing(cls, existing: ColumnRegistryEntry, **updates) -> "ColumnRegistryUpdate":
        contributing = {
            "column_name": existing.column_name,
            "column_type": existing.column_type,
        }
        for key in ("column_name", "column_type"):
            if key in updates:
                contributing[key] = updates[key]

        new_hash = _content_hash_id(
            "column_registry",
            contributing["column_name"],
            contributing["column_type"],
        )
        return cls(
            column_id=existing.column_id,
            content_hash_id=new_hash,
            **updates,
        )
