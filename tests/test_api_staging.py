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
def test_staging_returns_404_for_unknown_source(client, db):
    """Guarantee 3: GET /staging returns 404 for an unknown source_id."""
    unknown_id = str(uuid.uuid4())
    resp = client.get(f"/pipelines/{unknown_id}/staging")
    assert resp.status_code == 404, resp.text
