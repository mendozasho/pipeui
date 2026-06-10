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

from pipeui.api.pipelines import router
from pipeui.db import get_conn
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
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO alias_map VALUES (?, ?, ?, ?)",
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
        conn.execute("INSERT INTO parameter VALUES (?, ?, ?, ?, ?)", [p_id, p_ch, p_name, p_type, fn_id])
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
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO alias_map VALUES (?, ?, ?, ?)",
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
