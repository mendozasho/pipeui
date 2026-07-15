"""Behavioral guarantees for built-in pipeline steps (join, pivot, filter, rename, date_range).

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
  B11. builtin_registry is seeded with the built-in rows (join, pivot, filter, rename) on a fresh DB.
  B12. Re-running create_schema on an existing DB does not duplicate builtin rows.
  B13. GET /builtins returns all rows with required fields.
  B14. source_builtin_map accepts builtin_type = "filter" without error.
  B15. rename built-in: validate/execute, singleton-per-source, pinned last (#40).
  B16. Pinned-tail ordering is spec-metadata-driven (#83/#116): defined once as
       BuiltinSpec.pinned_tail, consumed by all three ordering sites (pipeline read,
       unified pipeline, run execution order); a second pinned type is a registration.
  B17. date_range built-in (#117): registered spec + catalog row on fresh AND
       pre-feature DBs (idempotent seed migration); pure validator over grouped
       range conditions; DuckDB executor — inclusive bounds, one-sided ranges,
       TIMESTAMP/TIMESTAMPTZ at DATE granularity, NULL fails its condition,
       AND within group / OR across groups; pinned tail rank 1 (before rename).
"""
from __future__ import annotations

import datetime
import uuid

import pandas as pd
import pytest

from pipeui.backend.data.base.db import create_schema, get_connection
from pipeui.backend.data.base.ids import content_hash_id
from pipeui.backend.data.base.tables import instance_table_name
from pipeui.backend.domain.functions.attach import attach_function
from pipeui.backend.domain.functions.builtins import (
    attach_builtin,
    detach_builtin,
    execute_builtin_step,
    get_unified_pipeline,
    patch_builtin,
    _validate_rename_config,
    _execute_rename,
    _validate_date_range_config,
    _execute_date_range,
)
from pipeui.backend.domain.functions.pipeline_read import get_pipeline
from pipeui.backend.domain.runner.run import run_pipeline
from pipeui.backend.data.runner.steps import StepContext


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

def test_builtin_registry_seeded_with_builtin_rows(db):
    rows = db.execute("SELECT builtin_type FROM builtin_registry ORDER BY builtin_type").fetchall()
    types = [r[0] for r in rows]
    assert set(types) == {"join", "pivot", "filter", "rename", "date_range"}


# ---------------------------------------------------------------------------
# B12 — re-running create_schema does not duplicate builtin rows
# ---------------------------------------------------------------------------

def test_create_schema_idempotent_no_duplicates(db):
    from pipeui.backend.data.base.db import create_schema
    create_schema(db)
    create_schema(db)
    count = db.execute("SELECT COUNT(*) FROM builtin_registry").fetchone()[0]
    assert count == 5  # join, pivot, filter, rename, date_range


# ---------------------------------------------------------------------------
# B17 (#120) — date_range catalog row migrates onto a pre-feature DB idempotently
# ---------------------------------------------------------------------------

def test_date_range_seed_migrates_pre_feature_db_idempotently(db):
    """#117 criterion 1: a DB created before the date_range feature (builtin_registry
    holds only the original 4 rows) gains the date_range catalog row when
    create_schema runs again (app startup path — the same INSERT OR IGNORE seed
    mechanism the rename builtin used), and re-running the migration does not
    duplicate it (count grows by exactly one)."""
    # Simulate a pre-feature DB: full schema, but no date_range catalog row.
    db.execute("DELETE FROM builtin_registry WHERE builtin_type = 'date_range'")
    pre_count = db.execute("SELECT COUNT(*) FROM builtin_registry").fetchone()[0]
    assert pre_count == 4  # the pre-feature seed set

    create_schema(db)  # the migration: startup re-runs the idempotent seed
    rows = db.execute("SELECT builtin_type FROM builtin_registry").fetchall()
    assert ("date_range",) in rows
    assert len(rows) == pre_count + 1  # grew by exactly one

    create_schema(db)  # idempotent: a second migration pass adds nothing
    count = db.execute("SELECT COUNT(*) FROM builtin_registry").fetchone()[0]
    assert count == pre_count + 1


# ---------------------------------------------------------------------------
# B13 — GET /builtins returns all 3 rows with required fields
# ---------------------------------------------------------------------------

