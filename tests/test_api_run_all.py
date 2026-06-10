"""Behavioral guarantees for POST /pipelines/{source_id}/run?run_type=all (§13 / F2-A).

Guarantees under test:

  1. run_type=all with both validation and transform steps attached:
     response steps array contains entries with both function_type values.
  2. run_type=all with only validation steps: only validation steps returned; status 200.
  3. run_type=all with only transform steps: only transform steps returned; status 200.
  4. A crashing step in a run_type=all run: failed step has status "failed" in the
     response and subsequent steps still execute (response contains more than just
     the failing step).
"""
from __future__ import annotations

import csv
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.api.pipelines import router
from pipeui.duckdb import get_conn
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


def seed_transform_step(db, source_id, column_id, fn_name, module_path, position=0, output_mode="append"):
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", fn_name, None, "pd.Series",
         "data: pd.Series", "transform", module_path, True],
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
               [sfm_id, source_id, set_id, position, output_mode])
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute("INSERT INTO alias_map VALUES (?, ?, ?, ?)",
               [alias_id, column_id, param_id, source_id])
    return sfm_id, set_id


def seed_validation_step(db, source_id, column_id, fn_name, module_path, position=1):
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
    return sfm_id, set_id


def get_val_col_id(db, source_id):
    return db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Guarantee 1: run_type=all with both validation and transform steps
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_all_returns_both_function_types(client, db, tmp_path):
    """Guarantee 1: run_type=all with both step types returns entries of both function_type values."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = get_val_col_id(db, source_id)

    xform_path = write_fn(tmp_path, "noop", "data", "return data")
    seed_transform_step(db, source_id, col_id, "noop", xform_path, position=0)

    check_path = write_fn(tmp_path, "all_positive", "data", "return data > 0")
    seed_validation_step(db, source_id, col_id, "all_positive", check_path, position=1)

    r = client.post(f"/pipelines/{source_id}/run?run_type=all")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_type"] == "all"
    steps = body["steps"]
    types = {s["function_type"] for s in steps}
    assert "transform" in types, "expected transform step in run_type=all response"
    assert "validation" in types, "expected validation step in run_type=all response"


# ---------------------------------------------------------------------------
# Guarantee 2: run_type=all with only validation steps
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_all_only_validation_steps(client, db, tmp_path):
    """Guarantee 2: run_type=all with only validation steps returns only validation steps; status 200."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = get_val_col_id(db, source_id)

    check_path = write_fn(tmp_path, "positive_check", "data", "return data > 0")
    seed_validation_step(db, source_id, col_id, "positive_check", check_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=all")
    assert r.status_code == 200, r.text
    body = r.json()
    steps = body["steps"]
    assert len(steps) > 0
    types = {s["function_type"] for s in steps}
    assert types == {"validation"}, f"expected only validation steps, got {types}"


# ---------------------------------------------------------------------------
# Guarantee 3: run_type=all with only transform steps
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_all_only_transform_steps(client, db, tmp_path):
    """Guarantee 3: run_type=all with only transform steps returns only transform steps; status 200."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = get_val_col_id(db, source_id)

    xform_path = write_fn(tmp_path, "double_val", "data", "return data * 2")
    seed_transform_step(db, source_id, col_id, "double_val", xform_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=all")
    assert r.status_code == 200, r.text
    body = r.json()
    steps = body["steps"]
    assert len(steps) > 0
    types = {s["function_type"] for s in steps}
    assert types == {"transform"}, f"expected only transform steps, got {types}"


# ---------------------------------------------------------------------------
# Guarantee 4: crashing step → status=failed; subsequent steps still execute
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_all_crashing_step_does_not_abort_subsequent_steps(client, db, tmp_path):
    """Guarantee 4: a crashing transform step surfaces as status=failed; subsequent
    steps still execute (response contains more than just the failing step).

    Pipeline: crash_xform (position=0, transform) → noop_xform (position=1, transform)
    After the crash the chain continues with the last good working table.
    """
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = get_val_col_id(db, source_id)

    crash_path = write_fn(tmp_path, "crash_fn", "data", "raise RuntimeError('intentional crash')")
    seed_transform_step(db, source_id, col_id, "crash_fn", crash_path, position=0)

    noop_path = write_fn(tmp_path, "noop_fn", "data", "return data")
    seed_transform_step(db, source_id, col_id, "noop_fn", noop_path, position=1)

    r = client.post(f"/pipelines/{source_id}/run?run_type=all")
    assert r.status_code == 200, r.text
    body = r.json()
    steps = body["steps"]

    # There must be more than one step (the crash step + subsequent noop)
    assert len(steps) > 1, "subsequent steps should still execute after a crash"

    # The first step should be failed
    failed_steps = [s for s in steps if s.get("status") == "failed"]
    assert len(failed_steps) >= 1, "crashing step should surface as status=failed"

    # There should also be a successful step
    ok_steps = [s for s in steps if s.get("status") == "ok"]
    assert len(ok_steps) >= 1, "subsequent non-crashing step should be status=ok"
