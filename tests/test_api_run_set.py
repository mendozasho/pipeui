"""Behavioral guarantees for POST /pipelines/run-set?set_id={id} (F2-C / §13).

Guarantees under test:
  1. POST /pipelines/run-set?set_id={id} with a set attached to 2 sources returns
     a response with a `sources` array containing 2 entries.
  2. POST /pipelines/run-set?set_id={unknown} returns 404.
  3. POST /pipelines/run-set?set_id={id} with a set not attached to any source
     returns 404 (sources list empty).
"""
from __future__ import annotations

import csv
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.middleware.pipelines import router
from pipeui.middleware.deps import get_conn
from pipeui.backend.data.base.ids import content_hash_id
from pipeui.backend.domain.sources.create import create_source
from pipeui.backend.domain.sources.ingestion import ingest_source


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)


def make_csv(tmp_path, name, columns, rows):
    p = tmp_path / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return str(p)


def register_and_ingest(db, tmp_path, name):
    path = make_csv(tmp_path, f"{name}.csv", ["id", "val"], [["r1", 10], ["r2", 20]])
    source_id, failed = create_source(db, path, name, "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    col_id = db.execute(
        """
        SELECT cr.column_id FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ? AND cr.column_name = 'val'
        """,
        [source_id],
    ).fetchone()[0]
    return source_id, col_id


def seed_validation_step_for_set(db, source_id, column_id, fn_name, module_path, set_id):
    """Attach an existing set to source_id with a validation function."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", fn_name, None, "pd.Series[bool]",
         "data: pd.Series", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute("INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
               [param_id, param_ch, "data", "pd.Series", fn_id])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
               [sfm_id, source_id, set_id, 0, "append"])
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute("INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
               [alias_id, column_id, param_id, source_id])
    return sfm_id


def create_shared_set(db, set_name):
    """Create a function_set row and return its set_id."""
    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, set_ch, set_name, None])
    return set_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_set_across_two_sources_returns_two_entries(client, db, tmp_path):
    """Guarantee 1: run-set with a set attached to 2 sources returns 2 source entries."""
    # Arrange: two sources, one shared set
    source_id_a, col_id_a = register_and_ingest(db, tmp_path, "source_a")
    source_id_b, col_id_b = register_and_ingest(db, tmp_path, "source_b")

    # Write a simple validation function
    fn_path = tmp_path / "check_positive.py"
    fn_path.write_text("def check_positive(data):\n    return data > 0\n")

    set_id = create_shared_set(db, "positive_check")
    seed_validation_step_for_set(db, source_id_a, col_id_a, "check_positive", str(fn_path), set_id)
    seed_validation_step_for_set(db, source_id_b, col_id_b, "check_positive", str(fn_path), set_id)

    # Act
    resp = client.post(f"/pipelines/run-set?set_id={set_id}")

    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "sources" in data
    assert len(data["sources"]) == 2
    source_names = {s["source_name"] for s in data["sources"]}
    assert source_names == {"source_a", "source_b"}
    assert data["set_id"] == str(set_id)


@pytest.mark.integration
def test_run_set_unknown_set_id_returns_404(client, db):
    """Guarantee 2: unknown set_id returns 404."""
    unknown_id = uuid.uuid4()
    resp = client.post(f"/pipelines/run-set?set_id={unknown_id}")
    assert resp.status_code == 404


@pytest.mark.integration
def test_run_set_no_attached_sources_returns_404(client, db):
    """Guarantee 3: set with no attached sources returns 404."""
    set_id = create_shared_set(db, "unattached_set")
    resp = client.post(f"/pipelines/run-set?set_id={set_id}")
    assert resp.status_code == 404