def test_get_builtins_endpoint(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pipeui.middleware.builtins import catalog_router, get_conn
    app = FastAPI()
    app.include_router(catalog_router)
    app.dependency_overrides[get_conn] = lambda: db
    client = TestClient(app)
    resp = client.get("/builtins")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 5
    types = {row["builtin_type"] for row in data}
    assert types == {"join", "pivot", "filter", "rename", "date_range"}
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
    from pipeui.backend.domain.runner.resolve import TRANSFORMED, resolve_frame

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


# ---------------------------------------------------------------------------
# OCP — builtin dispatch is registry-driven (#50)
# ---------------------------------------------------------------------------

def test_builtin_dispatch_is_registry_driven(db):
    """#50: a built-in type is reachable purely by REGISTERING a BuiltinSpec in
    BUILTIN_EXECUTORS — no edit to any attach-time or run-time if/elif chain. Registering
    a throwaway type routes BOTH attach_builtin (validation) and execute_builtin_step
    (execution) to it; unregistering removes it. This is the OCP the registry buys."""
    import pipeui.backend.domain.functions.builtins as b

    # The shipped registry holds exactly the built-in types, each a BuiltinSpec.
    assert set(b.BUILTIN_EXECUTORS) == {"join", "pivot", "filter", "rename", "date_range"}
    assert all(isinstance(s, b.BuiltinSpec) for s in b.BUILTIN_EXECUTORS.values())

    source_id, _ = _make_source(db, "ocp")
    seen: dict = {}
    spec = b.BuiltinSpec(
        validate=lambda cfg: None if cfg.get("ok") else "bad cfg",
        execute=lambda conn, df, cfg, run_transforms: (seen.setdefault("ran", df), None),
    )
    saved = dict(b.BUILTIN_EXECUTORS)
    b.BUILTIN_EXECUTORS["_ocp_probe"] = spec
    try:
        # attach-time validation dispatches through the registered validator
        bad = b.attach_builtin(db, source_id, "_ocp_probe", {})
        assert bad["ok"] is False and "bad cfg" in bad["detail"]
        good = b.attach_builtin(db, source_id, "_ocp_probe", {"ok": True})
        assert good["ok"] is True

        # run-time execution dispatches through the registered executor
        df = pd.DataFrame({"a": [1, 2]})
        out, consumed = execute_builtin_step(db, df, _builtin_step("_ocp_probe", {"ok": True}))
        assert seen.get("ran") is df and consumed is None
    finally:
        b.BUILTIN_EXECUTORS.clear()
        b.BUILTIN_EXECUTORS.update(saved)


# ---------------------------------------------------------------------------
# Rename built-in (#40) — validate, execute, singleton, pinned-last
# ---------------------------------------------------------------------------

def test_validate_rename_config_shapes():
    assert _validate_rename_config({"renames": {"a": "b"}}) is None
    assert _validate_rename_config({}) is not None                          # missing
    assert _validate_rename_config({"renames": {}}) is not None             # empty
    assert _validate_rename_config({"renames": "x"}) is not None            # not a dict
    assert _validate_rename_config({"renames": {"": "b"}}) is not None      # empty source
    assert _validate_rename_config({"renames": {"a": ""}}) is not None      # empty target
    assert _validate_rename_config({"renames": {"a": "x", "b": "x"}}) is not None  # dup target


def test_execute_rename_renames_columns():
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = _execute_rename(None, df, {"renames": {"a": "A"}})
    assert list(out.columns) == ["A", "b"]


def test_execute_rename_missing_column_raises():
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(ValueError, match="not found"):
        _execute_rename(None, df, {"renames": {"zzz": "Z"}})


def test_execute_rename_target_collision_raises():
    # Renaming a -> b collides with the surviving column b.
    df = pd.DataFrame({"a": [1], "b": [2]})
    with pytest.raises(ValueError, match="already exist"):
        _execute_rename(None, df, {"renames": {"a": "b"}})


def test_attach_rename_is_singleton(source):
    conn, source_id, _ = source
    first = attach_builtin(conn, source_id, "rename", {"renames": {"x": "y"}})
    assert first["ok"] is True
    second = attach_builtin(conn, source_id, "rename", {"renames": {"p": "q"}})
    assert second["ok"] is False
    assert "rename" in second["detail"]


def test_rename_pinned_last_in_pipeline_display(source):
    conn, source_id, col_ids = source
    col_name = conn.execute(
        "SELECT column_name FROM column_registry WHERE column_id = ?", [col_ids[0]]
    ).fetchone()[0]
    # Attach rename FIRST (lower position) then a filter — rename must still sort last.
    assert attach_builtin(conn, source_id, "rename", {"renames": {col_name: "renamed"}})["ok"] is True
    assert attach_builtin(conn, source_id, "filter", {"column": col_name, "operator": "is_null"})["ok"] is True
    pipe = get_pipeline(conn, source_id)
    builtin_types = [s["builtin_type"] for s in pipe["steps"] if s.get("step_type") == "builtin"]
    assert builtin_types[-1] == "rename", builtin_types  # pinned last despite lower position


def test_execute_rename_swap_is_allowed():
    # a->b and b->a swap: neither target collides with a SURVIVING column (both are
    # renamed away), so it must NOT raise — guards the `surviving = cols - keys`
    # nuance against a naive `new in columns` check.
    df = pd.DataFrame({"a": [1], "b": [2]})
    out = _execute_rename(None, df, {"renames": {"a": "b", "b": "a"}})
    assert out["a"].iloc[0] == 2 and out["b"].iloc[0] == 1  # labels swapped


def test_rename_pinned_last_in_execution(db):
    """#40: a rename built-in EXECUTES last (run.py), not just displays last — even when
    attached at a lower position than another step."""
    source_id, _ = _make_source(db, "rexec")
    _make_instance_table(db, source_id, pd.DataFrame({"a": [1, 2], "b": [3, 4]}))
    # rename attached FIRST (position 0), filter SECOND (position 1).
    assert attach_builtin(db, source_id, "rename", {"renames": {"a": "A"}})["ok"]
    assert attach_builtin(db, source_id, "filter", {"column": "b", "operator": "is_not_null"})["ok"]
    out = run_pipeline(db, source_id, "all")
    order = [s["builtin_type"] for s in out["steps"] if s.get("step_type") == "builtin"]
    assert order == ["filter", "rename"], order  # rename runs last despite lower position


# ---------------------------------------------------------------------------
# date_range built-in (#117) — pure config validator (#121)
# ---------------------------------------------------------------------------

def _dr_cfg(*groups):
    """Build a date_range config from condition lists: _dr_cfg([c1, c2], [c3])."""
    return {"groups": [{"conditions": list(conds)} for conds in groups]}


def test_validate_date_range_config_rejects_invalid_shapes():
    """#117 criterion 2 (#121) — the pure validator rejects: zero groups, an empty
    group, a condition missing a column, a condition with both bounds empty, and
    start > end. Structural only — no DB access."""
    # zero groups
    assert _validate_date_range_config({}) is not None
    assert _validate_date_range_config({"groups": []}) is not None
    # an empty group (no conditions)
    assert _validate_date_range_config(_dr_cfg([])) is not None
    # a condition missing a column
    assert _validate_date_range_config(
        _dr_cfg([{"start": "2025-01-01", "end": "2025-03-31"}])
    ) is not None
    # both bounds empty — None and "" both count as absent
    assert _validate_date_range_config(
        _dr_cfg([{"column": "d", "start": None, "end": None}])
    ) is not None
    assert _validate_date_range_config(
        _dr_cfg([{"column": "d", "start": "", "end": ""}])
    ) is not None
    # start > end
    assert _validate_date_range_config(
        _dr_cfg([{"column": "d", "start": "2025-06-01", "end": "2025-01-01"}])
    ) is not None


def test_validate_date_range_config_accepts_one_sided_and_multi_group():
    """#117 criterion 2 (#121) — one-sided conditions (start-only / end-only) and
    multi-group configs are valid; the same column may repeat across groups."""
    # both bounds
    assert _validate_date_range_config(
        _dr_cfg([{"column": "d", "start": "2025-01-01", "end": "2025-03-31"}])
    ) is None
    # start-only (on-or-after) — open bound as None or ""
    assert _validate_date_range_config(
        _dr_cfg([{"column": "d", "start": "2025-01-01", "end": None}])
    ) is None
    # end-only (on-or-before)
    assert _validate_date_range_config(
        _dr_cfg([{"column": "d", "start": "", "end": "2025-03-31"}])
    ) is None
    # multi-group, multi-condition, same column in two groups
    assert _validate_date_range_config(_dr_cfg(
        [{"column": "eff", "start": "2025-01-01", "end": "2025-03-31"},
         {"column": "ship", "start": None, "end": "2025-06-30"}],
        [{"column": "eff", "start": "2025-01-01", "end": None}],
    )) is None


# ---------------------------------------------------------------------------
# date_range built-in (#117) — DuckDB executor (#122)
# ---------------------------------------------------------------------------

def _run_date_range(conn, df, cfg):
    result, consumed = execute_builtin_step(conn, df, _builtin_step("date_range", cfg))
    assert consumed is None  # date_range never consumes another source's result
    return result


def test_execute_date_range_inclusive_and_one_sided_bounds(db):
    """#117 criterion 3 (#122) — keeps exactly the matching rows with inclusive
    bounds on both ends; start-only = on-or-after, end-only = on-or-before.
    Realistic data: boundary-day rows on both ends and a NULL date."""
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5, 6],
        "d": [
            datetime.date(2024, 12, 31),  # day before start boundary
            datetime.date(2025, 1, 1),    # start boundary — inclusive
            datetime.date(2025, 2, 15),   # inside
            datetime.date(2025, 3, 31),   # end boundary — inclusive
            datetime.date(2025, 4, 1),    # day after end boundary
            None,                          # NULL date — never matches
        ],
    })

    # Both bounds: [2025-01-01, 2025-03-31] inclusive on both ends.
    both = _run_date_range(db, df, _dr_cfg(
        [{"column": "d", "start": "2025-01-01", "end": "2025-03-31"}]))
    assert sorted(both["id"]) == [2, 3, 4]

    # Start-only: on-or-after 2025-01-01.
    start_only = _run_date_range(db, df, _dr_cfg(
        [{"column": "d", "start": "2025-01-01", "end": None}]))
    assert sorted(start_only["id"]) == [2, 3, 4, 5]

    # End-only: on-or-before 2025-03-31.
    end_only = _run_date_range(db, df, _dr_cfg(
        [{"column": "d", "start": "", "end": "2025-03-31"}]))
    assert sorted(end_only["id"]) == [1, 2, 3, 4]


