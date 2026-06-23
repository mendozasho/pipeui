"""Behavioral guarantees for GET, POST, and DELETE /pipelines (Phase E1 / §13).

Guarantees under test (GET):
  1. Returns { source, steps: [] } for a source with no attachments.
  2. Returns 404 for an unknown source_id.
  3. Returns correctly ordered steps with full param + binding detail.
  4. pd.DataFrame params carry an empty bindings list.

Guarantees under test (POST commit):
  5. function_id body auto-creates set; returns source_function_map_id.
  6. set_id body attaches existing set; returns source_function_map_id.
  7. Missing required bindings returns 200 with ok=False + missing_params list.
  8. Unknown source_id returns 404.
  9. Successful attach reflected in subsequent GET.

Guarantees under test (POST ?dry_run=true):
  10. Returns suggested columns without writing rows to alias_map.
  11. pd.DataFrame params are excluded from dry-run response.
  12. Returns 422 when both function_id and set_id are provided.
  13. Returns 422 when neither function_id nor set_id is provided.

Guarantees under test (DELETE /pipelines/{source_id}/steps/{sfm_id}):
 14. DELETE removes source_function_map row and all alias_map rows atomically.
 15. Auto-created set with no remaining references is deleted on detach.
 16. A set referenced by another source_function_map row is NOT deleted.
 17. A user-named set is NOT deleted even if it has no remaining references.
 18. 404 returned for unknown source_function_map_id.
 19. 404 returned when sfm_id doesn't belong to the given source_id.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.middleware.pipelines import router
from pipeui.middleware.deps import get_conn
from tests.conftest import make_registered_source


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)


def _seed_pipeline(conn, source_id, column_ids):
    """Seed a pipeline with two function sets attached to source_id.

    Set A (position 0): one function with a column-backed str param
    Set B (position 1): one function with a pd.DataFrame param
    Returns (sfm_a_id, set_a_id, fn_a_id, param_a_id,
             sfm_b_id, set_b_id, fn_b_id, param_b_id)
    """
    # --- Function A: column-backed ---
    fn_a_id = uuid.uuid4()
    fn_a_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_a_id, fn_a_ch, "column_backed", "fn_alpha", "Alpha doc", "pd.Series",
         "col_param: str", "transform", "/tmp/fn_alpha.py", True],
    )
    param_a_id = uuid.uuid4()
    param_a_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_a_id, param_a_ch, "col_param", "str", fn_a_id],
    )

    # --- Function B: dataframe ---
    fn_b_id = uuid.uuid4()
    fn_b_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_b_id, fn_b_ch, "pd.dataframe", "fn_beta", "Beta doc", "pd.DataFrame",
         "df: pd.DataFrame", "transform", "/tmp/fn_beta.py", True],
    )
    param_b_id = uuid.uuid4()
    param_b_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_b_id, param_b_ch, "df", "pd.DataFrame", fn_b_id],
    )

    # --- Set A ---
    set_a_id = uuid.uuid4()
    set_a_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_a_id, set_a_ch, "Set Alpha", "Alpha desc"],
    )
    set_a_map_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_a_map_id, set_a_id, fn_a_id, 0],
    )

    # --- Set B ---
    set_b_id = uuid.uuid4()
    set_b_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_b_id, set_b_ch, "Set Beta", None],
    )
    set_b_map_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_b_map_id, set_b_id, fn_b_id, 1],
    )

    # --- Attach both sets to source ---
    sfm_a_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id) VALUES (?, ?, ?)",
        [sfm_a_id, source_id, set_a_id],
    )
    sfm_b_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id) VALUES (?, ?, ?)",
        [sfm_b_id, source_id, set_b_id],
    )

    # --- Alias binding: col_param -> column_ids[0] ---
    alias_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
        [alias_id, column_ids[0], param_a_id, source_id],
    )

    return (sfm_a_id, set_a_id, fn_a_id, param_a_id,
            sfm_b_id, set_b_id, fn_b_id, param_b_id)


@pytest.mark.integration
def test_no_attachments_returns_empty_steps(client, db):
    """Guarantee 1: source with no attached sets returns steps: []."""
    source_id, _ = make_registered_source(db)
    resp = client.get(f"/pipelines/{source_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"] == []
    assert body["source"]["source_id"] == str(source_id)


@pytest.mark.integration
def test_unknown_source_returns_404(client, db):
    """Guarantee 2: unknown source_id returns 404."""
    resp = client.get(f"/pipelines/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.integration
def test_steps_ordered_and_full_detail(client, db):
    """Guarantee 3: steps are ordered by position with full param + binding detail."""
    source_id, column_ids = make_registered_source(db, n_columns=2)
    ids = _seed_pipeline(db, source_id, column_ids)
    sfm_a_id, set_a_id, fn_a_id, param_a_id = ids[:4]
    sfm_b_id, set_b_id, fn_b_id, param_b_id = ids[4:]

    resp = client.get(f"/pipelines/{source_id}")
    assert resp.status_code == 200
    body = resp.json()
    steps = body["steps"]

    # Two steps returned
    assert len(steps) == 2

    # Ordered by position: Set Alpha (pos 0) before Set Beta (pos 1)
    assert steps[0]["set_name"] == "Set Alpha"
    assert steps[1]["set_name"] == "Set Beta"

    # Step 0: function fn_alpha with col_param str param and one binding
    fn0 = steps[0]["functions"][0]
    assert fn0["function_name"] == "fn_alpha"
    assert fn0["function_doc"] == "Alpha doc"
    assert len(fn0["params"]) == 1
    p0 = fn0["params"][0]
    assert p0["param_name"] == "col_param"
    assert p0["param_type"] == "str"
    assert len(p0["bindings"]) == 1
    assert p0["bindings"][0]["column_id"] == str(column_ids[0])


@pytest.mark.integration
def test_dataframe_param_has_empty_bindings(client, db):
    """Guarantee 4: pd.DataFrame params carry an empty bindings list."""
    source_id, column_ids = make_registered_source(db, n_columns=2)
    _seed_pipeline(db, source_id, column_ids)

    resp = client.get(f"/pipelines/{source_id}")
    assert resp.status_code == 200
    steps = resp.json()["steps"]

    # Set Beta has fn_beta with df: pd.DataFrame — bindings must be empty
    beta_step = next(s for s in steps if s["set_name"] == "Set Beta")
    fn_beta = beta_step["functions"][0]
    df_param = next(p for p in fn_beta["params"] if p["param_type"] == "pd.DataFrame")
    assert df_param["bindings"] == []


# ---------------------------------------------------------------------------
# POST /pipelines/{source_id}/steps
# ---------------------------------------------------------------------------

def _make_function(conn, fn_name: str, params: list[tuple[str, str]]) -> tuple[uuid.UUID, list[uuid.UUID]]:
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "transform", fn_name, f"Doc {fn_name}", "pd.Series",
         ", ".join(f"{n}: {t}" for n, t in params), "transform", f"/tmp/{fn_name}.py", True],
    )
    param_ids = []
    for p_name, p_type in params:
        p_id = uuid.uuid4()
        p_ch = uuid.uuid4()
        conn.execute("INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)", [p_id, p_ch, p_name, p_type, fn_id])
        param_ids.append(p_id)
    return fn_id, param_ids


@pytest.mark.integration
def test_post_function_id_attaches_and_returns_sfm_id(client, db):
    """Guarantee 5: function_id body auto-creates set; returns source_function_map_id."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_api", [("col", "str")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0])]}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "source_function_map_id" in body


