"""
Phase A behavioral guarantees for /sources endpoints.

Guarantees under test (ROADMAP.md feat/api-sources-register):
  1. POST /sources with a valid CSV returns a source record (ok: true).
  2. POST /sources with a valid .xlsx returns a source record (ok: true).
  3. POST /sources with an unsupported file type returns 422, not a 500.
  4. POST /sources when create_source fails returns a failure payload, not a 500.
  5. GET /sources returns the list of registered sources.
"""
import io
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.api.sources import router, get_conn


@pytest.fixture
def client(db):
    """TestClient wired to the test's in-memory DuckDB sandbox."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)


def _csv_file(content: str = "id,name,value\n1,alice,10\n2,bob,20\n"):
    return ("test.csv", io.BytesIO(content.encode()), "text/csv")


def _xlsx_file():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "name", "value"])
    ws.append([1, "alice", 10])
    ws.append([2, "bob", 20])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return ("test.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@pytest.mark.integration
def test_post_sources_csv_returns_source_record(client):
    """POST /sources with a valid CSV returns ok:true and a source record."""
    name, data, mime = _csv_file()
    resp = client.post(
        "/sources",
        data={"source_name": "test_csv", "primary_key": "id"},
        files={"file": (name, data, mime)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["source"]["source_name"] == "test_csv"
    assert body["source"]["primary_key"] == "id"
    assert isinstance(body["source"]["columns"], list)


@pytest.mark.integration
def test_post_sources_xlsx_returns_source_record(client):
    """POST /sources with a valid .xlsx returns ok:true and a source record."""
    name, data, mime = _xlsx_file()
    resp = client.post(
        "/sources",
        data={"source_name": "test_xlsx", "primary_key": "id"},
        files={"file": (name, data, mime)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["source"]["source_name"] == "test_xlsx"


@pytest.mark.integration
def test_post_sources_bad_file_type_returns_422_not_500(client):
    """POST /sources with an unsupported file type returns 422, never 500."""
    resp = client.post(
        "/sources",
        data={"source_name": "bad", "primary_key": "id"},
        files={"file": ("bad.json", io.BytesIO(b"{}"), "application/json")},
    )
    assert resp.status_code == 422
    assert resp.status_code != 500


@pytest.mark.integration
def test_post_sources_failure_returns_failure_payload_not_500(client):
    """POST /sources when create_source fails returns a failure payload, not a 500."""
    name, data, mime = _csv_file()
    resp = client.post(
        "/sources",
        data={"source_name": "fail_test", "primary_key": "id", "ingestion_method": "INVALID"},
        files={"file": (name, data, mime)},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) > 0


@pytest.mark.integration
def test_get_sources_returns_registered_sources(client):
    """GET /sources returns all registered sources."""
    for i in range(2):
        name, data, mime = _csv_file(f"id,val\n{i},x\n")
        client.post(
            "/sources",
            data={"source_name": f"src_{i}", "primary_key": "id"},
            files={"file": (name, data, mime)},
        )

    resp = client.get("/sources")
    assert resp.status_code == 200
    sources = resp.json()
    assert len(sources) >= 2
    names = [s["source_name"] for s in sources]
    assert "src_0" in names
    assert "src_1" in names