def test_execute_date_range_timestamp_compares_at_date_granularity(db):
    """#117 criterion 4 (#122) — TIMESTAMP and TIMESTAMPTZ columns compare at DATE
    granularity: a row stamped 23:59 on the range's end day is kept (the last day
    of a reporting period is never silently dropped)."""
    ts_df = pd.DataFrame({
        "id": [1, 2, 3],
        "ts": pd.to_datetime([
            "2025-03-31 23:59:00",   # 23:59 on the end day — must be kept
            "2025-01-01 00:00:00",   # midnight on the start day
            "2025-04-01 00:00:01",   # just past the end day
        ]),
    })
    assert str(db.execute("SELECT typeof(ts) FROM ts_df LIMIT 1").fetchone()[0]) == "TIMESTAMP"
    kept = _run_date_range(db, ts_df, _dr_cfg(
        [{"column": "ts", "start": "2025-01-01", "end": "2025-03-31"}]))
    assert sorted(kept["id"]) == [1, 2]

    # TIMESTAMPTZ casts use DuckDB's session-default timezone (PRD); pin it to UTC
    # so the boundary assertion is deterministic on any machine.
    db.execute("SET TimeZone = 'UTC'")
    tstz_df = pd.DataFrame({
        "id": [1, 2],
        "ts": pd.to_datetime([
            "2025-03-31 23:59:00",
            "2025-04-01 00:00:01",
        ]).tz_localize("UTC"),
    })
    assert "TIMESTAMP WITH TIME ZONE" in str(
        db.execute("SELECT typeof(ts) FROM tstz_df LIMIT 1").fetchone()[0]
    )
    kept_tz = _run_date_range(db, tstz_df, _dr_cfg(
        [{"column": "ts", "start": "2025-01-01", "end": "2025-03-31"}]))
    assert sorted(kept_tz["id"]) == [1]


