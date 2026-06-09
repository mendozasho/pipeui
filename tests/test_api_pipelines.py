"""Behavioral guarantees for GET /pipelines/{source_id} (Phase E1 / §13).

Guarantees under test:
  1. Returns { source, steps: [] } for a source with no attachments.
  2. Returns 404 for an unknown source_id.
  3. Returns correctly ordered steps with full param + binding detail.
  4. pd.DataFrame params carry an empty bindings list.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeui.api.pipelines import router
from pipeui.helpers import get_conn
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
        "INSERT INTO source_function_map VALUES (?, ?, ?)",
        [sfm_a_id, source_id, set_a_id],
    )
    sfm_b_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO source_function_map VALUES (?, ?, ?)",
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
