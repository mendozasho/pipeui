from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from pipeui.ids import new_id, content_hash_id as _content_hash_id


class ColumnRegistryEntry(BaseModel):
    """
    Represents an entry in a column registry.

    The `ColumnRegistryEntry` class is used to define metadata and maintain validation
    logic for database or data structure columns. It includes attributes for identifying
    columns, specifying their type and name, and calculating a unique content hash ID.

    :ivar column_id: A unique identifier for the column.
    :type column_id: uuid.UUID
    :ivar content_hash_id: A hash-based identifier derived dynamically based on the
        column name and type. Defaults to None until computed.
    :type content_hash_id: uuid.UUID
    :ivar column_name: The name of the column.
    :type column_name: str
    :ivar column_type: A string representing the type of the column, such as 'integer',
        'string', or 'date'.
    :type column_type: str
    """
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
    """
    Represents an update to a column registry entry.

    This class is used to model the update operations on an existing column registry
    entry. It captures the necessary properties like column ID, content hash ID,
    column name, and column type that may be updated. It also provides a method to
    construct an update instance from an existing column registry entry.

    :ivar column_id: The unique identifier for the column.
    :type column_id: uuid.UUID
    :ivar content_hash_id: The hash ID representing content changes for the column.
        This is optional and may be None if not provided.
    :type content_hash_id: uuid.UUID or None
    :ivar column_name: The name of the column. This is optional and may be None
        if not provided.
    :type column_name: str or None
    :ivar column_type: The type of the column. This is optional and may be None
        if not provided.
    :type column_type: str or None
    """
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