@pytest.mark.integration
def test_post_set_id_attaches_existing_set(client, db):
    """Guarantee 6: set_id body attaches existing set."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_set_api", [("col", "str")])
    # Create set manually
    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set (set_id, content_hash_id, set_name, set_description) VALUES (?, ?, ?, ?)",
        [set_id, set_ch, "My Set", None],
    )
    sm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) VALUES (?, ?, ?, ?)",
        [sm_id, set_id, fn_id, 0],
    )

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "set_id": str(set_id),
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0])]}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.integration
def test_post_missing_binding_returns_structured_failure(client, db):
    """Guarantee 7: missing required bindings returns 200 with ok=False + missing_params."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_missing_api", [("col", "str")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert len(body["missing_params"]) == 1
    assert body["missing_params"][0]["param_name"] == "col"


@pytest.mark.integration
def test_post_unknown_source_returns_404(client, db):
    """Guarantee 8: unknown source_id returns 404."""
    resp = client.post(f"/pipelines/{uuid.uuid4()}/steps", json={"function_id": str(uuid.uuid4())})
    assert resp.status_code == 404


@pytest.mark.integration
def test_post_str_param_plain_string_attaches_in_one_go(client, db):
    """Bug #186 (1): POST with a str param in plain-string mode (scalar_values, no
    binding) succeeds in a single request and persists the literal."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_api_str_plain", [("label", "str")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [],
        "scalar_values": {str(param_id): "hello"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body

    # Literal persisted to source_scalar_map via the same POST
    row = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, param_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "hello"


@pytest.mark.integration
def test_post_scalar_value_persisted_and_visible_in_get(client, db):
    """Bug #186 (1)/(2): POST with scalar_values for an int param persists the value
    and GET surfaces it as scalar_value on the placed step's param."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_api_int_scalar", [("threshold", "int")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [],
        "scalar_values": {str(param_id): "11"},
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    get_resp = client.get(f"/pipelines/{source_id}")
    params = get_resp.json()["steps"][0]["functions"][0]["params"]
    threshold = next(p for p in params if p["param_name"] == "threshold")
    assert threshold["scalar_value"] == "11"


@pytest.mark.integration
def test_post_str_param_without_binding_or_value_still_fails(client, db):
    """Bug #186 (1): a str param with neither a binding nor a literal still returns
    the structured ok=False failure."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_api_str_novalue", [("label", "str")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["missing_params"][0]["param_name"] == "label"


@pytest.mark.integration
def test_post_attach_reflected_in_get(client, db):
    """Guarantee 9: successful attach reflected in subsequent GET."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_get_after_post", [("col", "str")])

    post_resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0])]}],
    })
    assert post_resp.json()["ok"] is True

    get_resp = client.get(f"/pipelines/{source_id}")
    assert get_resp.status_code == 200
    steps = get_resp.json()["steps"]
    assert len(steps) == 1
    assert steps[0]["functions"][0]["function_name"] == "fn_get_after_post"


