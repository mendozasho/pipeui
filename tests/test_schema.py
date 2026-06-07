import uuid

import pytest

from pipeui.schema import create_schema, get_connection
from tests.conftest import make_registered_source

REGISTRY_TABLES = {"source_registry", "function_registry", "column_registry", "parameter"}
MAP_TABLES = {"source_column_map", "source_function_map", "alias_map"}


def _table_names(conn) -> set[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
    return {r[0] for r in rows}


@pytest.mark.integration
def test_all_registry_tables_created(db):
    # §1: all four registry tables must exist after create_schema
    assert REGISTRY_TABLES <= _table_names(db)


@pytest.mark.integration
def test_all_map_tables_created(db):
    # §1: all three relational map tables must exist after create_schema
    assert MAP_TABLES <= _table_names(db)


@pytest.mark.integration
def test_source_registry_columns(db):
    # §1: source_registry must expose exactly the columns specified in the schema
    expected = {
        "source_id", "content_hash_id", "source_name", "date_ingested",
        "date_registered", "ingestion_method", "pattern", "primary_key", "table_url",
    }
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'source_registry'"
    ).fetchall()
    actual = {r[0] for r in rows}
    assert actual == expected


@pytest.mark.integration
def test_create_schema_idempotent(db):
    # §1 / Principle 3: CREATE TABLE IF NOT EXISTS — calling twice must not raise
    create_schema(db)


@pytest.mark.integration
def test_source_column_map_has_composite_uuid_pk(db):
    # §1: source_column_map_id is a UUID; inserting a row and reading back the PK verifies that
    source_id, column_ids = make_registered_source(db, n_columns=1)
    row = db.execute("SELECT source_column_map_id FROM source_column_map LIMIT 1").fetchone()
    assert row is not None
    # DuckDB returns UUIDs as strings in this context; round-trip must parse cleanly
    parsed = uuid.UUID(str(row[0]))
    assert isinstance(parsed, uuid.UUID)
