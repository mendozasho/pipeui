import uuid
from unittest.mock import patch

import pytest

import pipeui.ids as ids_mod
from pipeui.ids import content_hash_id, new_id


@pytest.mark.unit
def test_content_hash_id_changes_on_contributing_field_change():
    """Test that content_hash_id changes when contributing fields change.

    Principle 1: content_hash_id is a function of its contributing fields
    """
    h1 = content_hash_id("source_registry", "my_source", "id", "upsert")
    h2 = content_hash_id("source_registry", "my_source", "id", "skip")
    assert h1 != h2


@pytest.mark.unit
def test_content_hash_id_stable_on_same_inputs():
    """Test that content_hash_id is deterministic for identical inputs.

    Principle 2: content_hash_id is deterministic — identical inputs always yield the same hash
    """
    h1 = content_hash_id("source_registry", "my_source", "id", "upsert")
    h2 = content_hash_id("source_registry", "my_source", "id", "upsert")
    assert h1 == h2


@pytest.mark.unit
def test_different_table_namespace_yields_different_hash():
    """Test that different table namespaces yield different hashes."""
    h_source = content_hash_id("source_registry", "name", "id", "upsert")
    h_column = content_hash_id("column_registry", "name", "id", "upsert")
    assert h_source != h_column


@pytest.mark.unit
def test_new_id_returns_uuid4():
    """Test that new_id returns a UUID4."""
    result = new_id()
    assert isinstance(result, uuid.UUID)


@pytest.mark.unit
def test_new_id_injectable():
    """Test that new_id can be injected with a mock."""
    sentinel = uuid.UUID(int=42)
    with patch.object(ids_mod, "_uuid4", return_value=sentinel):
        result = ids_mod.new_id()
    assert result == sentinel
