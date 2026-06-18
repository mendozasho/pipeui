"""Behavioral guarantee tests for POST /function-sets and GET /function-sets — Phase D2 (§13)."""
from __future__ import annotations

import textwrap
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pipeui.api.function_sets as fs_mod
from pipeui.backend.data.base.db import create_schema, get_connection
from pipeui.workflow.functions import scan_functions


@pytest.fixture
def fs_client(tmp_path):
    conn = get_connection(":memory:")
    create_schema(conn)

    def override_conn():
        yield conn

    app = FastAPI()
    app.include_router(fs_mod.router)
    app.dependency_overrides[fs_mod.get_conn] = override_conn
    return TestClient(app), conn, tmp_path


def _register_functions(conn, tmp_path, src: str) -> list[str]:
    p = tmp_path / f"fn_{uuid.uuid4().hex[:6]}.py"
    p.write_text(textwrap.dedent(src))
    scan_functions(conn, [str(tmp_path)])
    rows = conn.execute(
        "SELECT function_id FROM function_registry ORDER BY function_name"
    ).fetchall()
    return [str(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# GET /function-sets
# ---------------------------------------------------------------------------

class TestGetFunctionSets:
    @pytest.mark.integration
    def test_returns_empty_list_initially(self, fs_client):
        """Guarantee: GET /function-sets returns [] when no sets exist."""
        client, conn, _ = fs_client
        res = client.get("/function-sets")
        assert res.status_code == 200
        assert res.json() == []

    @pytest.mark.integration
    def test_returns_sets_with_correct_fields(self, fs_client):
        """Guarantee: GET /function-sets returns sets with all summary fields."""
        client, conn, tmp_path = fs_client
        fn_ids = _register_functions(conn, tmp_path, """
            def fn_a(x: int) -> int: return x
        """)
        client.post("/function-sets", json={"set_name": "my_set", "set_description": "desc", "members": fn_ids})
        res = client.get("/function-sets")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        s = data[0]
        assert s["set_name"] == "my_set"
        assert s["set_description"] == "desc"
        assert s["member_count"] == 1
        assert s["has_inactive"] is False
        assert "set_id" in s

    @pytest.mark.integration
    def test_has_inactive_surfaced_correctly(self, fs_client):
        """Guarantee: GET /function-sets returns has_inactive=true when a member is inactive."""
        client, conn, tmp_path = fs_client
        fn_ids = _register_functions(conn, tmp_path, """
            def fn_a(x: int) -> int: return x
        """)
        client.post("/function-sets", json={"set_name": "iset", "members": fn_ids})
        conn.execute("UPDATE function_registry SET is_active = false WHERE function_id = ?", [fn_ids[0]])
        res = client.get("/function-sets")
        assert res.json()[0]["has_inactive"] is True


# ---------------------------------------------------------------------------
# POST /function-sets
# ---------------------------------------------------------------------------

class TestPostFunctionSet:
    @pytest.mark.integration
    def test_creates_set_successfully(self, fs_client):
        """Guarantee: POST /function-sets with valid payload returns ok=true and set summary."""
        client, conn, tmp_path = fs_client
        fn_ids = _register_functions(conn, tmp_path, """
            def fn_a(x: int) -> int: return x
            def fn_b(x: int) -> int: return x
        """)
        res = client.post("/function-sets", json={
            "set_name": "new_set",
            "set_description": "a desc",
            "members": fn_ids,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["set"]["set_name"] == "new_set"
        assert data["set"]["member_count"] == 2

    @pytest.mark.integration
    def test_duplicate_name_returns_structured_failure(self, fs_client):
        """Guarantee: POST with duplicate set_name returns 422 structured failure, not 500."""
        client, conn, _ = fs_client
        client.post("/function-sets", json={"set_name": "dup", "members": []})
        res = client.post("/function-sets", json={"set_name": "dup", "members": []})
        assert res.status_code == 422
        data = res.json()
        assert data["ok"] is False
        assert "errors" in data
        assert len(data["errors"]) > 0

    @pytest.mark.integration
    def test_creates_set_with_no_members(self, fs_client):
        """Guarantee: POST with empty members list succeeds."""
        client, conn, _ = fs_client
        res = client.post("/function-sets", json={"set_name": "empty", "members": []})
        assert res.status_code == 200
        assert res.json()["set"]["member_count"] == 0


# ---------------------------------------------------------------------------
# GET /function-sets/{id}
# ---------------------------------------------------------------------------

class TestGetFunctionSetDetail:
    @pytest.mark.integration
    def test_returns_404_for_unknown_id(self, fs_client):
        """Guarantee: GET /function-sets/{id} returns 404 for unknown id."""
        client, conn, _ = fs_client
        res = client.get(f"/function-sets/{uuid.uuid4()}")
        assert res.status_code == 404

    @pytest.mark.integration
    def test_returns_full_detail_with_members(self, fs_client):
        """Guarantee: GET /function-sets/{id} returns full detail with ordered members."""
        client, conn, tmp_path = fs_client
        fn_ids = _register_functions(conn, tmp_path, """
            def fn_a(x: int) -> int: return x
            def fn_b(x: int) -> int: return x
        """)
        post = client.post("/function-sets", json={
            "set_name": "detail_set", "set_description": "desc", "members": fn_ids,
        })
        set_id = post.json()["set"]["set_id"]
        res = client.get(f"/function-sets/{set_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["set_name"] == "detail_set"
        assert data["set_description"] == "desc"
        assert len(data["members"]) == 2
        assert [m["position"] for m in data["members"]] == [0, 1]


# ---------------------------------------------------------------------------
# PATCH /function-sets/{id}
# ---------------------------------------------------------------------------

class TestPatchFunctionSet:
    @pytest.mark.integration
    def test_returns_404_for_unknown_id(self, fs_client):
        """Guarantee: PATCH /function-sets/{id} returns 404 for unknown id."""
        client, conn, _ = fs_client
        res = client.patch(f"/function-sets/{uuid.uuid4()}", json={"set_name": "x"})
        assert res.status_code == 404

    @pytest.mark.integration
    def test_rename_succeeds(self, fs_client):
        """Guarantee: PATCH with set_name updates the name and returns ok=true."""
        client, conn, _ = fs_client
        post = client.post("/function-sets", json={"set_name": "old", "members": []})
        set_id = post.json()["set"]["set_id"]
        res = client.patch(f"/function-sets/{set_id}", json={"set_name": "new"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["set"]["set_name"] == "new"

    @pytest.mark.integration
    def test_rename_collision_returns_422(self, fs_client):
        """Guarantee: PATCH renaming to a taken name returns 422 structured failure."""
        client, conn, _ = fs_client
        client.post("/function-sets", json={"set_name": "taken", "members": []})
        post2 = client.post("/function-sets", json={"set_name": "mine", "members": []})
        set_id = post2.json()["set"]["set_id"]
        res = client.patch(f"/function-sets/{set_id}", json={"set_name": "taken"})
        assert res.status_code == 422
        assert res.json()["ok"] is False

    @pytest.mark.integration
    def test_replace_members_updates_pipeline(self, fs_client):
        """Guarantee: PATCH with members replaces existing members."""
        client, conn, tmp_path = fs_client
        fn_ids = _register_functions(conn, tmp_path, """
            def fn_a(x: int) -> int: return x
            def fn_b(x: int) -> int: return x
        """)
        post = client.post("/function-sets", json={"set_name": "pipeline", "members": [fn_ids[0]]})
        set_id = post.json()["set"]["set_id"]
        res = client.patch(f"/function-sets/{set_id}", json={"members": [fn_ids[1]]})
        assert res.status_code == 200
        members = res.json()["set"]["members"]
        assert len(members) == 1
        assert members[0]["function_id"] == fn_ids[1]


# ---------------------------------------------------------------------------
# DELETE /function-sets/{id}
# ---------------------------------------------------------------------------

class TestDeleteFunctionSet:
    @pytest.mark.integration
    def test_returns_404_for_unknown_id(self, fs_client):
        """Guarantee: DELETE /function-sets/{id} returns 404 for unknown id."""
        client, conn, _ = fs_client
        res = client.delete(f"/function-sets/{uuid.uuid4()}")
        assert res.status_code == 404

    @pytest.mark.integration
    def test_returns_204_and_set_is_gone(self, fs_client):
        """Guarantee: DELETE returns 204 and the set no longer appears in GET /function-sets."""
        client, conn, _ = fs_client
        post = client.post("/function-sets", json={"set_name": "bye", "members": []})
        set_id = post.json()["set"]["set_id"]
        res = client.delete(f"/function-sets/{set_id}")
        assert res.status_code == 204
        sets = client.get("/function-sets").json()
        assert all(s["set_id"] != set_id for s in sets)

    @pytest.mark.integration
    def test_member_functions_survive(self, fs_client):
        """Guarantee: DELETE does not remove member functions from function_registry."""
        client, conn, tmp_path = fs_client
        fn_ids = _register_functions(conn, tmp_path, """
            def fn_a(x: int) -> int: return x
        """)
        post = client.post("/function-sets", json={"set_name": "survivor", "members": fn_ids})
        set_id = post.json()["set"]["set_id"]
        client.delete(f"/function-sets/{set_id}")
        # Check DB directly — function must still exist in function_registry
        row = conn.execute(
            "SELECT 1 FROM function_registry WHERE function_id = ?", [fn_ids[0]]
        ).fetchone()
        assert row is not None
