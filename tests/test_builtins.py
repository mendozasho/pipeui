"""Behavioral guarantees for built-in pipeline steps (join, pivot, filter).

Guarantees:
  B1. attach_builtin creates a source_builtin_map row for a valid join config.
  B2. attach_builtin creates a source_builtin_map row for a valid pivot config.
  B3. attach_builtin returns ok=False for an unknown builtin_type.
  B4. attach_builtin returns ok=False for an invalid source_id.
  B5. detach_builtin removes the row and returns True; returns False for unknown step_id.
  B6. patch_builtin updates builtin_config and position; returns False for unknown step_id.
  B7. get_unified_pipeline returns function steps and built-in steps interleaved by
      position with the correct step_type discriminator.
  B8. execute_builtin_step (join) produces a DataFrame with the correct merged shape.
  B9. execute_builtin_step (pivot) output columns match the aggregation specification.
  B10. get_unified_pipeline returns None for an unknown source_id.
  B11. builtin_registry is seeded with exactly 3 rows (join, pivot, filter) on a fresh DB.
  B12. Re-running create_schema on an existing DB does not duplicate builtin rows.
  B13. GET /builtins returns all 3 rows with required fields.
  B14. source_builtin_map accepts builtin_type = "filter" without error.
"""
from __future__ import annotations

import datetime
import uuid

import pandas as pd
import pytest

from pipeui.backend.data.base.db import create_schema, get_connection
from pipeui.backend.data.base.ids import content_hash_id, new_id
from pipeui.backend.data.base.tables import instance_table_name
from pipeui.workflow.attach import attach_function
from pipeui.workflow.builtins import (
    attach_builtin,
    detach_builtin,
    execute_builtin_step,
    get_unified_pipeline,
    patch_builtin,
)
from pipeui.workflow.run import run_pipeline
from pipeui.backend.data.runner.steps import StepContext
from tests.conftest import make_registered_source


def _builtin_step(builtin_type: str, builtin_config: dict, *, step_id="s", position=0):
    """Build the typed BuiltinStepContext carrier execute_builtin_step now consumes.

    execute_builtin_step's input boundary is the BuiltinStepContext carrier (the
    loader/executor producer); these tests construct it via the factory exactly as
    the loader does."""
    return StepContext.from_builtin({
        "step_id": step_id,
        "step_type": "builtin",
        "builtin_type": builtin_type,
        "builtin_config": builtin_config,
        "position": position,
    })


# ---------------------------------------------------------------------------
# Local helper — unique column names to avoid content_hash_id collisions
# ---------------------------------------------------------------------------

