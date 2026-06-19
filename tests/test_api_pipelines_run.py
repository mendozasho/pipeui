"""Behavioral guarantees for POST /pipelines/{source_id}/run (Phase E2/F1 / §13).

Guarantees under test:

run_type variants:
  1. run_type=transforms executes transform steps and returns per-step results.
  2. run_type=validations executes validation steps and returns per-function results.
  3. run_type=set&set_id={id} executes only the specified set.
  4. Unknown source returns 404.
  5. Unknown set_id on run_type=set returns 200 with empty steps (not an error).

Failure / skip:
  6. A worker crash on transforms returns status=failed with error; subsequent steps still execute.
  6b. A worker crash on one validation function sets that function's status=failed; other
      functions in the same run still return results.

Response shape:
  7. Transform step response shape matches the documented shape (unchanged).
  8. Validation per-function response shape: function_id, function_name, set_name, set_id,
     status, rows_passed, rows_failed, pass_rate, failing_rows, error.
  9. pass_rate is a float 0..1 when status=ok; null when status=failed.
  10. failing_rows is [] when all rows pass or when status=failed.
  11. run_type=transforms path is unaffected by the validation shape change.
  12. failing_rows contains correct full-row dicts for pd.Series[bool] result.
  13. failing_rows is [] when all rows pass.
  14. failing_rows is [] on worker crash (status=failed).
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
    db.execute("INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
               [param_id, param_ch, "data", "pd.Series", fn_id])
    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, set_ch, fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
               [sfm_id, source_id, set_id, position, output_mode])
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute("INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
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
    db.execute("INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
               [param_id, param_ch, "data", "pd.Series", fn_id])
    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, set_ch, fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
               [sfm_id, source_id, set_id, position, "append"])
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute("INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
               [alias_id, column_id, param_id, source_id])
    return sfm_id, set_id


# ---------------------------------------------------------------------------
# Guarantee 4: unknown source returns 404
# ---------------------------------------------------------------------------

def test_run_unknown_source_returns_404(client):
    """GET /pipelines/{unknown}/run returns 404."""
    r = client.post(f"/pipelines/{uuid.uuid4()}/run?run_type=transforms")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# run_type=transforms
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_transforms_executes_steps_and_returns_results(client, db, tmp_path):
    """Guarantee 1: run_type=transforms executes transform steps and returns per-step results."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "noop", "data", "return data")
    seed_transform_step(db, source_id, col_id, "noop", fn_path)

    r = client.post(f"/pipelines/{source_id}/run?run_type=transforms")
    assert r.status_code == 200
    body = r.json()
    assert body["run_type"] == "transforms"
    assert len(body["steps"]) == 1
    assert body["steps"][0]["status"] == "ok"
    assert body["steps"][0]["function_type"] == "transform"