def test_execute_date_range_null_fails_condition_but_row_can_pass_via_or_group(db):
    """#117 criterion 5 (#122) — a NULL date fails its condition (standard SQL),
    but the row can still pass via another OR group on a different column."""
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "eff": [None, None, datetime.date(2025, 2, 1)],           # NULL effective dates
        "renewal": [datetime.date(2025, 8, 1), datetime.date(2024, 5, 1), None],
    })
    cfg = _dr_cfg(
        [{"column": "eff", "start": "2025-01-01", "end": "2025-03-31"}],   # group 1
        [{"column": "renewal", "start": "2025-01-01", "end": "2025-12-31"}],  # OR group 2
    )
    kept = _run_date_range(db, df, cfg)
    # id=1: NULL eff fails group 1, but renewal 2025-08-01 passes group 2 -> kept.
    # id=2: NULL eff fails group 1 AND renewal 2024 fails group 2 -> dropped.
    # id=3: eff passes group 1 (NULL renewal fails group 2 but OR needs one) -> kept.
    assert sorted(kept["id"]) == [1, 3]


def test_execute_date_range_and_within_group_or_across_groups(db):
    """#117 criterion 6 (#122) — conditions within a group AND; groups OR. The
    matrix includes the same column (eff) in two different groups: the PRD's
    '(eff in Q1 AND shipped by June 30) OR eff on-or-after July 1' shape."""
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "eff": [
            datetime.date(2025, 2, 1),   # in Q1
            datetime.date(2025, 2, 1),   # in Q1
            datetime.date(2025, 9, 1),   # after July 1
            datetime.date(2024, 12, 1),  # before Q1
            None,                        # NULL — fails both groups' eff conditions
        ],
        "ship": [
            datetime.date(2025, 5, 1),   # by June 30
            datetime.date(2025, 8, 1),   # after June 30
            datetime.date(2025, 8, 1),
            datetime.date(2025, 5, 1),   # by June 30 — but eff fails its AND
            datetime.date(2025, 5, 1),
        ],
    })
    cfg = _dr_cfg(
        # group 1: eff in Q1 AND shipped by June 30
        [{"column": "eff", "start": "2025-01-01", "end": "2025-03-31"},
         {"column": "ship", "start": None, "end": "2025-06-30"}],
        # group 2 (OR): eff on-or-after July 1 — same column as group 1
        [{"column": "eff", "start": "2025-07-01", "end": None}],
    )
    kept = _run_date_range(db, df, cfg)
    # id=1: group 1 fully holds -> kept.
    # id=2: ship fails group 1's AND; eff in Q1 fails group 2 -> dropped
    #       (an OR-within-group bug would keep it).
    # id=3: group 2 holds -> kept.
    # id=4: eff fails group 1's AND despite ship passing; fails group 2 -> dropped.
    # id=5: NULL eff fails both groups -> dropped.
    assert sorted(kept["id"]) == [1, 3]