# ---------------------------------------------------------------------------
# Slice 4 — POST attach carries replace output_targets through the API
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_post_replace_output_targets_persists_map_rows(client, db):
    """Slice 4 #2/#5: a replace attach with output_targets writes the output_target_map
    rows via the API (UI -> POST seam)."""
    source_id, col_ids = make_registered_source(db, n_columns=4)
    fn_id, (param_id,) = _make_function(db, "fn_api_replace", [("cols", "pd.Series")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "output_mode": "replace",
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0]), str(col_ids[1])]}],
        "output_targets": [str(col_ids[2]), str(col_ids[3])],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    sfm_id = body["source_function_map_id"]

    rows = db.execute(
        "SELECT column_id FROM output_target_map WHERE source_function_map_id = ? ORDER BY position",
        [sfm_id],
    ).fetchall()
    got = [str(r[0]) for r in rows]
    assert got == [str(col_ids[2]), str(col_ids[3])]


@pytest.mark.integration
def test_post_replace_target_count_mismatch_returns_ok_false_not_500(client, db):
    """Slice 4 #1: a replace attach whose target count != bundle count returns a
    structured ok=False failure (HTTP 200), never a 500."""
    source_id, col_ids = make_registered_source(db, n_columns=5)
    fn_id, (param_id,) = _make_function(db, "fn_api_mismatch", [("cols", "pd.Series")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "output_mode": "replace",
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0]), str(col_ids[1])]}],
        "output_targets": [str(col_ids[2]), str(col_ids[3]), str(col_ids[4])],  # 3 != 2
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "2" in body["detail"] and "3" in body["detail"]


@pytest.mark.integration
def test_post_append_name_persisted_via_api(client, db):
    """Slice 4b: an append attach with a user-provided append_name persists the
    (normalized) name to source_function_map via the API (modal -> POST seam)."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (param_id,) = _make_function(db, "fn_api_append", [("col", "str")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "output_mode": "append",
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0])]}],
        "append_name": "Risk Score",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    sfm_id = body["source_function_map_id"]

    row = db.execute(
        "SELECT append_name FROM source_function_map WHERE source_function_map_id = ?",
        [sfm_id],
    ).fetchone()
    assert row[0] == "Risk_Score"


# ---------------------------------------------------------------------------
# POST /pipelines/{source_id}/steps?dry_run=true
# ---------------------------------------------------------------------------

def _insert_alias(conn, column_id, param_id, source_id):
    alias_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
        [alias_id, column_id, param_id, source_id],
    )


@pytest.mark.integration
def test_dry_run_returns_suggestions_without_writing(client, db):
    """Guarantee 10: dry_run returns suggested columns without writing alias_map rows."""
    import datetime
    target_source_id, target_col_ids = make_registered_source(db, n_columns=2)
    other_source_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [other_source_id, uuid.uuid4(), f"oth_10_{other_source_id}", datetime.date.today(), "upsert", "id"],
    )

    # Add target_col_ids[0] to the other source's column map
    scm_id = uuid.uuid4()
    db.execute("INSERT INTO source_column_map VALUES (?, ?, ?)", [scm_id, target_col_ids[0], other_source_id])

    fn_id, (p_id,) = _make_function(db, "fn_dry_10", [("col", "str")])
    _insert_alias(db, target_col_ids[0], p_id, other_source_id)

    resp = client.post(
        f"/pipelines/{target_source_id}/steps?dry_run=true",
        json={"function_id": str(fn_id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "params" in body

    suggested_ids = {c["column_id"] for p in body["params"] for c in p["suggested_columns"]}
    assert str(target_col_ids[0]) in suggested_ids

    # No alias_map rows written on target source
    rows = db.execute(
        "SELECT COUNT(*) FROM alias_map WHERE source_id = ?",
        [target_source_id],
    ).fetchone()[0]
    assert rows == 0


@pytest.mark.integration
def test_dry_run_excludes_dataframe_params(client, db):
    """Guarantee 11: dry_run excludes pd.DataFrame params from the response."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, _ = _make_function(db, "fn_dry_11", [("df", "pd.DataFrame")])

    resp = client.post(
        f"/pipelines/{source_id}/steps?dry_run=true",
        json={"function_id": str(fn_id)},
    )
    assert resp.status_code == 200
    assert resp.json()["params"] == []


