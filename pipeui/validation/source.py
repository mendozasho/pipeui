from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from pipeui.ids import new_id, content_hash_id as _content_hash_id


class SourceRegistryEntry(BaseModel):
    source_id: uuid.UUID = Field(default_factory=new_id)
    content_hash_id: uuid.UUID = Field(default=None)  # type: ignore[assignment]
    source_name: str
    date_ingested: datetime.datetime | None = None
    date_registered: datetime.date = Field(default_factory=datetime.date.today)
    ingestion_method: str
    pattern: str | None = None
    primary_key: str
    table_url: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("ingestion_method")
    @classmethod
    def _validate_ingestion_method(cls, v: str) -> str:
        if v not in ("upsert", "skip"):
            raise ValueError(f"ingestion_method must be 'upsert' or 'skip', got {v!r}")
        return v

    @model_validator(mode="after")
    def _compute_content_hash_id(self) -> "SourceRegistryEntry":
        self.content_hash_id = _content_hash_id(
            "source_registry",
            self.source_name,
            self.primary_key,
            self.ingestion_method,
        )
        return self

    def generate_table_url(self, db_path: str) -> None:
        self.table_url = db_path


class SourceRegistryUpdate(BaseModel):
    source_id: uuid.UUID
    content_hash_id: uuid.UUID | None = None
    source_name: str | None = None
    date_ingested: datetime.datetime | None = None
    ingestion_method: str | None = None
    pattern: str | None = None
    primary_key: str | None = None
    table_url: str | None = None

    @classmethod
    def from_existing(cls, existing: SourceRegistryEntry, **updates) -> "SourceRegistryUpdate":
        contributing = {
            "source_name": existing.source_name,
            "primary_key": existing.primary_key,
            "ingestion_method": existing.ingestion_method,
        }
        for key in ("source_name", "primary_key", "ingestion_method"):
            if key in updates:
                contributing[key] = updates[key]

        new_hash = _content_hash_id(
            "source_registry",
            contributing["source_name"],
            contributing["primary_key"],
            contributing["ingestion_method"],
        )
        return cls(
            source_id=existing.source_id,
            content_hash_id=new_hash,
            **updates,
        )
