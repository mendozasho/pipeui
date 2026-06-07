import datetime
import itertools
import uuid

import pytest

from pipeui.ids import content_hash_id
from pipeui.schema import create_schema, get_connection


@pytest.fixture
def db():
    # §13: fresh in-memory sandbox; function-scoped so each test gets a clean slate
    conn = get_connection(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def db_file(tmp_path):
    # §13: file-backed only when table_url / file-path resolution is under test
    path = str(tmp_path / "test.db")
    conn = get_connection(path)
    create_schema(conn)
    yield conn, path
    conn.close()


@pytest.fixture
def patch_new_id(monkeypatch):
    counter = itertools.count(1)

    def _fixed() -> uuid.UUID:
        return uuid.UUID(int=next(counter))

    monkeypatch.setattr("pipeui.ids.new_id", _fixed)
    return _fixed


def make_registered_source(conn, n_columns: int = 2):
    source_id = uuid.uuid4()
    ch = content_hash_id("source_registry", f"test_source_{source_id}", "id", "upsert")
    conn.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_id, ch, f"test_source_{source_id}", datetime.date.today(), "upsert", "id"],
    )

    column_ids = []
    for i in range(n_columns):
        col_id = uuid.uuid4()
        col_name = f"col_{i}"
        col_ch = content_hash_id("column_registry", col_name, "INTEGER")
        conn.execute(
            "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
            [col_id, col_ch, col_name, "INTEGER"],
        )
        map_id = content_hash_id("source_column_map", str(source_id), str(col_id))
        conn.execute(
            "INSERT INTO source_column_map VALUES (?, ?, ?)",
            [map_id, col_id, source_id],
        )
        column_ids.append(col_id)

    return source_id, column_ids
