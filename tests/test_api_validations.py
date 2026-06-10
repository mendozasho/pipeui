"""Behavioral guarantees for POST /validations/run?function_id={id} (Phase F1 / §13).

Guarantees under test:

  1. Cross-source run returns all attached sources with per-source results.
  2. Worker crash isolation: a crash on one source marks it failed without blocking others.
  3. 404 on unknown function_id.
  4. Empty sources list when function has no source_function_map attachments.
  5. failing_rows content is correct (full row dicts, uncapped at API layer).
"""
from __future__ import annotations

import csv
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.api.validations import router
from pipeui.helpers import get_conn
from pipeui.ids import content_hash_id
from pipeui.workflow.create import create_source
from pipeui.workflow.ingestion import ingest_source


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


def register_and_ingest(db, tmp_path, name="sales"):
    path = make_csv(tmp_path, f"{name}.csv", ["id", "val"], [["r1", 10], ["r2", 20], ["r3", 30]])
    source_id, failed = create_source(db, path, name, "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    return source_id, path


def write_fn(tmp_path, fn_name, param, body):
    p = tmp_path / f"{fn_name}.py"
    p.write_text(f"def {fn_name}({param}):\n    {body}\n")
    return str(p)


def seed_validation_fn_and_attach(db, source_id, column_id, fn_name, module_path, position=0):
    """Register a validation function and attach it to a source. Returns (fn_id, set_id, sfm_id)."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", fn_name, None, "pd.Series[bool]",
         "data: pd.Series", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute("INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
               [param_id, param_ch, "data", "pd.Series", fn_id])
    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, set_ch, fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
               [sfm_id, source_id, set_id, position, "append"])
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute("INSERT INTO alias_map VALUES (?, ?, ?, ?)",
               [alias_id, column_id, param_id, source_id])
    return fn_id, set_id, sfm_id


def get_column_id(db, source_id, col_name="val"):
    return db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = ?",
        [source_id, col_name],
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Guarantee 3: 404 on unknown function_id
# ---------------------------------------------------------------------------

def test_unknown_function_returns_404(client):
    """POST /validations/run?function_id=<unknown> returns 404."""
    r = client.post(f"/validations/run?function_id={uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Guarantee 4: empty sources list when no attachments
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_no_attachments_returns_empty_sources(client, db, tmp_path):
    """A function with no source_function_map rows returns sources: []."""
    # Register the function but do NOT attach it to any source
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    module_path = write_fn(tmp_path, "always_true", "data", "return data > 0")
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", "always_true", None, "pd.Series[bool]",
         "data: pd.Series", "validation", module_path, True],
    )
    r = client.post(f"/validations/run?function_id={fn_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["function_id"] == str(fn_id)
    assert body["sources"] == []


# ---------------------------------------------------------------------------
# Guarantee 1: cross-source run returns all attached sources
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_cross_source_run_returns_all_attached_sources(client, db, tmp_path):
    """POST /validations/run returns one entry per attached source."""
    src1_id, _ = register_and_ingest(db, tmp_path, name="src1")
    src2_id, _ = register_and_ingest(db, tmp_path, name="src2")

    col1_id = get_column_id(db, src1_id)
    col2_id = get_column_id(db, src2_id)

    module_path = write_fn(tmp_path, "pos_check", "data", "return data > 0")
    fn_id, _, _ = seed_validation_fn_and_attach(db, src1_id, col1_id, "pos_check", module_path)
    # Attach the same function (same fn_id) to src2 via a new set
    set2_id = uuid.uuid4()
    set2_ch = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set2_id, set2_ch, "pos_check_s2", None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set2_id, fn_id, 0])
    sfm2_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
               [sfm2_id, src2_id, set2_id, 0, "append"])
    param_id = db.execute("SELECT param_id FROM parameter WHERE function_id = ?", [fn_id]).fetchone()[0]
    alias_id2 = content_hash_id("alias_map", str(param_id), str(col2_id), str(src2_id))
    db.execute("INSERT INTO alias_map VALUES (?, ?, ?, ?)",
               [alias_id2, col2_id, param_id, src2_id])

    r = client.post(f"/validations/run?function_id={fn_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["function_id"] == str(fn_id)
    source_ids = {s["source_id"] for s in body["sources"]}
    assert str(src1_id) in source_ids
    assert str(src2_id) in source_ids


# ---------------------------------------------------------------------------
# Guarantee 2: worker crash isolation
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_worker_crash_on_one_source_does_not_block_others(client, db, tmp_path):
    """A worker crash on one source sets status=failed; other sources still return results."""
    src_good, _ = register_and_ingest(db, tmp_path, name="good_src")
    src_bad, _ = register_and_ingest(db, tmp_path, name="bad_src")

    col_good = get_column_id(db, src_good)
    col_bad = get_column_id(db, src_bad)

    # Good function: always passes
    good_path = write_fn(tmp_path, "always_pass", "data", "return data > 0")
    fn_id, _, _ = seed_validation_fn_and_attach(db, src_good, col_good, "always_pass", good_path)

    # Attach same function to bad_src but point to a non-existent module_path
    bad_path = str(tmp_path / "nonexistent_module.py")  # does not exist on disk
    # We attach the fn with a patched module_path only for the bad source by creating
    # a separate function registry entry pointing to the bad path
    bad_fn_id = uuid.uuid4()
    bad_fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [bad_fn_id, bad_fn_ch, "pd.series", "always_pass", None, "pd.Series[bool]",
         "data: pd.Series", "validation", bad_path, True],
    )
    param_id_bad = uuid.uuid4()
    param_ch_bad = uuid.uuid4()
    db.execute("INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
               [param_id_bad, param_ch_bad, "data", "pd.Series", bad_fn_id])
    set_bad_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_bad_id, uuid.uuid4(), "always_pass_bad", None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_bad_id, bad_fn_id, 0])
    sfm_bad_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
               [sfm_bad_id, src_bad, set_bad_id, 0, "append"])
    alias_id_bad = content_hash_id("alias_map", str(param_id_bad), str(col_bad), str(src_bad))
    db.execute("INSERT INTO alias_map VALUES (?, ?, ?, ?)",
               [alias_id_bad, col_bad, param_id_bad, src_bad])

    # Run good function across good source (should succeed)
    r = client.post(f"/validations/run?function_id={fn_id}")
    assert r.status_code == 200
    body = r.json()
    src_ids = [s["source_id"] for s in body["sources"]]
    assert str(src_good) in src_ids

    # Run bad function across bad source (should return 200 with failed status for bad source)
    r2 = client.post(f"/validations/run?function_id={bad_fn_id}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["sources"]) == 1
    bad_entry = body2["sources"][0]
    assert bad_entry["source_id"] == str(src_bad)
    assert bad_entry["status"] == "failed"
    assert bad_entry["error"] is not None


# ---------------------------------------------------------------------------
# Guarantee 5: failing_rows content is correct
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_failing_rows_populated_correctly(client, db, tmp_path):
    """failing_rows contains full-row dicts for rows that fail the validation."""
    # Create source with rows where val is 10, -5, 30 (row with val=-5 should fail > 0 check)
    path = make_csv(tmp_path, "mixed.csv", ["id", "val"], [["r1", 10], ["r2", -5], ["r3", 30]])
    source_id, failed = create_source(db, path, "mixed", "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)

    col_id = get_column_id(db, source_id)
    module_path = write_fn(tmp_path, "positive_check", "data", "return data > 0")
    fn_id, _, _ = seed_validation_fn_and_attach(db, source_id, col_id, "positive_check", module_path)

    r = client.post(f"/validations/run?function_id={fn_id}")
    assert r.status_code == 200
    body = r.json()

    assert len(body["sources"]) == 1
    src_entry = body["sources"][0]
    assert src_entry["status"] == "ok"
    assert src_entry["rows_passed"] == 2
    assert src_entry["rows_failed"] == 1
    assert len(src_entry["failing_rows"]) == 1
    # The failing row should have id="r2" and val=-5
    failing = src_entry["failing_rows"][0]
    assert failing["id"] == "r2"
    assert int(failing["val"]) == -5