# ---------------------------------------------------------------------------
# Guarantee 7: transform step response shape
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_transform_step_response_shape(client, db, tmp_path):
    """Guarantee 7: transform step response contains expected keys."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "id_fn", "data", "return data")
    seed_transform_step(db, source_id, col_id, "id_fn", fn_path)

    r = client.post(f"/pipelines/{source_id}/run?run_type=transforms")
    step = r.json()["steps"][0]
    assert "source_function_map_id" in step
    assert "set_name" in step
    assert "function_type" in step
    assert "status" in step
    assert "rows_affected" in step
    assert "rows_passed" in step
    assert "rows_failed" in step
    assert "error" in step
    # validation fields are null for transform
    assert step["rows_passed"] is None
    assert step["rows_failed"] is None


# ---------------------------------------------------------------------------
# run_type=validations
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_validations_returns_pass_fail(client, db, tmp_path):
    """Guarantee 2: run_type=validations returns rows_passed/rows_failed."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    # val > 15 → passes rows r2 (20) and r3 (30), fails r1 (10)
    fn_path = write_fn(tmp_path, "gt15", "data", "return data > 15")
    seed_validation_step(db, source_id, col_id, "gt15", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    step = r.json()["steps"][0]
    assert step["status"] == "ok"
    assert step["rows_passed"] == 2
    assert step["rows_failed"] == 1
    # Per-function shape: no rows_affected field
    assert "rows_affected" not in step


# ---------------------------------------------------------------------------
# Guarantee 8: validation per-function response shape
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_validation_step_response_shape(client, db, tmp_path):
    """Guarantee 8/9/10: validation per-function response shape is correct."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "always_pass", "data", "return data > 0")
    seed_validation_step(db, source_id, col_id, "always_pass", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    step = r.json()["steps"][0]
    # Required per-function fields
    assert "function_id" in step
    assert "function_name" in step
    assert "set_name" in step
    assert "set_id" in step
    assert "status" in step
    assert "rows_passed" in step
    assert "rows_failed" in step
    assert "pass_rate" in step
    assert "failing_rows" in step
    assert "error" in step
    # pass_rate is float 0..1 on success (Guarantee 9)
    assert isinstance(step["pass_rate"], float)
    assert 0.0 <= step["pass_rate"] <= 1.0
    # failing_rows is [] when all rows pass (Guarantee 10/13)
    assert step["failing_rows"] == []


# ---------------------------------------------------------------------------
# Guarantee 6b: worker crash isolation for validation functions
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_validation_worker_crash_isolated_other_functions_still_run(client, db, tmp_path):
    """Guarantee 6b: crash in one validation function leaves others unaffected."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_crash = write_fn(tmp_path, "val_crash", "data", "raise RuntimeError('val boom')")
    seed_validation_step(db, source_id, col_id, "val_crash", fn_crash, position=0)

    fn_ok = write_fn(tmp_path, "val_ok", "data", "return data > 0")
    seed_validation_step(db, source_id, col_id, "val_ok", fn_ok, position=1)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    steps = r.json()["steps"]
    assert len(steps) == 2
    crashed = next(s for s in steps if s["function_name"] == "val_crash")
    ok_step = next(s for s in steps if s["function_name"] == "val_ok")
    assert crashed["status"] == "failed"
    assert crashed["error"] is not None
    assert crashed["pass_rate"] is None
    assert ok_step["status"] == "ok"
    assert ok_step["rows_passed"] is not None


# ---------------------------------------------------------------------------
# Guarantee 11: transforms path unaffected
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_transforms_path_unaffected_by_validation_shape_change(client, db, tmp_path):
    """Guarantee 11: run_type=transforms response shape is unchanged."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "noop_t", "data", "return data")
    seed_transform_step(db, source_id, col_id, "noop_t", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=transforms")
    assert r.status_code == 200
    body = r.json()
    assert body["run_type"] == "transforms"
    step = body["steps"][0]
    assert step["status"] == "ok"
    assert "source_function_map_id" in step
    assert "set_name" in step
    assert "function_type" in step
    assert "rows_affected" in step
    assert step["rows_passed"] is None
    assert step["rows_failed"] is None


# ---------------------------------------------------------------------------
# run_type=set
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_type_set_executes_only_specified_set(client, db, tmp_path):
    """Guarantee 3: run_type=set executes only the specified set."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_a = write_fn(tmp_path, "set_fn_a", "data", "return data")
    sfm_a, set_a = seed_transform_step(db, source_id, col_id, "set_fn_a", fn_a, position=0)

    fn_b = write_fn(tmp_path, "set_fn_b", "data", "return data")
    seed_transform_step(db, source_id, col_id, "set_fn_b", fn_b, output_mode="append", position=1)

    r = client.post(f"/pipelines/{source_id}/run?run_type=set&set_id={set_a}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["steps"]) == 1
    assert body["steps"][0]["source_function_map_id"] == str(sfm_a)


@pytest.mark.integration
def test_run_type_set_unknown_set_returns_empty_steps(client, db, tmp_path):
    """Guarantee 5: run_type=set with unknown set_id returns 200 with empty steps."""
    source_id, _ = register_and_ingest(db, tmp_path)
    r = client.post(f"/pipelines/{source_id}/run?run_type=set&set_id={uuid.uuid4()}")
    assert r.status_code == 200
    assert r.json()["steps"] == []


# ---------------------------------------------------------------------------
# Failure / skip (Guarantee 6)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_worker_crash_returns_failed_status_subsequent_steps_run(client, db, tmp_path):
    """Guarantee 6: worker crash → status=failed+error; subsequent steps still execute."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_bad = write_fn(tmp_path, "crash_fn", "data", "raise RuntimeError('boom')")
    seed_transform_step(db, source_id, col_id, "crash_fn", fn_bad, position=0)

    fn_ok = write_fn(tmp_path, "ok_fn2", "data", "return data")
    seed_transform_step(db, source_id, col_id, "ok_fn2", fn_ok, output_mode="append", position=1)

    r = client.post(f"/pipelines/{source_id}/run?run_type=transforms")
    assert r.status_code == 200
    steps = r.json()["steps"]
    assert steps[0]["status"] == "failed"
    assert steps[0]["error"] is not None
    assert steps[1]["status"] == "ok"


# ---------------------------------------------------------------------------
# Guarantee 12: failing_rows populated for pd.Series[bool] result
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_failing_rows_populated_for_series_bool_result(client, db, tmp_path):
    """Guarantee 12: failing_rows contains full row dicts for rows where pd.Series result is False."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    # val > 15 → r1 (val=10) fails, r2 (val=20) and r3 (val=30) pass
    fn_path = write_fn(tmp_path, "gt15_fr", "data", "return data > 15")
    seed_validation_step(db, source_id, col_id, "gt15_fr", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    step = r.json()["steps"][0]
    assert step["status"] == "ok"
    assert step["rows_failed"] == 1
    failing = step["failing_rows"]
    assert len(failing) == 1
    # Full row values present (not just PK)
    assert "id" in failing[0]
    assert "val" in failing[0]
    assert failing[0]["id"] == "r1"
    assert failing[0]["val"] == 10


# ---------------------------------------------------------------------------
# Guarantee 13: failing_rows is [] when all rows pass
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_failing_rows_empty_when_all_pass(client, db, tmp_path):
    """Guarantee 13: failing_rows is [] when every row passes the validation."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    # val > 0 → all rows pass (10, 20, 30 all > 0)
    fn_path = write_fn(tmp_path, "all_pass_fr", "data", "return data > 0")
    seed_validation_step(db, source_id, col_id, "all_pass_fr", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    step = r.json()["steps"][0]
    assert step["status"] == "ok"
    assert step["rows_failed"] == 0
    assert step["failing_rows"] == []


# ---------------------------------------------------------------------------
# Guarantee 14: failing_rows is [] on worker crash (status=failed)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_failing_rows_empty_on_worker_crash(client, db, tmp_path):
    """Guarantee 14: failing_rows is [] when the worker crashes (status=failed)."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "val_crash_fr", "data", "raise RuntimeError('crash')")
    seed_validation_step(db, source_id, col_id, "val_crash_fr", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    step = r.json()["steps"][0]
    assert step["status"] == "failed"
    assert step["failing_rows"] == []


# ---------------------------------------------------------------------------
# Slice runner-execution/1 — acceptance #2: POST /run serializes RunResult(s)
# with a readable label and the UUID5 identity.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_validations_serializes_runresult_identity_and_label(client, db, tmp_path):
    """Acceptance #2: each validation result carries a UUID5 result_id and a readable label."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "rr_gt5", "data", "return data > 5")
    seed_validation_step(db, source_id, col_id, "rr_gt5", fn_path, position=0)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    step = r.json()["steps"][0]
    import re as _re
    assert _re.fullmatch(r"[0-9a-f]+", step["result_id"])  # UUID5 hex identity
    assert step["label"] == "val"                          # readable, normalized
    assert step["function_type"] == "validation"


@pytest.mark.integration
def test_run_transforms_serializes_runresult_identity_and_label(client, db, tmp_path):
    """Acceptance #2: a transform run result carries a UUID5 result_id and a readable label."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = write_fn(tmp_path, "rr_double", "data", "return data * 2")
    seed_transform_step(db, source_id, col_id, "rr_double", fn_path)

    r = client.post(f"/pipelines/{source_id}/run?run_type=transforms")
    assert r.status_code == 200
    step = r.json()["steps"][0]
    import re as _re
    assert _re.fullmatch(r"[0-9a-f]+", step["result_id"])
    assert step["label"] == "val"
    assert step["function_type"] == "transform"


# ---------------------------------------------------------------------------
# Slice runner-execution/3 — acceptance #4: POST /run returns per-bundle RunResults
# (one per argument bundle). The Results screen renders one card per RunResult
# (slice 1), so N RunResults -> N labeled cards.
# NEW alias_map fixtures use explicit-column INSERTs (slice-2 5th column position).
# ---------------------------------------------------------------------------

def register_and_ingest_multicol(db, tmp_path, name="multi", cols=("a", "b", "c")):
    header = ["id", *cols]
    rows = [["r1", *[10] * len(cols)], ["r2", *[20] * len(cols)], ["r3", *[30] * len(cols)]]
    path = make_csv(tmp_path, f"{name}.csv", header, rows)
    source_id, failed = create_source(db, path, name, "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    return source_id


def seed_multicol_validation_step(db, source_id, column_ids, fn_name, module_path, position=0):
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "pd.series", fn_name, None, "pd.Series[bool]",
         "data: pd.Series", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    db.execute("INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
               [param_id, uuid.uuid4(), "data", "pd.Series", fn_id])
    set_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, uuid.uuid4(), fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
               [sfm_id, source_id, set_id, position, "append"])
    for pos, col_id in enumerate(column_ids):
        alias_id = content_hash_id("alias_map", str(param_id), str(col_id), str(source_id))
        db.execute(
            "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
            [alias_id, col_id, param_id, source_id, pos],
        )
    return sfm_id, set_id


@pytest.mark.integration
def test_run_returns_one_runresult_per_bundle(client, db, tmp_path):
    """Acceptance #4: a validation bound to N columns -> N per-bundle RunResults over POST /run."""
    source_id = register_and_ingest_multicol(db, tmp_path, cols=("a", "b", "c"))
    col_ids = [
        db.execute(
            "SELECT cr.column_id FROM column_registry cr "
            "JOIN source_column_map scm ON scm.column_id = cr.column_id "
            "WHERE scm.source_id = ? AND cr.column_name = ?",
            [source_id, c],
        ).fetchone()[0]
        for c in ("a", "b", "c")
    ]
    fn_path = write_fn(tmp_path, "gt15_api", "data", "return data > 15")
    seed_multicol_validation_step(db, source_id, col_ids, "gt15_api", fn_path)

    r = client.post(f"/pipelines/{source_id}/run?run_type=validations")
    assert r.status_code == 200
    steps = r.json()["steps"]
    assert len(steps) == 3
    labels = sorted(s["label"] for s in steps)
    assert labels == ["a", "b", "c"]
    # Distinct per-bundle identity.
    assert len({s["result_id"] for s in steps}) == 3


# ---------------------------------------------------------------------------
# runner-resolution-model slice 5 (#15 / #21 / #22) — behavior-preservation lock.
#
# Slices 3+4 moved run_pipeline's dispatch onto the STEP_EXECUTORS registry (the
# unified resolution model). The run / results / staging API paths already route
# through run_pipeline, so the migration is "already done" by construction — these
# tests LOCK that:
#   * #21[0]: the run endpoint's response equals a direct run_pipeline call (shape
#     + content preserved across the migration), AND the registry was the execution
#     path (a spy proves no endpoint bypasses the unified model).
#   * #21[1]: same for the results-export endpoint (a results/staging path).
#   * #22[2]: no execution code was left dead by the registry swap to remove (every
#     run.py helper retains a live caller — see the build record's grep); the only
#     thing to guard is that the registry stays the sole execution path. The
#     registry-routing assertion below IS that behavior-preserving guard — if a
#     future change re-introduced a superseded bypass path, these go red.
# ---------------------------------------------------------------------------

import pipeui.backend.domain.runner.executors as _executors_mod  # noqa: E402


class _SpyExecutor:
    """Wraps a real StepExecutor, recording each dispatch through the registry."""

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def execute(self, ctx, working, env):
        self._log.append(ctx.step_type)
        return self._inner.execute(ctx, working, env)


def _spy_registry(monkeypatch):
    """Replace STEP_EXECUTORS with spies; return the dispatch-log list.

    Patches the module-level dict run_pipeline reads (it imports the module and reads
    `_executors.STEP_EXECUTORS`), so any execution that does NOT go through the
    registry produces an empty log — proving a bypass.
    """
    log: list[str] = []
    spied = {k: _SpyExecutor(v, log) for k, v in _executors_mod.STEP_EXECUTORS.items()}
    monkeypatch.setattr(_executors_mod, "STEP_EXECUTORS", spied)
    return log


@pytest.mark.integration
def test_run_endpoint_routes_through_registry_and_matches_run_pipeline(client, db, tmp_path, monkeypatch):
    """#21[0]: the run endpoint produces the same shape/content as a direct run_pipeline
    call, and dispatches through the STEP_EXECUTORS registry (unified model — no bypass)."""
    from pipeui.backend.domain.runner.run import run_pipeline

    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    fn_path = write_fn(tmp_path, "lock_double", "data", "return data * 2")
    seed_transform_step(db, source_id, col_id, "lock_double", fn_path)

    # Direct call to the unified model = the reference output.
    direct = run_pipeline(db, source_id, "transforms")

    # Endpoint call, with the registry spied.
    log = _spy_registry(monkeypatch)
    resp = client.post(f"/pipelines/{source_id}/run?run_type=transforms")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Behavior preserved: same keys, same per-step content (result_id is the UUID5
    # identity, stable across equal inputs, so equality is meaningful).
    assert body["run_type"] == direct["run_type"]
    assert len(body["steps"]) == len(direct["steps"]) == 1
    e_step, d_step = body["steps"][0], direct["steps"][0]
    for key in ("status", "function_type", "result_id", "label", "rows_affected"):
        assert e_step[key] == d_step[key], f"{key}: {e_step.get(key)!r} != {d_step.get(key)!r}"

    # Routed through the unified model: the registry executed the step.
    assert log, "run endpoint did not dispatch through STEP_EXECUTORS — it bypassed the unified model"


@pytest.mark.integration
def test_results_export_endpoint_routes_through_registry(client, db, tmp_path, monkeypatch):
    """#21[1] / #22[2]: the results-export endpoint (a results/staging path) runs the
    pipeline through the STEP_EXECUTORS registry — the unified execution model, with no
    superseded bypass path surviving."""
    source_id, _ = register_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    fn_path = write_fn(tmp_path, "lock_gt5", "data", "return data > 5")
    seed_validation_step(db, source_id, col_id, "lock_gt5", fn_path, position=0)

    log = _spy_registry(monkeypatch)
    resp = client.get(f"/pipelines/{source_id}/export/results?run_type=validations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "columns" in body and "rows" in body  # results-report shape preserved
    assert log, "results-export endpoint bypassed the STEP_EXECUTORS registry"
