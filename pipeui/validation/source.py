from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from pipeui.ids import new_id, content_hash_id as _content_hash_id


class SourceRegistryEntry(BaseModel):
    """
    Represents an entry in the source registry.

    This class defines the structure for storing information about a source registered
    in the registry system. It includes attributes for tracking the source's unique
    identifiers, metadata, and ingestion method. The class also provides validations
    and utility methods to ensure the integrity of the data and assist with the
    construction of related URLs.

    :ivar source_id: The unique identifier for the source.
    :type source_id: uuid.UUID
    :ivar content_hash_id: The unique hash identifier for the source content, computed
        based on key attributes of the source.
    :type content_hash_id: uuid.UUID
    :ivar source_name: The name of the source being registered.
    :type source_name: str
    :ivar date_ingested: The optional datetime when the source was ingested.
    :type date_ingested: datetime.datetime | None
    :ivar date_registered: The date the source was registered, defaults to today's date.
    :type date_registered: datetime.date
    :ivar ingestion_method: The ingestion method of the source. Valid values are
        "upsert" or "skip".
    :type ingestion_method: str
    :ivar pattern: The optional pattern associated with the source.
    :type pattern: str | None
    :ivar primary_key: The primary key of the source, typically used for indexing or
        identification purposes.
    :type primary_key: str
    :ivar table_url: The optional URL of the table associated with the source.
    :type table_url: str | None
    """
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
    """
    Represents an update to the source registry.

    This model is intended to capture updates to a source registry entry. It allows
    specifying updated attributes while keeping other attributes unchanged. The
    class encapsulates metadata related to a source such as its name, ingestion
    method, and various related properties.

    :ivar source_id: The unique identifier for the source.
    :type source_id: uuid.UUID
    :ivar content_hash_id: The hash of the source content for deduplication, if available.
    :type content_hash_id: uuid.UUID | None
    :ivar source_name: The name of the source, if specified.
    :type source_name: str | None
    :ivar date_ingested: The timestamp when the source was ingested, if available.
    :type date_ingested: datetime.datetime | None
    :ivar ingestion_method: The method used for ingestion, if specified.
    :type ingestion_method: str | None
    :ivar pattern: A specific pattern associated with the source, if applicable.
    :type pattern: str | None
    :ivar primary_key: The primary key in the source, if defined.
    :type primary_key: str | None
    :ivar table_url: The URL of the underlying table in the source, if specified.
    :type table_url: str | None
    """
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
        """
        Creates a new instance of SourceRegistryUpdate based on an existing
        SourceRegistryEntry, applying any updates provided and recalculating
        the content hash ID.

        The method combines the attributes from the existing SourceRegistryEntry
        with the provided updates, ensuring that the `source_name`, `primary_key`,
        and `ingestion_method` are properly processed when recalculating the
        content hash ID.

        :param existing: An existing SourceRegistryEntry object whose attributes
            will serve as the base for the new instance.
        :param updates: A dictionary containing updated attributes that will
            override those in the existing object, if present.
        :return: A new instance of SourceRegistryUpdate with the updated attributes
            and a recalculated content hash ID.
        """
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
