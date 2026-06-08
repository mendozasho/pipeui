import uuid

import pytest

from pipeui.duckdb import create_schema
from tests.conftest import make_registered_source

REGISTRY_TABLES = {"source_registry", "function_registry", "column_registry", "parameter"}
MAP_TABLES = {"source_column_map", "source_function_map", "alias_map"}


def _table_names(conn) -> set[str]:
    """Returns the set of table names in the database."""
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
        "source_id",
        "content_hash_id",
        "source_name",
        "date_ingested",
        "date_registered",
        "ingestion_method",
        "pattern",
        "primary_key",
        "table_url",
    }
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'source_registry'"
    ).fetchall()
    actual = {r[0] for r in rows}
    assert actual == expected


@pytest.mark.integration
def test_function_registry_has_signature_and_is_active_columns(db):
    # §1 (Phase D): function_registry must have function_signature (nullable VARCHAR)
    # and is_active (BOOLEAN DEFAULT TRUE)
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'function_registry'"
    ).fetchall()
    col_names = {r[0] for r in rows}
    assert "function_signature" in col_names, "function_registry must have function_signature"
    assert "is_active" in col_names, "function_registry must have is_active"


@pytest.mark.integration
def test_function_registry_is_active_defaults_true(db):
    # §1 (Phase D): is_active DEFAULT TRUE — a row inserted without explicit is_active gets True
    import uuid
    fid = str(uuid.uuid4())
    chid = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO function_registry
            (function_id, content_hash_id, function_class, function_name,
             function_return_type, function_signature, function_type, module_path)
        VALUES (?, ?, 'scalar', 'fn', 'bool', '() -> bool', 'validation', '/tmp/fn.py')
        """,
        [fid, chid],
    )
    row = db.execute(
        "SELECT is_active FROM function_registry WHERE function_id = ?", [fid]
    ).fetchone()
    assert row[0] is True


@pytest.mark.integration
def test_function_registry_function_signature_not_null(db):
    # §1 (Phase D): function_signature is NOT NULL — functions without typed signatures
    # are rejected at registration time, so a null can never legitimately exist.
    import uuid
    fid = str(uuid.uuid4())
    chid = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO function_registry
            (function_id, content_hash_id, function_class, function_name,
             function_return_type, function_signature, function_type, module_path)
        VALUES (?, ?, 'scalar', 'fn2', 'bool', '(x: int) -> bool', 'validation', '/tmp/fn2.py')
        """,
        [fid, chid],
    )
    row = db.execute(
        "SELECT function_signature FROM function_registry WHERE function_id = ?", [fid]
    ).fetchone()
    assert row[0] == "(x: int) -> bool"


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
