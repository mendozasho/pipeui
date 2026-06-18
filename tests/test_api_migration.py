"""Behavioral guarantees for PATCH /sources/{source_id}/columns/{col_id} (§7 / §13).

Guarantees under test:
  1. PATCH with dry_run=true returns castable/uncastable counts without mutating.
  2. PATCH commit on a clean migration returns ok=True and rows_migrated.
  3. PATCH returns 404 for unknown source_id.
  4. PATCH returns 404 for unknown col_id.
  5. PATCH returns a structured failure (not 500) for an invalid column_type.
"""
from __future__ import annotations

import csv
import io
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.api.sources import router, get_conn
from pipeui.backend.domain.sources.create import create_source
from pipeui.backend.domain.sources.ingestion import ingest_source
from tests.conftest import make_registered_source


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
    """Register and ingest a small source with one INTEGER column called 'val'."""
    csv_path = _make_csv(
        tmp_path, "src.csv",
        ["id", "val"],
        [["1", "10"], ["2", "20"]],
    )
    source_id, _ = create_source(
        conn=db,
        file_path=csv_path,
        source_name="migtest",
        primary_key="id",
        ingestion_method="upsert",
    )
    ingest_source(conn=db, source_id=source_id, file_path=csv_path)
    # Fetch col_id for 'val'
    row = db.execute(
        """
        SELECT cr.column_id FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ? AND cr.column_name = 'val'
        """,
        [source_id],
    ).fetchone()
    col_id = row[0]
    return source_id, col_id


@pytest.mark.integration
def test_dry_run_returns_counts_without_mutation(client, db, tmp_path):
    """Guarantee 1: dry_run=true returns castable/uncastable counts without mutating data."""
    source_id, col_id = _register_and_ingest(db, tmp_path)

    resp = client.patch(
        f"/sources/{source_id}/columns/{col_id}?dry_run=true",
        json={"column_type": "VARCHAR", "scope": "this_source", "on_uncastable": "abort"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body.get("dry_run") is True
    assert "castable" in body
    assert "uncastable" in body
    assert "shared_sources" in body

    # Verify the column type was NOT changed — original row still maps this source
    mapping = db.execute(
        "SELECT cr.column_type FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()
    assert mapping is not None
    assert mapping[0] != "VARCHAR"  # dry_run must not have committed the change


@pytest.mark.integration
def test_commit_clean_migration_returns_ok_and_rows_migrated(client, db, tmp_path):
    """Guarantee 2: commit on a zero-uncastable migration returns ok=True and rows_migrated."""
    source_id, col_id = _register_and_ingest(db, tmp_path)

    # INTEGER -> VARCHAR is always safe (widening)
    resp = client.patch(
        f"/sources/{source_id}/columns/{col_id}",
        json={"column_type": "VARCHAR", "scope": "this_source", "on_uncastable": "nullify"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "rows_migrated" in body
    assert isinstance(body["rows_migrated"], int)
    assert body["rows_migrated"] >= 0
    assert isinstance(body["nullified"], list)


@pytest.mark.integration
def test_patch_unknown_source_returns_404(client, db):
    """Guarantee 3: unknown source_id returns 404."""
    fake_source = uuid.uuid4()
    fake_col = uuid.uuid4()
    resp = client.patch(
        f"/sources/{fake_source}/columns/{fake_col}",
        json={"column_type": "VARCHAR"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_patch_unknown_col_returns_404(client, db, tmp_path):
    """Guarantee 4: source exists but col_id does not exist at all -> 404."""
    source_id, _ = _register_and_ingest(db, tmp_path)
    fake_col = uuid.uuid4()
    resp = client.patch(
        f"/sources/{source_id}/columns/{fake_col}",
        json={"column_type": "VARCHAR"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_patch_invalid_column_type_returns_structured_failure_not_500(client, db, tmp_path):
    """Guarantee 5: invalid column_type returns a structured failure payload, not a 500."""
    source_id, col_id = _register_and_ingest(db, tmp_path)
    resp = client.patch(
        f"/sources/{source_id}/columns/{col_id}",
        json={"column_type": "EXOTIC_TYPE"},
    )
    assert resp.status_code == 422
    assert resp.status_code != 500
    body = resp.json()
    assert body["ok"] is False
    assert "error" in body