def _make_source(conn, prefix: str = "x") -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Create a source with two columns whose names are unique via prefix."""
    source_id = uuid.uuid4()
    ch = content_hash_id("source_registry", f"test_{source_id}", "id", "upsert")
    conn.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_id, ch, f"test_{source_id}", datetime.date.today(), "upsert", "id"],
    )
    col_ids = []
    for i in range(2):
        col_id = uuid.uuid4()
        col_name = f"{prefix}_{source_id.hex[:6]}_col_{i}"
        col_ch = content_hash_id("column_registry", col_name, "INTEGER", str(source_id))
        conn.execute("INSERT INTO column_registry VALUES (?, ?, ?, ?)", [col_id, col_ch, col_name, "INTEGER"])
        map_id = content_hash_id("source_column_map", str(source_id), str(col_id))
        conn.execute("INSERT INTO source_column_map VALUES (?, ?, ?)", [map_id, col_id, source_id])
        col_ids.append(col_id)
    return source_id, col_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_function(conn, fn_name: str) -> uuid.UUID:
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    conn.execute(
        """
        INSERT INTO function_registry
          (function_id, content_hash_id, function_class, function_name, function_doc,
           function_return_type, function_signature, function_type, module_path, is_active)
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, TRUE)
        """,
        [fn_id, fn_ch, "pd.dataframe", fn_name, "pd.DataFrame", "(df: pd.DataFrame)", "transform", "/tmp/fn.py"],
    )
    # No params needed for these tests
    return fn_id


def _attach_fn_to_source(conn, source_id: uuid.UUID, fn_id: uuid.UUID, position: int = 0) -> uuid.UUID:
    """Attach a function to a source and override position."""
    result = attach_function(conn, source_id, [], function_id=fn_id)
    assert result["ok"], result
    sfm_id = result["source_function_map_id"]
    conn.execute(
        "UPDATE source_function_map SET position = ? WHERE source_function_map_id = ?",
        [position, sfm_id],
    )
    return uuid.UUID(sfm_id)


def _make_instance_table(conn, source_id: uuid.UUID, df: pd.DataFrame) -> None:
    tname = instance_table_name(source_id)
    conn.execute(f'CREATE TABLE "{tname}" AS SELECT * FROM df')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    conn = get_connection(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def source(db):
    source_id, col_ids = _make_source(db, "src")
    return db, source_id, col_ids


# ---------------------------------------------------------------------------
# B1 — attach_builtin creates row for valid join config
# ---------------------------------------------------------------------------

def test_attach_builtin_join_creates_row(source):
    conn, source_id, _ = source
    right_source_id, _ = _make_source(conn, "right")
    cfg = {
        "right_source_id": str(right_source_id),
        "join_type": "inner",
        "on": [{"left_col": "col_0", "right_col": "col_0"}],
        "keep_columns": "all",
    }
    result = attach_builtin(conn, source_id, "join", cfg)
    assert result["ok"] is True
    step_id = result["step_id"]

    row = conn.execute(
        "SELECT step_id, builtin_type FROM source_builtin_map WHERE step_id = ?",
        [step_id],
    ).fetchone()
    assert row is not None
    assert row[1] == "join"


# ---------------------------------------------------------------------------
# B2 — attach_builtin creates row for valid pivot config
# ---------------------------------------------------------------------------

def test_attach_builtin_pivot_creates_row(source):
    conn, source_id, _ = source
    cfg = {
        "index_columns": ["col_0"],
        "pivot_column": "col_1",
        "value_columns": [{"col_name": "col_0", "aggregations": ["sum"]}],
    }
    result = attach_builtin(conn, source_id, "pivot", cfg)
    assert result["ok"] is True
    row = conn.execute(
        "SELECT builtin_type FROM source_builtin_map WHERE step_id = ?",
        [result["step_id"]],
    ).fetchone()
    assert row is not None
    assert row[0] == "pivot"


# ---------------------------------------------------------------------------
# B3 — unknown builtin_type returns ok=False
# ---------------------------------------------------------------------------

def test_attach_builtin_unknown_type_fails(source):
    conn, source_id, _ = source
    result = attach_builtin(conn, source_id, "unknown_type", {})
    assert result["ok"] is False
    assert "builtin_type" in result["detail"]


# ---------------------------------------------------------------------------
# B4 — attach_builtin with invalid source_id returns ok=False
# ---------------------------------------------------------------------------

def test_attach_builtin_bad_source_id_fails(db):
    cfg = {
        "right_source_id": str(uuid.uuid4()),
        "join_type": "left",
        "on": [{"left_col": "a", "right_col": "b"}],
        "keep_columns": "all",
    }
    result = attach_builtin(db, uuid.uuid4(), "join", cfg)
    assert result["ok"] is False
    assert "not found" in result["detail"]


# ---------------------------------------------------------------------------
# B5 — detach_builtin removes row; returns False for unknown id
# ---------------------------------------------------------------------------

def test_detach_builtin(source):
    conn, source_id, _ = source
    right_id, _ = _make_source(conn, "right")
    cfg = {
        "right_source_id": str(right_id),
        "join_type": "inner",
        "on": [{"left_col": "col_0", "right_col": "col_0"}],
        "keep_columns": "all",
    }
    result = attach_builtin(conn, source_id, "join", cfg)
    step_id = uuid.UUID(result["step_id"])

    # Successful detach
    assert detach_builtin(conn, source_id, step_id) is True
    assert conn.execute(
        "SELECT 1 FROM source_builtin_map WHERE step_id = ?", [step_id]
    ).fetchone() is None

    # Already removed — returns False
    assert detach_builtin(conn, source_id, step_id) is False


# ---------------------------------------------------------------------------
# B6 — patch_builtin updates config / position
# ---------------------------------------------------------------------------

def test_patch_builtin(source):
    conn, source_id, _ = source
    right_id, _ = _make_source(conn, "right")
    cfg = {
        "right_source_id": str(right_id),
        "join_type": "inner",
        "on": [{"left_col": "col_0", "right_col": "col_0"}],
        "keep_columns": "all",
    }
    result = attach_builtin(conn, source_id, "join", cfg)
    step_id = uuid.UUID(result["step_id"])

    new_cfg = dict(cfg, join_type="left")
    assert patch_builtin(conn, source_id, step_id, builtin_config=new_cfg, position=5) is True

    row = conn.execute(
        "SELECT builtin_config, position FROM source_builtin_map WHERE step_id = ?",
        [step_id],
    ).fetchone()
    import json
    stored_cfg = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    assert stored_cfg["join_type"] == "left"
    assert row[1] == 5

    # Non-existent step_id
    assert patch_builtin(conn, source_id, uuid.uuid4(), position=99) is False


# ---------------------------------------------------------------------------
# B7 — get_unified_pipeline returns mixed steps with correct step_type
# ---------------------------------------------------------------------------

def test_unified_pipeline_interleaves_by_position(db):
    source_id, col_ids = _make_source(db, "main")
    fn_id = _make_function(db, "my_fn")
    # Attach function at position 0
    _attach_fn_to_source(db, source_id, fn_id, position=0)

    # Attach builtin at position 1
    right_id, _ = _make_source(db, "right")
    cfg = {
        "right_source_id": str(right_id),
        "join_type": "inner",
        "on": [{"left_col": "col_0", "right_col": "col_0"}],
        "keep_columns": "all",
    }
    result = attach_builtin(db, source_id, "join", cfg)
    assert result["ok"]
    db.execute(
        "UPDATE source_builtin_map SET position = 1 WHERE step_id = ?",
        [result["step_id"]],
    )

    pipeline = get_unified_pipeline(db, source_id)
    assert pipeline is not None
    steps = pipeline["steps"]
    assert len(steps) == 2

    types = [s["step_type"] for s in sorted(steps, key=lambda s: s["position"])]
    assert types == ["function", "builtin"]


# ---------------------------------------------------------------------------
# B8 — execute_builtin_step (join) produces correct shape
# ---------------------------------------------------------------------------

def test_execute_builtin_join(db):
    left_id, _ = _make_source(db, "left")
    right_id, _ = _make_source(db, "right")

    left_df = pd.DataFrame({"id": [1, 2, 3], "value": [10, 20, 30]})
    right_df = pd.DataFrame({"id": [1, 2, 4], "extra": ["a", "b", "c"]})

    _make_instance_table(db, left_id, left_df)
    _make_instance_table(db, right_id, right_df)

    cfg = {
        "right_source_id": str(right_id),
        "join_type": "inner",
        "on": [{"left_col": "id", "right_col": "id"}],
        "keep_columns": "all",
    }
    step = _builtin_step("join", cfg)
    result, _ = execute_builtin_step(db, left_df, step)

    assert isinstance(result, pd.DataFrame)
    # Inner join on id: rows 1 and 2 match
    assert len(result) == 2
    assert "extra" in result.columns


# ---------------------------------------------------------------------------
# B9 — execute_builtin_step (pivot) output columns match aggregation spec
# ---------------------------------------------------------------------------

def test_execute_builtin_pivot(db):
    df = pd.DataFrame({
        "region": ["A", "A", "B", "B"],
        "category": ["X", "Y", "X", "Y"],
        "sales": [100, 200, 150, 250],
    })

    cfg = {
        "index_columns": ["region"],
        "pivot_column": "category",
        "value_columns": [{"col_name": "sales", "aggregations": ["sum"]}],
    }
    step = _builtin_step("pivot", cfg)
    result, _ = execute_builtin_step(db, df, step)

    assert isinstance(result, pd.DataFrame)
    cols = list(result.columns)
    # Should have region + aggregated columns for X and Y
    assert "region" in cols
    # DuckDB PIVOT creates columns named after pivot values
    assert any("X" in c for c in cols)
    assert any("Y" in c for c in cols)


# ---------------------------------------------------------------------------
# B10 — get_unified_pipeline returns None for unknown source_id
# ---------------------------------------------------------------------------

def test_unified_pipeline_unknown_source(db):
    assert get_unified_pipeline(db, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# B11 — builtin_registry seeded with exactly 3 rows on fresh DB
# ---------------------------------------------------------------------------

def test_builtin_registry_seeded_with_three_rows(db):
    rows = db.execute("SELECT builtin_type FROM builtin_registry ORDER BY builtin_type").fetchall()
    types = [r[0] for r in rows]
    assert len(types) == 3
    assert "filter" in types
    assert "join" in types
    assert "pivot" in types


# ---------------------------------------------------------------------------
# B12 — re-running create_schema does not duplicate builtin rows
# ---------------------------------------------------------------------------

def test_create_schema_idempotent_no_duplicates(db):
    from pipeui.backend.data.base.db import create_schema
    create_schema(db)
    create_schema(db)
    count = db.execute("SELECT COUNT(*) FROM builtin_registry").fetchone()[0]
    assert count == 3


# ---------------------------------------------------------------------------
# B13 — GET /builtins returns all 3 rows with required fields
# ---------------------------------------------------------------------------

def test_get_builtins_endpoint(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pipeui.api.builtins import catalog_router, get_conn
    app = FastAPI()
    app.include_router(catalog_router)
    app.dependency_overrides[get_conn] = lambda: db
    client = TestClient(app)
    resp = client.get("/builtins")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    types = {row["builtin_type"] for row in data}
    assert types == {"join", "pivot", "filter"}
    for row in data:
        assert "builtin_id" in row
        assert "display_name" in row
        assert "description" in row
        assert "config_schema" in row


# ---------------------------------------------------------------------------
# B14 — source_builtin_map accepts builtin_type = "filter"
# ---------------------------------------------------------------------------

def test_attach_builtin_filter_accepted(source):
    conn, source_id, _ = source
    cfg = {"column": "col_0", "operator": "eq", "value": 1}
    result = attach_builtin(conn, source_id, "filter", cfg)
    assert result["ok"] is True
    row = conn.execute(
        "SELECT builtin_type FROM source_builtin_map WHERE step_id = ?",
        [result["step_id"]],
    ).fetchone()
    assert row is not None
    assert row[0] == "filter"


# ---------------------------------------------------------------------------
# Filter built-in: config validation + execution
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_attach_builtin_filter_validates_config(source):
    conn, source_id, _ = source
    ok = attach_builtin(conn, source_id, "filter", {"column": "amount", "operator": "gt", "value": "5"})
    assert ok["ok"], ok
    assert not attach_builtin(conn, source_id, "filter", {"column": "amount", "operator": "between", "value": "5"})["ok"]
    assert not attach_builtin(conn, source_id, "filter", {"column": "amount", "operator": "gt"})["ok"]
    assert not attach_builtin(conn, source_id, "filter", {"operator": "gt", "value": "5"})["ok"]
    # nullary operator needs no value
    assert attach_builtin(conn, source_id, "filter", {"column": "amount", "operator": "is_null"})["ok"]


@pytest.mark.integration
def test_execute_builtin_filter_operators(db):
    df = pd.DataFrame({"k": ["a", "b", "c", None], "n": [1, 5, 10, 7]})

    def run(cfg):
        return execute_builtin_step(db, df, _builtin_step("filter", cfg))[0]

    assert list(run({"column": "n", "operator": "gte", "value": "5"})["n"]) == [5, 10, 7]
    assert list(run({"column": "n", "operator": "eq", "value": "10"})["n"]) == [10]
    assert list(run({"column": "k", "operator": "contains", "value": "b"})["k"]) == ["b"]
    assert list(run({"column": "k", "operator": "is_null", "value": None})["n"]) == [7]
    assert list(run({"column": "k", "operator": "is_not_null", "value": None})["n"]) == [1, 5, 10]


# ---------------------------------------------------------------------------
# run_pipeline now iterates source_builtin_map (built-ins were previously skipped)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_pipeline_executes_builtin_filter_step(db):
    """A built-in step attached to a source is executed by run_pipeline and reshapes
    the working table (regression: source_builtin_map used to be skipped entirely)."""
    source_id, _ = _make_source(db, "flt")
    _make_instance_table(db, source_id, pd.DataFrame({"id": [1, 2, 3, 4], "amount": [10, 200, 30, 400]}))

    attached = attach_builtin(db, source_id, "filter", {"column": "amount", "operator": "gt", "value": "100"})
    assert attached["ok"], attached

    out = run_pipeline(db, source_id, "all")
    assert out is not None
    builtin_results = [s for s in out["steps"] if s.get("step_type") == "builtin"]
    assert len(builtin_results) == 1
    b = builtin_results[0]
    assert b["status"] == "ok"
    assert b["builtin_type"] == "filter"
    assert b["rows_affected"] == 2  # amount > 100 → [200, 400]


@pytest.mark.integration
def test_run_pipeline_validations_only_skips_builtins(db):
    """A validations-only run does not execute built-ins (they reshape the working
    table, which validations don't read)."""
    source_id, _ = _make_source(db, "flt2")
    _make_instance_table(db, source_id, pd.DataFrame({"id": [1, 2, 3], "amount": [10, 20, 30]}))
    assert attach_builtin(db, source_id, "filter", {"column": "amount", "operator": "gt", "value": "100"})["ok"]

    out = run_pipeline(db, source_id, "validations")
    assert out is not None
    assert [s for s in out["steps"] if s.get("step_type") == "builtin"] == []


# ---------------------------------------------------------------------------
# Slice 2 (#16) — _execute_join honors use_transformed via resolve_frame
#
#   J0 (AC0). use_transformed=false joins the right source's RAW instance table
#             (behavior unchanged).
#   J1 (AC1). use_transformed=true joins the right source's TRANSFORMED frame
#             resolved via resolve_frame (its latest staging table).
#   J2 (AC2). A transformed join against a never-run right source materializes it
#             on demand; the join output reflects the right source's transforms.
#   J5 (AC5). A join over null-containing / type-messy keys behaves correctly.
# ---------------------------------------------------------------------------

def _join_cfg(right_id, *, use_transformed, join_type="inner", on=None):
    return {
        "right_source_id": str(right_id),
        "use_transformed": use_transformed,
        "join_type": join_type,
        "on": on or [{"left_col": "id", "right_col": "id"}],
        "keep_columns": "all",
    }


def test_execute_join_raw_unchanged_when_use_transformed_false(db):
    """J0 (AC0): use_transformed=false joins the RAW instance table — even when a
    transformed staging table also exists, raw is what's joined (unchanged behavior)."""
    left_id, _ = _make_source(db, "jl0")
    right_id, _ = _make_source(db, "jr0")

    left_df = pd.DataFrame({"id": [1, 2, 3], "lval": [10, 20, 30]})
    raw_right = pd.DataFrame({"id": [1, 2, 4], "rtag": ["raw1", "raw2", "raw4"]})
    _make_instance_table(db, left_id, left_df)
    _make_instance_table(db, right_id, raw_right)

    # A *different* transformed staging table exists for the right source — must be ignored.
    db.execute(
        f'CREATE TABLE "staging_{right_id.hex[:8]}_999" AS '
        "SELECT * FROM (VALUES (1, 'XFORM')) AS t(id, rtag)"
    )

    step = _builtin_step("join", _join_cfg(right_id, use_transformed=False))
    result, _ = execute_builtin_step(db, left_df, step)

    # Inner join on id against RAW right: ids 1,2 match; tags are the raw ones.
    assert len(result) == 2
    assert set(result["rtag"]) == {"raw1", "raw2"}


def test_execute_join_transformed_uses_resolved_frame(db):
    """J1 (AC1): use_transformed=true joins the right source's latest staging frame
    (its transformed output), not the raw instance table."""
    left_id, _ = _make_source(db, "jl1")
    right_id, _ = _make_source(db, "jr1")

    left_df = pd.DataFrame({"id": [1, 2, 3], "lval": [10, 20, 30]})
    raw_right = pd.DataFrame({"id": [1, 2, 4], "rtag": ["raw1", "raw2", "raw4"]})
    _make_instance_table(db, left_id, left_df)
    _make_instance_table(db, right_id, raw_right)

    # The right source's transformed output (latest staging): same keys, different values.
    db.execute(
        f'CREATE TABLE "staging_{right_id.hex[:8]}_1000" AS '
        "SELECT * FROM (VALUES (1, 'XF1'), (2, 'XF2'), (3, 'XF3')) AS t(id, rtag)"
    )

    step = _builtin_step("join", _join_cfg(right_id, use_transformed=True))
    result, _ = execute_builtin_step(
        db, left_df, step,
        run_transforms=lambda c, sid: run_pipeline(c, sid, "transforms"),
    )

    # Inner join on id against the TRANSFORMED frame: ids 1,2,3 match; tags are transformed.
    assert len(result) == 3
    assert set(result["rtag"]) == {"XF1", "XF2", "XF3"}


@pytest.mark.integration
def test_execute_join_transformed_materializes_never_run_right_source(db):
    """J2 (AC2): a transformed join against a right source that has never run
    materializes it on demand; the join output reflects the right source's transforms."""
    left_id, _ = _make_source(db, "jl2")
    right_id, _ = _make_source(db, "jr2")

    left_df = pd.DataFrame({"id": [1, 2, 3, 4], "lval": [10, 20, 30, 40]})
    # Right raw has 4 rows; a filter transform on the right keeps amount > 100.
    raw_right = pd.DataFrame({"id": [1, 2, 3, 4], "amount": [50, 200, 75, 400]})
    _make_instance_table(db, left_id, left_df)
    _make_instance_table(db, right_id, raw_right)

    # Right source has a transform but has NEVER been run (no staging table yet).
    assert attach_builtin(
        db, right_id, "filter", {"column": "amount", "operator": "gt", "value": "100"}
    )["ok"]

    step = _builtin_step("join", _join_cfg(right_id, use_transformed=True))
    result, _ = execute_builtin_step(
        db, left_df, step,
        run_transforms=lambda c, sid: run_pipeline(c, sid, "transforms"),
    )

    # resolve_frame ran the right source's pipeline: only ids 2 and 4 survive the filter,
    # so the inner join keeps exactly those two rows.
    assert set(result["id"]) == {2, 4}
    assert set(result["amount"]) == {200, 400}


def test_execute_join_messy_null_keys(db):
    """J5 (AC5): a join over null-containing / type-messy keys behaves correctly —
    NULL keys do not match (SQL semantics) and no rows are dropped or corrupted."""
    left_id, _ = _make_source(db, "jl5")
    right_id, _ = _make_source(db, "jr5")

    # Mixed-content / NULL-bearing keys on both sides (VARCHAR keys).
    left_df = pd.DataFrame({"key": ["a", None, "b", "c"], "lval": [1, 2, 3, 4]})
    raw_right = pd.DataFrame({"key": ["a", "b", None, "z"], "rval": [100, 200, 300, 400]})
    _make_instance_table(db, left_id, left_df)
    _make_instance_table(db, right_id, raw_right)

    step = _builtin_step("join", _join_cfg(
        right_id, use_transformed=False, on=[{"left_col": "key", "right_col": "key"}]
    ))
    result, _ = execute_builtin_step(db, left_df, step)

    # Only non-null matching keys join: 'a' and 'b'. NULL=NULL never matches.
    matched = result[["key", "lval", "rval"]].dropna(subset=["key"])
    assert set(matched["key"]) == {"a", "b"}
    assert len(result) == 2
    # Values are paired correctly, not corrupted.
    by_key = {r["key"]: (r["lval"], r["rval"]) for _, r in result.iterrows()}
    assert by_key["a"] == (1, 100)
    assert by_key["b"] == (3, 200)


# ---------------------------------------------------------------------------
# Consumed transformed-output lineage (PRD User Story 7)
#
#   A join that consumes a TRANSFORMED right source records that result's
#   result_id on its step-result entry; a RAW join records None. The id equals
#   the one resolve_frame returns for the same (source, transformed) reference.
# ---------------------------------------------------------------------------

def _attach_join_to_source(conn, left_id, right_id, *, use_transformed, on=None):
    """Attach a join built-in on left_id against right_id and return its step_id."""
    cfg = {
        "right_source_id": str(right_id),
        "use_transformed": use_transformed,
        "join_type": "inner",
        "on": on or [{"left_col": "id", "right_col": "id"}],
        "keep_columns": "all",
    }
    res = attach_builtin(conn, left_id, "join", cfg)
    assert res["ok"], res
    return res["step_id"]


@pytest.mark.integration
def test_transformed_join_step_result_carries_consumed_result_id(db):
    """A TRANSFORMED join's run_pipeline step entry carries consumed_result_id equal
    to the result_id resolve_frame returns for the same transformed reference."""
    from pipeui.workflow.resolve import TRANSFORMED, resolve_frame

    left_id, _ = _make_source(db, "cjl")
    right_id, _ = _make_source(db, "cjr")

    _make_instance_table(db, left_id, pd.DataFrame({"id": [1, 2, 3], "lval": [10, 20, 30]}))
    _make_instance_table(db, right_id, pd.DataFrame({"id": [1, 2, 4], "rtag": ["raw1", "raw2", "raw4"]}))
    # The right source's transformed output (its latest staging table).
    db.execute(
        f'CREATE TABLE "staging_{right_id.hex[:8]}_2000" AS '
        "SELECT * FROM (VALUES (1, 'XF1'), (2, 'XF2'), (3, 'XF3')) AS t(id, rtag)"
    )

    _attach_join_to_source(db, left_id, right_id, use_transformed=True)

    out = run_pipeline(db, left_id, "all")
    builtin_results = [s for s in out["steps"] if s.get("step_type") == "builtin"]
    assert len(builtin_results) == 1
    entry = builtin_results[0]
    assert entry["status"] == "ok", entry.get("error")

    # The id the join consumed equals resolve_frame's transformed result_id for the right source.
    _frame, ref = resolve_frame(db, right_id, TRANSFORMED)
    assert entry["consumed_result_id"] == ref.result_id
    assert entry["consumed_result_id"] is not None


@pytest.mark.integration
def test_raw_join_step_result_has_no_consumed_result_id(db):
    """A RAW join consumes the source's own data, not a produced result — its step
    entry's consumed_result_id is None."""
    left_id, _ = _make_source(db, "rjl")
    right_id, _ = _make_source(db, "rjr")

    _make_instance_table(db, left_id, pd.DataFrame({"id": [1, 2, 3], "lval": [10, 20, 30]}))
    _make_instance_table(db, right_id, pd.DataFrame({"id": [1, 2, 4], "rtag": ["raw1", "raw2", "raw4"]}))

    _attach_join_to_source(db, left_id, right_id, use_transformed=False)

    out = run_pipeline(db, left_id, "all")
    builtin_results = [s for s in out["steps"] if s.get("step_type") == "builtin"]
    assert len(builtin_results) == 1
    entry = builtin_results[0]
    assert entry["status"] == "ok", entry.get("error")
    assert entry["consumed_result_id"] is None