@pytest.mark.integration
def test_dry_run_422_when_both_ids_provided(client, db):
    """Guarantee 12: 422 when both function_id and set_id are provided."""
    source_id, _ = make_registered_source(db, n_columns=1)
    resp = client.post(
        f"/pipelines/{source_id}/steps?dry_run=true",
        json={"function_id": str(uuid.uuid4()), "set_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_dry_run_422_when_no_ids_provided(client, db):
    """Guarantee 13: 422 when neither function_id nor set_id is provided."""
    source_id, _ = make_registered_source(db, n_columns=1)
    resp = client.post(
        f"/pipelines/{source_id}/steps?dry_run=true",
        json={},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /pipelines/{source_id}/steps/{sfm_id} guarantees
# ---------------------------------------------------------------------------

def _seed_auto_set(conn, source_id, column_ids):
    """Seed one auto-created set (set_name == function_name, single member).

    Returns (sfm_id, set_id, fn_id, param_id).
    """
    fn_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "column_backed", "fn_auto", "Auto doc", "pd.Series",
         "x: str", "transform", "/tmp/fn_auto.py", True],
    )
    param_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_id, uuid.uuid4(), "x", "str", fn_id],
    )
    # Auto-created set: set_name == function_name, single member
    set_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, uuid.uuid4(), "fn_auto", None],
    )
    conn.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [uuid.uuid4(), set_id, fn_id, 0],
    )
    sfm_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id) VALUES (?, ?, ?)",
        [sfm_id, source_id, set_id],
    )
    # One alias binding
    conn.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
        [uuid.uuid4(), column_ids[0], param_id, source_id],
    )
    return sfm_id, set_id, fn_id, param_id


@pytest.mark.integration
def test_delete_removes_sfm_and_alias_map(client, db):
    """Guarantee 10: DELETE removes source_function_map + alias_map atomically."""
    source_id, column_ids = make_registered_source(db, n_columns=2)
    sfm_id, set_id, fn_id, param_id = _seed_auto_set(db, source_id, column_ids)

    # Confirm rows exist before delete
    assert db.execute("SELECT COUNT(*) FROM source_function_map WHERE source_function_map_id = ?",
                      [sfm_id]).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM alias_map WHERE source_id = ?",
                      [source_id]).fetchone()[0] == 1

    resp = client.delete(f"/pipelines/{source_id}/steps/{sfm_id}")
    assert resp.status_code == 204

    # Both rows removed
    assert db.execute("SELECT COUNT(*) FROM source_function_map WHERE source_function_map_id = ?",
                      [sfm_id]).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM alias_map WHERE source_id = ?",
                      [source_id]).fetchone()[0] == 0


@pytest.mark.integration
def test_delete_removes_auto_set_when_no_remaining_references(client, db):
    """Guarantee 11: auto-created set with no remaining sfm references is deleted."""
    source_id, column_ids = make_registered_source(db, n_columns=2)
    sfm_id, set_id, fn_id, param_id = _seed_auto_set(db, source_id, column_ids)

    resp = client.delete(f"/pipelines/{source_id}/steps/{sfm_id}")
    assert resp.status_code == 204

    # Auto-created set cleaned up
    assert db.execute("SELECT COUNT(*) FROM function_set WHERE set_id = ?",
                      [set_id]).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM function_set_map WHERE set_id = ?",
                      [set_id]).fetchone()[0] == 0


