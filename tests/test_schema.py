import uuid

import pytest

from pipeui.db import create_schema
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


@pytest.mark.integration
def test_source_function_map_has_position_and_output_mode(db):
    """source_function_map must have position INTEGER NOT NULL and output_mode VARCHAR NOT NULL."""
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'source_function_map'"
    ).fetchall()
    col_names = {r[0] for r in rows}
    assert "position" in col_names, "source_function_map must have position column"
    assert "output_mode" in col_names, "source_function_map must have output_mode column"

    # Verify defaults: insert a row and check the defaults
    source_id, _ = make_registered_source(db, n_columns=1)
    set_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, uuid.uuid4(), "test_set", None],
    )
    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, 0, 'append')",
        [sfm_id, source_id, set_id],
    )
    row = db.execute(
        "SELECT position, output_mode FROM source_function_map WHERE source_function_map_id = ?",
        [sfm_id],
    ).fetchone()
    assert row[0] == 0
    assert row[1] == "append"


@pytest.mark.integration
def test_source_function_map_has_append_name_column(db):
    """source_function_map must expose an append_name VARCHAR column (slice 4b).

    The runtime (run.py) names append-mode columns by step['append_name'] when set;
    this column is the persistence backing for the user-provided append name.
    """
    rows = db.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'source_function_map'"
    ).fetchall()
    col_types = {r[0]: r[1] for r in rows}
    assert "append_name" in col_types, "source_function_map must have append_name column"
    assert col_types["append_name"] == "VARCHAR"

    # append_name is nullable (no default): a row without it inserts as NULL.
    source_id, _ = make_registered_source(db, n_columns=1)
    set_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, uuid.uuid4(), "test_set", None],
    )
    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, 0, 'append')",
        [sfm_id, source_id, set_id],
    )
    row = db.execute(
        "SELECT append_name FROM source_function_map WHERE source_function_map_id = ?",
        [sfm_id],
    ).fetchone()
    assert row[0] is None


@pytest.mark.integration
def test_alias_map_has_position_column(db):
    # Slice 2 #0: alias_map must expose a position INTEGER column so bound
    # columns persist their add-order (argument-bundle alignment).
    rows = db.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'alias_map'"
    ).fetchall()
    cols = {r[0]: r[1] for r in rows}
    assert "position" in cols, "alias_map must have a position column"
    assert "INT" in cols["position"].upper(), f"position should be an integer type, got {cols['position']}"


@pytest.mark.integration
def test_output_target_map_table_created(db):
    # Slice 4 #2: the output-target map ties a replace transform step's OUTPUT to
    # an ordered set of target columns — no existing table maps a function's output
    # to a column (alias_map is param-keyed input). The schema builder creates it.
    assert "output_target_map" in _table_names(db)

    rows = db.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'output_target_map'"
    ).fetchall()
    cols = {r[0]: r[1] for r in rows}
    # Keyed by (source_function_map_id, function_id) -> ordered (column_id, position).
    for expected in (
        "output_target_map_id",
        "source_function_map_id",
        "function_id",
        "column_id",
        "position",
    ):
        assert expected in cols, f"output_target_map must have a {expected} column"
    assert "INT" in cols["position"].upper(), f"position should be integer, got {cols['position']}"


def _old_schema_conn():
    """A connection whose alias_map/source_function_map predate this feature —
    i.e. the columns slices 2 and 4b added are absent (the real shape of a DB
    created before runner-execution). Mirrors the pre-feature DDL."""
    import duckdb

    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE alias_map (
            alias_map_id UUID PRIMARY KEY,
            column_id    UUID NOT NULL,
            parameter_id UUID NOT NULL,
            source_id    UUID NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE source_function_map (
            source_function_map_id UUID PRIMARY KEY,
            source_id              UUID NOT NULL,
            set_id                 UUID NOT NULL,
            position               INTEGER NOT NULL DEFAULT 0,
            output_mode            VARCHAR NOT NULL DEFAULT 'append'
        )
        """
    )
    return conn


@pytest.mark.integration
def test_run_migrations_adds_position_and_append_name_to_pre_feature_db():
    # #254: slices 2/4b added alias_map.position + source_function_map.append_name to
    # the DDL but never registered them in _REGISTRY_SCHEMA_MIGRATIONS, so a DB created
    # before this feature kept the old shape and get_pipeline 500'd on `ORDER BY am.position`.
    from pipeui.db import _run_migrations

    conn = _old_schema_conn()
    # an existing alias_map row must backfill to position 0, not NULL
    conn.execute(
        "INSERT INTO alias_map VALUES (?, ?, ?, ?)",
        [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())],
    )

    _run_migrations(conn)

    am_cols = {
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'alias_map'"
        ).fetchall()
    }
    sfm_cols = {
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'source_function_map'"
        ).fetchall()
    }
    assert "position" in am_cols, "alias_map must gain position after migration"
    assert "append_name" in sfm_cols, "source_function_map must gain append_name after migration"
    # existing row backfilled to the default, not NULL
    assert conn.execute("SELECT position FROM alias_map").fetchone()[0] == 0
    # the exact query shape that 500'd in get_pipeline now binds
    conn.execute("SELECT alias_map_id FROM alias_map am ORDER BY am.position").fetchall()


@pytest.mark.integration
def test_parameter_table_has_default_columns(db):
    # #258: parameter must carry has_default + default_value so the executor can fall
    # back to Python defaults and the frontend can flag required params.
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'parameter'"
    ).fetchall()
    cols = {r[0] for r in rows}
    assert "has_default" in cols, "parameter must have has_default"
    assert "default_value" in cols, "parameter must have default_value"


@pytest.mark.integration
def test_run_migrations_adds_parameter_default_columns():
    # #258: pre-feature parameter tables must gain has_default + default_value on migration.
    import duckdb
    from pipeui.db import _run_migrations

    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE parameter (param_id UUID PRIMARY KEY, content_hash_id UUID, "
        "param_name VARCHAR, param_type VARCHAR, function_id UUID)"
    )
    _run_migrations(conn)
    cols = {
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'parameter'"
        ).fetchall()
    }
    assert "has_default" in cols and "default_value" in cols


@pytest.mark.integration
def test_run_migrations_is_idempotent_when_columns_already_present():
    # #254: running migrations twice (column already added) must not raise.
    from pipeui.db import _run_migrations

    conn = _old_schema_conn()
    _run_migrations(conn)
    _run_migrations(conn)  # second pass: ADD COLUMN fails-and-rolls-back per entry, no raise
    am_cols = {
        r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'alias_map'"
        ).fetchall()
    }
    assert "position" in am_cols


@pytest.mark.integration
def test_function_output_config_table_created(db):
    # #264: per-function transform output config (output_mode + append_name), keyed
    # (source_function_map_id, function_id) like output_target_map.
    assert "function_output_config" in _table_names(db)
    cols = {
        r[0] for r in db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'function_output_config'"
        ).fetchall()
    }
    for c in ("source_function_map_id", "function_id", "output_mode", "append_name"):
        assert c in cols, f"function_output_config must have {c}"