# ---------------------------------------------------------------------------
# B16 — pinned-tail ordering is spec-metadata-driven (#83 / #116)
# ---------------------------------------------------------------------------

def _builtin_order_at_all_three_consumers(conn, source_id):
    """Return the builtin_type order seen by each ordering consumer:
    (pipeline read payload, unified pipeline payload, run execution order)."""
    read_order = [
        s["builtin_type"]
        for s in get_pipeline(conn, source_id)["steps"]
        if s.get("step_type") == "builtin"
    ]
    unified_order = [
        s["builtin_type"]
        for s in get_unified_pipeline(conn, source_id)["steps"]
        if s.get("step_type") == "builtin"
    ]
    run_order = [
        s["builtin_type"]
        for s in run_pipeline(conn, source_id, "all")["steps"]
        if s.get("step_type") == "builtin"
    ]
    return read_order, unified_order, run_order


def test_rename_pinned_last_in_unified_pipeline(db):
    """Regression lock (#116 criterion 1) — the third ordering consumer: a rename
    built-in sorts last in the get_unified_pipeline payload despite a lower stored
    position. Locks today's behavior through the pinned-tail rewrite (the
    get_pipeline and run_pipeline consumers are locked by the existing #40 tests)."""
    source_id, _ = _make_source(db, "runi")
    # rename attached FIRST (position 0), filter SECOND (position 1).
    assert attach_builtin(db, source_id, "rename", {"renames": {"a": "A"}})["ok"]
    assert attach_builtin(db, source_id, "filter", {"column": "b", "operator": "is_not_null"})["ok"]
    pipe = get_unified_pipeline(db, source_id)
    builtin_types = [s["builtin_type"] for s in pipe["steps"] if s.get("step_type") == "builtin"]
    assert builtin_types == ["filter", "rename"], builtin_types