@pytest.mark.integration
def test_delete_does_not_remove_set_still_referenced(client, db):
    """Guarantee 12: set referenced by another sfm row is NOT deleted on detach."""
    source_id_a, col_ids_a = make_registered_source(db, n_columns=2)

    # Register a second source reusing the same column rows (shared column_registry).
    source_id_b = uuid.uuid4()
    import datetime as _dt
    ch_b = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_id_b, ch_b, f"test_source_{source_id_b}", _dt.date.today(), "upsert", "id"],
    )

    # Seed the auto set under source_a
    sfm_a_id, set_id, fn_id, param_id = _seed_auto_set(db, source_id_a, col_ids_a)

    # Also attach the same set to source_b (a second sfm row referencing the same set)
    sfm_b_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id) VALUES (?, ?, ?)",
        [sfm_b_id, source_id_b, set_id],
    )

    # Detach from source_a only
    resp = client.delete(f"/pipelines/{source_id_a}/steps/{sfm_a_id}")
    assert resp.status_code == 204

    # Set must survive (still referenced by source_b)
    assert db.execute("SELECT COUNT(*) FROM function_set WHERE set_id = ?",
                      [set_id]).fetchone()[0] == 1


@pytest.mark.integration
def test_delete_does_not_remove_user_named_set(client, db):
    """Guarantee 13: user-named set is NOT deleted even with no remaining references."""
    source_id, column_ids = make_registered_source(db, n_columns=2)

    # Create a user-named set: set_name != function_name
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "column_backed", "fn_named", "doc", "pd.Series",
         "x: str", "transform", "/tmp/fn_named.py", True],
    )
    set_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, uuid.uuid4(), "My Named Set", None],  # set_name != function_name
    )
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [uuid.uuid4(), set_id, fn_id, 0],
    )
    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id) VALUES (?, ?, ?)",
        [sfm_id, source_id, set_id],
    )

    resp = client.delete(f"/pipelines/{source_id}/steps/{sfm_id}")
    assert resp.status_code == 204

    # User-named set must NOT be deleted
    assert db.execute("SELECT COUNT(*) FROM function_set WHERE set_id = ?",
                      [set_id]).fetchone()[0] == 1


