"""Behavioral guarantees for built-in pipeline steps (join and pivot).

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
"""
from __future__ import annotations

import datetime
import uuid

import pandas as pd
import pytest

from pipeui.db import create_schema, get_connection
from pipeui.ids import content_hash_id, new_id
from pipeui.sql_user_table import instance_table_name
from pipeui.workflow.attach import attach_function
from pipeui.workflow.builtins import (
    attach_builtin,
    detach_builtin,
    execute_builtin_step,
    get_unified_pipeline,
    patch_builtin,
)
from tests.conftest import make_registered_source


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
    step = {"builtin_type": "join", "builtin_config": cfg}
    result = execute_builtin_step(db, left_df, step)

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
    step = {"builtin_type": "pivot", "builtin_config": cfg}
    result = execute_builtin_step(db, df, step)

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