def test_pinned_tail_ordering_comes_from_spec_metadata_only(db):
    """#116 criterion 2 — the pinned tail is defined in exactly one place: the
    BuiltinSpec registry metadata. Overriding rename's registration with a spec that
    carries NO pinned metadata must make rename sort purely by stored position at
    ALL THREE consumers. Fails if any ordering site keys on the literal 'rename'."""
    import pipeui.backend.domain.functions.builtins as b

    source_id, _ = _make_source(db, "unpin")
    _make_instance_table(db, source_id, pd.DataFrame({"a": [1, 2], "b": [3, 4]}))
    # rename at position 0, filter at position 1.
    assert attach_builtin(db, source_id, "rename", {"renames": {"a": "A"}})["ok"]
    assert attach_builtin(db, source_id, "filter", {"column": "b", "operator": "is_not_null"})["ok"]

    saved = dict(b.BUILTIN_EXECUTORS)
    # Same validate/execute, but WITHOUT pinned metadata — an unpinned rename.
    b.BUILTIN_EXECUTORS["rename"] = b.BuiltinSpec(
        validate=saved["rename"].validate,
        execute=saved["rename"].execute,
        singleton=True,
    )
    try:
        read_order, unified_order, run_order = _builtin_order_at_all_three_consumers(db, source_id)
        # Unpinned via the spec -> plain position order everywhere.
        assert read_order == ["rename", "filter"], read_order
        assert unified_order == ["rename", "filter"], unified_order
        assert run_order == ["rename", "filter"], run_order
    finally:
        b.BUILTIN_EXECUTORS.clear()
        b.BUILTIN_EXECUTORS.update(saved)


def test_date_range_sorts_in_pinned_tail_before_rename(db):
    """#117 criterion 7 (#120) — date_range sorts in the pinned tail: after every
    positional step and before rename, at all three ordering consumers (pipeline
    read, unified pipeline, run execution order), via slice 1's spec-metadata
    mechanism. Attach order gives rename the LOWEST position and filter the
    HIGHEST (rename 0, date_range 1, filter 2); stored positions would order them
    rename, date_range, filter — the pinned tail must reorder to
    filter, date_range, rename."""
    source_id, _ = _make_source(db, "drtail")
    _make_instance_table(db, source_id, pd.DataFrame({
        "a": [1, 2],
        "b": [3, 4],
        "d": [datetime.date(2025, 1, 15), datetime.date(2025, 6, 1)],
    }))

    assert attach_builtin(db, source_id, "rename", {"renames": {"a": "A"}})["ok"]
    assert attach_builtin(db, source_id, "date_range", {
        "groups": [{"conditions": [{"column": "d", "start": "2025-01-01", "end": None}]}]
    })["ok"]
    assert attach_builtin(db, source_id, "filter", {"column": "b", "operator": "is_not_null"})["ok"]

    read_order, unified_order, run_order = _builtin_order_at_all_three_consumers(db, source_id)
    assert read_order == ["filter", "date_range", "rename"], read_order
    assert unified_order == ["filter", "date_range", "rename"], unified_order
    assert run_order == ["filter", "date_range", "rename"], run_order


def test_second_pinned_type_sorts_between_positional_and_rename(db):
    """#116 criterion 3 — a second pinned type is a REGISTRATION, not a fourth sort
    site: a test double with pinned_tail=1 (rename carries pinned_tail=2) sorts after
    every positional step and before rename at all three consumers, with no
    site-specific changes."""
    import pipeui.backend.domain.functions.builtins as b

    source_id, _ = _make_source(db, "pin2")
    _make_instance_table(db, source_id, pd.DataFrame({"a": [1, 2], "b": [3, 4]}))

    saved = dict(b.BUILTIN_EXECUTORS)
    b.BUILTIN_EXECUTORS["_pin_probe"] = b.BuiltinSpec(
        validate=lambda cfg: None,
        execute=lambda conn, df, cfg, run_transforms: (df, None),
        singleton=True,
        pinned_tail=1,  # tail order: [positional..., _pin_probe (1), rename (2)]
    )
    try:
        # Attach order gives rename the LOWEST position and filter the HIGHEST:
        # rename (0), _pin_probe (1), filter (2). Stored positions would order
        # them rename, _pin_probe, filter — the pinned tail must reorder to
        # filter, _pin_probe, rename.
        assert attach_builtin(db, source_id, "rename", {"renames": {"a": "A"}})["ok"]
        assert attach_builtin(db, source_id, "_pin_probe", {})["ok"]
        assert attach_builtin(db, source_id, "filter", {"column": "b", "operator": "is_not_null"})["ok"]

        read_order, unified_order, run_order = _builtin_order_at_all_three_consumers(db, source_id)
        assert read_order == ["filter", "_pin_probe", "rename"], read_order
        assert unified_order == ["filter", "_pin_probe", "rename"], unified_order
        assert run_order == ["filter", "_pin_probe", "rename"], run_order
    finally:
        b.BUILTIN_EXECUTORS.clear()
        b.BUILTIN_EXECUTORS.update(saved)