@pytest.mark.integration
def test_delete_unknown_sfm_returns_404(client, db):
    """Guarantee 14: 404 for unknown source_function_map_id."""
    source_id, _ = make_registered_source(db)
    resp = client.delete(f"/pipelines/{source_id}/steps/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.integration
def test_delete_wrong_source_returns_404(client, db):
    """Guarantee 15: 404 when sfm_id doesn't belong to the given source_id."""
    source_id_a, col_ids_a = make_registered_source(db, n_columns=2)

    # Register a second source without extra columns to avoid column_registry collision.
    source_id_b = uuid.uuid4()
    import datetime as _dt
    ch_b = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_id_b, ch_b, f"test_source_{source_id_b}", _dt.date.today(), "upsert", "id"],
    )

    sfm_id, _, _, _ = _seed_auto_set(db, source_id_a, col_ids_a)

    # Use source_id_b but sfm_id belongs to source_id_a
    resp = client.delete(f"/pipelines/{source_id_b}/steps/{sfm_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /pipelines/{source_id}/steps/{sfm_id}
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_patch_updates_position(client, db):
    """PATCH updates position on a source_function_map row."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    sfm_id, _, _, _ = _seed_auto_set(db, source_id, col_ids)

    resp = client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={"position": 5})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    row = db.execute(
        "SELECT position FROM source_function_map WHERE source_function_map_id = ?",
        [sfm_id],
    ).fetchone()
    assert row[0] == 5


@pytest.mark.integration
def test_patch_updates_output_mode(client, db):
    """PATCH updates output_mode on a source_function_map row."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    sfm_id, _, _, _ = _seed_auto_set(db, source_id, col_ids)

    resp = client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={"output_mode": "replace"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    row = db.execute(
        "SELECT output_mode FROM source_function_map WHERE source_function_map_id = ?",
        [sfm_id],
    ).fetchone()
    assert row[0] == "replace"


@pytest.mark.integration
def test_patch_unknown_sfm_returns_404(client, db):
    """PATCH returns 404 for an unknown source_function_map_id."""
    source_id, _ = make_registered_source(db)
    resp = client.patch(f"/pipelines/{source_id}/steps/{uuid.uuid4()}", json={"position": 1})
    assert resp.status_code == 404


@pytest.mark.integration
def test_patch_invalid_output_mode_returns_422(client, db):
    """PATCH returns 422 when output_mode is not a valid value."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    sfm_id, _, _, _ = _seed_auto_set(db, source_id, col_ids)

    resp = client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={"output_mode": "invalid"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET ordering — source_function_map.position ASC
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dry-run response shape — param_kind + available_columns (#151)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dry_run_includes_param_kind_and_available_columns(client, db):
    """Dry-run response includes param_kind and available_columns fields."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (p_str, p_int) = _make_function(db, "fn_dr_shape", [("col", "str"), ("n", "int")])

    resp = client.post(
        f"/pipelines/{source_id}/steps?dry_run=true",
        json={"function_id": str(fn_id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "available_columns" in body
    assert len(body["available_columns"]) == 2

    # param-binding-output-mode #99: param_kind is derived from binding_kind. Only a
    # column_only param (pd.Series) is "column"; a str is value_or_column → "scalar"
    # (free-text input that toggles to a column binding, same as a numeric).
    param_kinds = {p["param_name"]: p["param_kind"] for p in body["params"]}
    assert param_kinds["col"] == "scalar"
    assert param_kinds["n"] == "scalar"
    binding_kinds = {p["param_name"]: p["binding_kind"] for p in body["params"]}
    assert binding_kinds["col"] == "value_or_column"
    assert binding_kinds["n"] == "value_or_column"


@pytest.mark.integration
def test_dry_run_scalar_has_empty_suggested_columns(client, db):
    """Dry-run: scalar params have empty suggested_columns and current_scalar_value=null."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_dr_scalar", [("n", "int")])

    resp = client.post(
        f"/pipelines/{source_id}/steps?dry_run=true",
        json={"function_id": str(fn_id)},
    )
    assert resp.status_code == 200
    params = resp.json()["params"]
    assert len(params) == 1
    assert params[0]["param_kind"] == "scalar"
    assert params[0]["suggested_columns"] == []
    assert params[0]["current_scalar_value"] is None


# ---------------------------------------------------------------------------
# PATCH with scalar_values and bindings (#151)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_patch_scalar_values_upserts_and_returns_ok(client, db):
    """PATCH with scalar_values upserts into source_scalar_map and returns ok=true."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_api_scalar", [("n", "int")])
    sfm_id, _, _, _ = _seed_auto_set(db, source_id, col_ids)

    resp = client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={
        "scalar_values": {str(p_id): "7"},
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    row = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "7"


@pytest.mark.integration
def test_patch_scalar_values_second_call_updates_no_duplicate(client, db):
    """Second PATCH with same (source_id, param_id) updates value — no duplicate row."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_api_scalar2", [("n", "int")])
    sfm_id, _, _, _ = _seed_auto_set(db, source_id, col_ids)

    client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={"scalar_values": {str(p_id): "1"}})
    client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={"scalar_values": {str(p_id): "2"}})

    rows = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2"


@pytest.mark.integration
def test_patch_bindings_replaces_alias_map(client, db):
    """PATCH with bindings replaces all alias_map rows for that step."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    sfm_id, set_id, fn_id, param_id = _seed_auto_set(db, source_id, col_ids)

    # Initially bound to col_ids[0] (from _seed_auto_set); patch to col_ids[1]
    resp = client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={
        "bindings": {str(param_id): [str(col_ids[1])]},
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    rows = db.execute(
        "SELECT column_id FROM alias_map WHERE parameter_id = ? AND source_id = ?",
        [param_id, source_id],
    ).fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == str(col_ids[1])


# ---------------------------------------------------------------------------
# GET ordering — source_function_map.position ASC
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_get_pipeline_ordered_by_sfm_position(client, db):
    """GET returns steps ordered by source_function_map.position ASC."""
    source_id, col_ids = make_registered_source(db, n_columns=1)

    # Attach two functions; attach_function assigns position 0, then 1
    fn_a_id, _ = _make_function(db, "fn_order_a", [("df", "pd.DataFrame")])
    fn_b_id, _ = _make_function(db, "fn_order_b", [("df", "pd.DataFrame")])

    resp_a = client.post(f"/pipelines/{source_id}/steps", json={"function_id": str(fn_a_id)})
    assert resp_a.json()["ok"] is True
    sfm_a_id = resp_a.json()["source_function_map_id"]

    resp_b = client.post(f"/pipelines/{source_id}/steps", json={"function_id": str(fn_b_id)})
    assert resp_b.json()["ok"] is True

    # Reorder: set fn_a to position 10 (higher than fn_b's position 1)
    client.patch(f"/pipelines/{source_id}/steps/{sfm_a_id}", json={"position": 10})

    get_resp = client.get(f"/pipelines/{source_id}")
    assert get_resp.status_code == 200
    steps = get_resp.json()["steps"]
    assert len(steps) == 2
    # fn_b (position 1) should come before fn_a (position 10)
    assert steps[0]["functions"][0]["function_name"] == "fn_order_b"
    assert steps[1]["functions"][0]["function_name"] == "fn_order_a"


# ---------------------------------------------------------------------------
# GET /pipelines/{source_id} — placed built-in steps in the canvas list (#209)
# ---------------------------------------------------------------------------

def _make_right_source(conn):
    """Register a second source with a uniquely-named column (avoids the
    column_registry content_hash_id collision a second make_registered_source
    would cause). Returns (source_id, column_name)."""
    import datetime as _dt
    from pipeui.backend.data.base.ids import content_hash_id as _ch
    src_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [src_id, uuid.uuid4(), f"right_{src_id}", _dt.date.today(), "upsert", "id"],
    )
    col_id = uuid.uuid4()
    col_name = f"rcol_{src_id.hex[:6]}"
    conn.execute(
        "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
        [col_id, _ch("column_registry", col_name, "INTEGER"), col_name, "INTEGER"],
    )
    conn.execute(
        "INSERT INTO source_column_map VALUES (?, ?, ?)",
        [_ch("source_column_map", str(src_id), str(col_id)), col_id, src_id],
    )
    return src_id, col_name


@pytest.mark.integration
def test_get_pipeline_returns_builtin_step_with_discriminator(client, db):
    """#209 AC1: GET /pipelines/{source_id} returns a placed built-in step in
    steps[] with step_type='builtin', its builtin_type and builtin_config."""
    from pipeui.backend.domain.functions.builtins import attach_builtin

    source_id, _ = make_registered_source(db, n_columns=1)
    right_id, rcol = _make_right_source(db)
    cfg = {
        "right_source_id": str(right_id),
        "join_type": "inner",
        "on": [{"left_col": "col_0", "right_col": rcol}],
        "keep_columns": "all",
    }
    res = attach_builtin(db, source_id, "join", cfg)
    assert res["ok"] is True

    resp = client.get(f"/pipelines/{source_id}")
    assert resp.status_code == 200
    steps = resp.json()["steps"]
    assert len(steps) == 1
    bstep = steps[0]
    assert bstep["step_type"] == "builtin"
    assert bstep["builtin_type"] == "join"
    assert bstep["step_id"] == res["step_id"]
    assert bstep["builtin_config"]["join_type"] == "inner"


@pytest.mark.integration
def test_get_pipeline_function_step_carries_step_type_no_regression(client, db):
    """#209 AC2: a function step in the GET response carries step_type='function'
    and still includes its full nested functions[] payload (no regression)."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_disc", [("col", "str")])
    post = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [{"param_id": str(param_id), "column_ids": [str(col_ids[0])]}],
    })
    assert post.json()["ok"] is True

    steps = client.get(f"/pipelines/{source_id}").json()["steps"]
    assert steps[0]["step_type"] == "function"
    assert steps[0]["functions"][0]["function_name"] == "fn_disc"


@pytest.mark.integration
def test_get_pipeline_interleaves_builtin_and_function_by_position(client, db):
    """#209 AC1: function (position 0) and built-in (position 1) come back ordered
    by position with the correct discriminators."""
    from pipeui.backend.domain.functions.builtins import attach_builtin

    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, _ = _make_function(db, "fn_inter", [("df", "pd.DataFrame")])
    client.post(f"/pipelines/{source_id}/steps", json={"function_id": str(fn_id)})

    right_id, rcol = _make_right_source(db)
    attach_builtin(db, source_id, "join", {
        "right_source_id": str(right_id),
        "join_type": "left",
        "on": [{"left_col": "col_0", "right_col": rcol}],
        "keep_columns": "all",
    })

    steps = client.get(f"/pipelines/{source_id}").json()["steps"]
    assert [s["step_type"] for s in steps] == ["function", "builtin"]


# ---------------------------------------------------------------------------
# Slice 2 — PATCH rewrites alias_map column positions; new order reads back
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_patch_rewrites_column_positions_and_reads_back(client, db):
    """Slice 2 #3: PATCH rewrites a parameter's column positions in the provided
    order; GET reads the bindings back in that new order."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    fn_id, (param_id,) = _make_function(db, "fn_patch_pos", [("cols", "pd.Series")])

    # Attach with add-order col_0, col_1, col_2
    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [{"param_id": str(param_id), "column_ids": [str(c) for c in col_ids]}],
    })
    assert resp.json()["ok"] is True
    sfm_id = resp.json()["source_function_map_id"]

    # PATCH to a new order: col_2, col_0, col_1
    new_order = [str(col_ids[2]), str(col_ids[0]), str(col_ids[1])]
    presp = client.patch(f"/pipelines/{source_id}/steps/{sfm_id}", json={
        "bindings": {str(param_id): new_order},
    })
    assert presp.status_code == 200
    assert presp.json() == {"ok": True}

    # alias_map.position reflects the new order
    rows = db.execute(
        "SELECT column_id, position FROM alias_map WHERE parameter_id = ? AND source_id = ? ORDER BY position",
        [param_id, source_id],
    ).fetchall()
    assert [str(cid) for cid, _ in rows] == new_order
    assert [pos for _, pos in rows] == [0, 1, 2]

    # GET reads bindings back in the new order
    get_resp = client.get(f"/pipelines/{source_id}")
    fn = get_resp.json()["steps"][0]["functions"][0]
    param = next(p for p in fn["params"] if p["param_id"] == str(param_id))
    assert [b["column_name"] for b in param["bindings"]] == ["col_2", "col_0", "col_1"]


# ---------------------------------------------------------------------------
# Slice runner-execution/3 — acceptance #1: an equal-length-among-varying
# violation at attach returns a STRUCTURED failure, never a 500.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_post_unequal_varying_attach_returns_structured_failure_not_500(client, db):
    """A multi-bind attach with mismatched varying column counts (3,2) returns a
    structured failure body (ok=False with detail) at 200 — not a 500."""
    source_id, col_ids = make_registered_source(db, n_columns=5)
    fn_id, (p_a, p_b) = _make_function(db, "fn_api_unequal", [("a", "pd.Series"), ("b", "pd.Series")])

    resp = client.post(f"/pipelines/{source_id}/steps", json={
        "function_id": str(fn_id),
        "bindings": [
            {"param_id": str(p_a), "column_ids": [str(c) for c in col_ids[:3]]},
            {"param_id": str(p_b), "column_ids": [str(c) for c in col_ids[3:5]]},
        ],
    })
    assert resp.status_code != 500
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "3" in body["detail"] and "2" in body["detail"]


# ---------------------------------------------------------------------------
# Slice runner-execution/5 — #243: the transformed-report export route exists
# and returns the empty-payload contract for a registered source with no
# transform run yet (route-existence + shape guard at the pipelines seam).
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_transformed_export_route_returns_empty_payload_for_registered_source(client, db):
    """GET /pipelines/{source_id}/export/transformed exists and returns {columns,rows}.

    A registered source with no transform run yet has no staging table, so the
    transformed report is an empty payload (200) — never a 404 (source exists) or 500.
    """
    source_id, _ = make_registered_source(db, n_columns=2)
    resp = client.get(f"/pipelines/{source_id}/export/transformed")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"columns": [], "rows": []}


# ---------------------------------------------------------------------------
# Slice 2 (#17) — right-column-fetch endpoint honors the transformed flag.
#
#   AC3. The right-column-fetch endpoint returns the right source's TRANSFORMED
#        column set when the transformed flag is set, and the RAW columns when not.
# ---------------------------------------------------------------------------

@pytest.fixture
def sources_client(db):
    """TestClient for the sources router wired to the test's DuckDB sandbox."""
    from pipeui.middleware.sources import router as sources_router
    from pipeui.middleware.sources import get_conn as sources_get_conn

    app = FastAPI()
    app.include_router(sources_router)
    app.dependency_overrides[sources_get_conn] = lambda: db
    yield TestClient(app)


@pytest.mark.integration
def test_join_columns_endpoint_raw_vs_transformed(sources_client, db):
    """AC3: /sources/{id}/join-columns returns raw columns by default and the
    transformed column set when transformed=true.

    Raw = the source's registered columns. Transformed = the columns of the source's
    resolved transformed output (its latest staging table), which here carries an
    extra column the raw table does not.
    """
    from pipeui.backend.data.base.tables import instance_table_name

    source_id, _ = make_registered_source(db, n_columns=2)  # raw cols: col_0, col_1

    # Build the raw instance table and a transformed staging table with an extra column.
    db.execute(
        f'CREATE TABLE "{instance_table_name(source_id)}" AS '
        "SELECT * FROM (VALUES (1, 10)) AS t(col_0, col_1)"
    )
    db.execute(
        f'CREATE TABLE "staging_{source_id.hex[:8]}_2000" AS '
        "SELECT * FROM (VALUES (1, 10, 'x')) AS t(col_0, col_1, derived)"
    )

    # Raw (no flag): the registered raw column set, no 'derived'.
    raw_resp = sources_client.get(f"/sources/{source_id}/join-columns")
    assert raw_resp.status_code == 200, raw_resp.text
    raw_names = [c["column_name"] for c in raw_resp.json()["columns"]]
    assert "derived" not in raw_names
    assert {"col_0", "col_1"} <= set(raw_names)

    # Transformed flag: the transformed column set, including 'derived'.
    xf_resp = sources_client.get(f"/sources/{source_id}/join-columns?transformed=true")
    assert xf_resp.status_code == 200, xf_resp.text
    xf_names = [c["column_name"] for c in xf_resp.json()["columns"]]
    assert "derived" in xf_names


@pytest.mark.integration
def test_join_columns_endpoint_404_unknown_source(sources_client):
    """AC3 guard: an unknown source_id is a 404, never a 500."""
    resp = sources_client.get(f"/sources/{uuid.uuid4()}/join-columns")
    assert resp.status_code == 404
