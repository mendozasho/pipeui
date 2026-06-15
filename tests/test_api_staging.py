"""Behavioral guarantees for GET /pipelines/{source_id}/staging (§13).

Guarantees under test:
  1. Returns {"columns": [], "rows": []} before any transform run.
  2. Returns rows and column names after a transform run that writes a staging table.
  3. Returns 404 for an unknown source_id.
"""
from __future__ import annotations

import csv
import time
import uuid

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.api.pipelines import router, get_conn
from pipeui.workflow.create import create_source
from pipeui.workflow.ingestion import ingest_source
from pipeui.workflow.run import _staging_prefix, _write_staging_table


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)


def _make_csv(tmp_path, name, columns, rows):
    p = tmp_path / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return str(p)


def _register_and_ingest(db, tmp_path):
    csv_path = _make_csv(
        tmp_path, "src.csv",
        ["id", "val"],
        [["1", "10"], ["2", "20"], ["3", "30"]],
    )
    source_id, _ = create_source(
        conn=db,
        file_path=csv_path,
        source_name="staging_test",
        primary_key="id",
        ingestion_method="upsert",
    )
    ingest_source(conn=db, source_id=source_id, file_path=csv_path)
    return source_id


@pytest.mark.integration
def test_staging_returns_empty_before_any_transform_run(client, db, tmp_path):
    """Guarantee 1: GET /staging returns empty columns/rows when no staging table exists yet."""
    source_id = _register_and_ingest(db, tmp_path)

    resp = client.get(f"/pipelines/{source_id}/staging")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"columns": [], "rows": []}, (
        "Expected empty payload before any transform run; got: " + str(data)
    )


@pytest.mark.integration
def test_staging_returns_rows_after_transform_run(client, db, tmp_path):
    """Guarantee 2: GET /staging returns rows and columns after a transform writes a staging table."""
    source_id = _register_and_ingest(db, tmp_path)

    # Manually write a staging table (simulating what run_pipeline does after a transform)
    df = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30], "doubled": [20, 40, 60]})
    ts = int(time.time())
    _write_staging_table(db, source_id, df, ts)

    resp = client.get(f"/pipelines/{source_id}/staging")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data["columns"]) == {"id", "val", "doubled"}, (
        f"Unexpected columns: {data['columns']}"
    )
    assert len(data["rows"]) == 3, f"Expected 3 rows, got {len(data['rows'])}"
    # Verify the 'doubled' column values are correct
    doubled_vals = sorted(r["doubled"] for r in data["rows"])
    assert doubled_vals == [20, 40, 60], f"Unexpected doubled values: {doubled_vals}"


@pytest.mark.integration
def test_staging_export_scrubs_nan_and_inf_to_null(client, db, tmp_path):
    """#262: real source data contains nulls -> NaN in the staging dataframe. The
    transformed-report export must render NaN/None/inf as JSON null, not 500. The
    slice-5 tests used null-free data, so this crash ('nan not JSON compliant') had
    no coverage."""
    source_id = _register_and_ingest(db, tmp_path)
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "monthly_spend": [10.5, float("nan"), 30.0],   # NaN (a real null)
        "ratio": [1.0, 2.0, float("inf")],             # inf
        "name": ["a", None, "c"],                       # object-column null
    })
    ts = int(time.time())
    _write_staging_table(db, source_id, df, ts)

    resp = client.get(f"/pipelines/{source_id}/staging")
    assert resp.status_code == 200, resp.text   # was 500: nan not JSON compliant
    data = resp.json()
    assert set(data["columns"]) == {"id", "monthly_spend", "ratio", "name"}
    by_id = {r["id"]: r for r in data["rows"]}
    assert by_id[2]["monthly_spend"] is None     # NaN -> null
    assert by_id[2]["name"] is None              # None -> null
    assert by_id[3]["ratio"] is None             # inf -> null
    assert by_id[1]["monthly_spend"] == 10.5     # real values preserved


@pytest.mark.integration
def test_staging_returns_404_for_unknown_source(client, db):
    """Guarantee 3: GET /staging returns 404 for an unknown source_id."""
    unknown_id = str(uuid.uuid4())
    resp = client.get(f"/pipelines/{unknown_id}/staging")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Slice runner-execution/5 — #243: transformed report export (slice #2) and the
# #193 mixed validation/transform staging-export fix (slice #4).
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_transformed_export_returns_table_after_transforms(client, db, tmp_path):
    """#243 (slice #2): exporting a source's transformed report -> its transformed data table.

    After a transform has written a staging table, GET /export/transformed returns the
    transformed data (columns + rows), the transformed-report contract.
    """
    source_id = _register_and_ingest(db, tmp_path)

    df = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30], "doubled": [20, 40, 60]})
    ts = int(time.time())
    _write_staging_table(db, source_id, df, ts)

    resp = client.get(f"/pipelines/{source_id}/export/transformed")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data["columns"]) == {"id", "val", "doubled"}
    assert len(data["rows"]) == 3
    doubled_vals = sorted(r["doubled"] for r in data["rows"])
    assert doubled_vals == [20, 40, 60]


@pytest.mark.integration
def test_transformed_export_404_for_unknown_source(client, db):
    """#243 (slice #2): GET /export/transformed returns 404 for an unknown source_id."""
    resp = client.get(f"/pipelines/{uuid.uuid4()}/export/transformed")
    assert resp.status_code == 404, resp.text


@pytest.mark.integration
def test_transformed_export_does_not_fail_for_validation_only_mixed_set(client, db, tmp_path):
    """#243 (slice #4 / #193): the staging-export path no longer fails for a mixed set.

    A validation-only (no transform) run writes no staging table. Exporting the
    transformed report must return an empty payload (200), NOT raise/500 — this is the
    #193 staging-export-failure symptom for a mixed validation/transform set.
    """
    source_id = _register_and_ingest(db, tmp_path)

    # No transform has run -> no staging table exists.
    resp = client.get(f"/pipelines/{source_id}/export/transformed")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"columns": [], "rows": []}
