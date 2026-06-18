import pytest

from pipeui.backend.domain.sources.create import CreateFlowCache


@pytest.fixture
def cache(db):
    return CreateFlowCache(db)


@pytest.mark.integration
def test_stage_columns_stores_all_columns(cache):
    # §5: create-flow cache holds column metadata
    cache.stage_columns([("id", "INTEGER"), ("name", "VARCHAR"), ("score", "DOUBLE")])
    staged = cache.get_staged()
    assert len(staged) == 3
    names = {r["column_name"] for r in staged}
    types = {r["column_name"]: r["column_type"] for r in staged}
    assert names == {"id", "name", "score"}
    assert types["id"] == "INTEGER"
    assert types["name"] == "VARCHAR"
    assert types["score"] == "DOUBLE"


@pytest.mark.integration
def test_stage_columns_is_idempotent(cache):
    # §5: re-stage is idempotent — second call replaces the first
    cache.stage_columns([("a", "INTEGER"), ("b", "VARCHAR")])
    cache.stage_columns([("x", "DOUBLE"), ("y", "BOOLEAN")])
    staged = cache.get_staged()
    assert len(staged) == 2
    names = {r["column_name"] for r in staged}
    assert names == {"x", "y"}


@pytest.mark.integration
def test_set_primary_key_marks_correct_column(cache):
    # §5: PK choice stored in cache
    cache.stage_columns([("id", "INTEGER"), ("name", "VARCHAR")])
    cache.set_primary_key("id")
    assert cache.get_primary_key() == "id"
    staged = {r["column_name"]: r["is_primary_key"] for r in cache.get_staged()}
    assert staged["id"] is True
    assert staged["name"] is False


@pytest.mark.integration
def test_set_primary_key_replaces_previous_pk(cache):
    # §5: only one PK at a time
    cache.stage_columns([("col_a", "INTEGER"), ("col_b", "VARCHAR")])
    cache.set_primary_key("col_a")
    cache.set_primary_key("col_b")
    assert cache.get_primary_key() == "col_b"
    staged = {r["column_name"]: r["is_primary_key"] for r in cache.get_staged()}
    assert staged["col_a"] is False
    assert staged["col_b"] is True


@pytest.mark.integration
def test_clear_removes_all_rows(cache):
    # §5: cache can be cleared
    cache.stage_columns([("a", "INTEGER"), ("b", "VARCHAR")])
    cache.clear()
    assert cache.get_staged() == []


@pytest.mark.integration
def test_drop_removes_temp_table(cache):
    # §5: temp table lifecycle
    cache.drop()
    with pytest.raises(Exception):
        cache._conn.execute("SELECT * FROM _stage_create_flow").fetchall()


@pytest.mark.integration
def test_create_flow_cache_does_not_affect_registry_tables(db):
    # §5: staging and registry are isolated
    c = CreateFlowCache(db)
    c.stage_columns([("col", "INTEGER")])
    count = db.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
    assert count == 0


@pytest.mark.integration
def test_get_primary_key_returns_none_when_not_set(cache):
    # §5: PK unset state
    cache.stage_columns([("a", "INTEGER"), ("b", "VARCHAR")])
    assert cache.get_primary_key() is None
