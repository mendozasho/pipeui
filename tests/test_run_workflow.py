"""Behavioral guarantees for workflow/run.py (Phase E2 / §13).

Guarantees under test:

Staging table mechanics:
  1. After a successful transform run, a staging table exists with the correct prefix.
  2. Re-running drops the prior staging table and creates a new one.
  3. Validation-only run does NOT write a staging table.

output_mode variants:
  4. output_mode=append adds a new column to the working table.
  5. output_mode=replace overwrites the bound column in the working table.
  6. pd.DataFrame return replaces the full working table regardless of output_mode.

Failure/skip behaviour:
  7. A failed transform step returns status="failed" with error populated.
  8. After a failed transform step subsequent steps run against the last good table.

Validation side-effect isolation:
  9. Validation step returns rows_passed and rows_failed counts.
  10. Validation step does NOT modify the working table (original table unchanged).

run_type filtering:
  11. run_type=transforms only executes transform steps.
  12. run_type=validations only executes validation steps.
  13. run_type=set executes only the specified set.

Source lookup:
  14. Returns None when source_id is not found.
"""
from __future__ import annotations

import csv
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeui.ids import content_hash_id
from pipeui.sql_user_table import instance_table_name
from pipeui.workflow.create import create_source
from pipeui.workflow.ingestion import ingest_source
from pipeui.workflow.run import _staging_prefix, run_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_csv(tmp_path, name, columns, rows):
    p = tmp_path / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return str(p)


def _register_source_and_ingest(db, tmp_path, name="sales"):
    """Create source with two columns (id, val) and ingest 3 rows."""
    path = make_csv(tmp_path, f"{name}.csv", ["id", "val"], [["r1", 10], ["r2", 20], ["r3", 30]])
    source_id, failed = create_source(db, path, name, "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    return source_id, path


def _write_fn_file(tmp_path, fn_name, body):
    """Write a small Python function to a temp file; return the path string."""
    p = tmp_path / f"{fn_name}.py"
    p.write_text(f"def {fn_name}(data):\n    {body}\n")
    return str(p)


def _write_df_fn_file(tmp_path, fn_name, body):
    """Write a function accepting df kwarg."""
    p = tmp_path / f"{fn_name}.py"
    p.write_text(f"def {fn_name}(df):\n    {body}\n")
    return str(p)


def _seed_transform_step(db, source_id, column_id, fn_name, module_path, output_mode="append", position=0):
    """Seed a transform function + set attached to source_id, bound to column_id."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", fn_name, None, "pd.Series",
         f"data: pd.Series", "transform", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
        [param_id, param_ch, "data", "pd.Series", fn_id],
    )

    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, set_ch, fn_name, None],
    )
    set_map_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_map_id, set_id, fn_id, 0],
    )

    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, output_mode],
    )

    # alias_map binding
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute(
        "INSERT INTO alias_map VALUES (?, ?, ?, ?)",
        [alias_id, column_id, param_id, source_id],
    )
    return sfm_id, set_id


def _seed_df_transform_step(db, source_id, fn_name, module_path, output_mode="replace", position=0):
    """Seed a pd.DataFrame transform step attached to source_id (no alias_map)."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.dataframe", fn_name, None, "pd.DataFrame",
         "df: pd.DataFrame", "transform", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
        [param_id, param_ch, "df", "pd.DataFrame", fn_id],
    )

    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, set_ch, fn_name, None],
    )
    set_map_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_map_id, set_id, fn_id, 0],
    )

    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, output_mode],
    )
    return sfm_id, set_id


def _seed_validation_step(db, source_id, column_id, fn_name, module_path, position=1):
    """Seed a validation function + set attached to source_id, bound to column_id."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", fn_name, None, "pd.Series[bool]",
         "data: pd.Series", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
        [param_id, param_ch, "data", "pd.Series", fn_id],
    )

    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, set_ch, fn_name, None],
    )
    set_map_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_map_id, set_id, fn_id, 0],
    )

    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )

    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute(
        "INSERT INTO alias_map VALUES (?, ?, ?, ?)",
        [alias_id, column_id, param_id, source_id],
    )
    return sfm_id, set_id


def _list_staging_tables(db, source_id):
    prefix = _staging_prefix(source_id)
    rows = db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchall()
    return [r[0] for r in rows if r[0].startswith(prefix)]


# ---------------------------------------------------------------------------
# Guarantee 14: unknown source returns None
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_unknown_source_returns_none(db):
    """run_pipeline returns None when source_id is not in source_registry."""
    result = run_pipeline(db, uuid.uuid4(), "transforms")
    assert result is None


# ---------------------------------------------------------------------------
# Staging table mechanics
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_staging_table_written_after_transform(db, tmp_path):
    """Guarantee 1: after a successful transform run a staging table is created."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "double_val", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "double_val", fn_path, output_mode="append")

    result = run_pipeline(db, source_id, "transforms")
    assert result is not None
    assert result["steps"][0]["status"] == "ok"

    staging = _list_staging_tables(db, source_id)
    assert len(staging) == 1


@pytest.mark.integration
def test_rerun_drops_prior_staging_table(db, tmp_path):
    """Guarantee 2: re-running drops the prior staging table and creates a new one."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "noop", "return data")
    _seed_transform_step(db, source_id, col_id, "noop", fn_path)

    run_pipeline(db, source_id, "transforms")
    first_tables = _list_staging_tables(db, source_id)
    assert len(first_tables) == 1

    # Second run with a different timestamp may collide; patch time to ensure different name
    import time
    with patch("pipeui.workflow.run.time") as mock_time:
        mock_time.time.return_value = int(time.time()) + 9999
        run_pipeline(db, source_id, "transforms")

    second_tables = _list_staging_tables(db, source_id)
    assert len(second_tables) == 1
    assert second_tables[0] != first_tables[0]


@pytest.mark.integration
def test_validation_only_run_no_staging_table(db, tmp_path):
    """Guarantee 3: validation-only run does NOT write a staging table."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "positive", "return data > 0")
    _seed_validation_step(db, source_id, col_id, "positive", fn_path, position=0)

    run_pipeline(db, source_id, "validations")
    staging = _list_staging_tables(db, source_id)
    assert staging == []


# ---------------------------------------------------------------------------
# output_mode variants
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_output_mode_append_adds_column(db, tmp_path):
    """Guarantee 4: output_mode=append adds a new column; existing columns preserved."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "doubled", fn_path, output_mode="append")

    result = run_pipeline(db, source_id, "transforms")
    assert result["steps"][0]["status"] == "ok"

    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "val" in df.columns      # original column preserved
    assert "doubled" in df.columns  # new column appended


@pytest.mark.integration
def test_output_mode_replace_overwrites_column(db, tmp_path):
    """Guarantee 5: output_mode=replace overwrites the bound column in the working table."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "triple_val", "return data * 3")
    _seed_transform_step(db, source_id, col_id, "triple_val", fn_path, output_mode="replace")

    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "val" in df.columns
    # original values were 10, 20, 30 -> tripled: 30, 60, 90
    assert sorted(df["val"].tolist()) == [30, 60, 90]


@pytest.mark.integration
def test_dataframe_return_replaces_working_table(db, tmp_path):
    """Guarantee 6: pd.DataFrame return replaces the full working table regardless of output_mode."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)

    import pandas as pd
    fn_path = tmp_path / "rebuild.py"
    fn_path.write_text(
        "import pandas as pd\n"
        "def rebuild(df):\n"
        "    return pd.DataFrame({'only_col': [1, 2, 3]})\n"
    )
    _seed_df_transform_step(db, source_id, "rebuild", str(fn_path), output_mode="append")

    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    # The original columns (id, val) should be replaced by only_col
    assert list(df.columns) == ["only_col"]
    assert sorted(df["only_col"].tolist()) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Failure / skip behaviour
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_failed_transform_step_has_error_status(db, tmp_path):
    """Guarantee 7: a crashed worker returns status=failed with error populated."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "boom", "raise RuntimeError('intentional crash')")
    _seed_transform_step(db, source_id, col_id, "boom", fn_path)

    result = run_pipeline(db, source_id, "transforms")
    assert result["steps"][0]["status"] == "failed"
    assert result["steps"][0]["error"] is not None


@pytest.mark.integration
def test_subsequent_steps_run_after_failed_step(db, tmp_path):
    """Guarantee 8: subsequent steps still run after a failed step."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path_bad = _write_fn_file(tmp_path, "bad_step", "raise RuntimeError('fail')")
    _seed_transform_step(db, source_id, col_id, "bad_step", fn_path_bad, position=0)

    fn_path_ok = _write_fn_file(tmp_path, "ok_step", "return data")
    _seed_transform_step(db, source_id, col_id, "ok_step", fn_path_ok, output_mode="append", position=1)

    result = run_pipeline(db, source_id, "transforms")
    assert len(result["steps"]) == 2
    assert result["steps"][0]["status"] == "failed"
    assert result["steps"][1]["status"] == "ok"


# ---------------------------------------------------------------------------
# Validation side-effect isolation
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_validation_step_returns_pass_fail_counts(db, tmp_path):
    """Guarantee 9: validation step returns rows_passed and rows_failed counts."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    # All 3 vals (10, 20, 30) are > 5, so all pass
    fn_path = _write_fn_file(tmp_path, "gt5", "return data > 5")
    _seed_validation_step(db, source_id, col_id, "gt5", fn_path, position=0)

    result = run_pipeline(db, source_id, "validations")
    step = result["steps"][0]
    assert step["status"] == "ok"
    assert step["rows_passed"] == 3
    assert step["rows_failed"] == 0


@pytest.mark.integration
def test_validation_step_does_not_modify_instance_table(db, tmp_path):
    """Guarantee 10: validation run leaves the instance table unchanged."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    tname = instance_table_name(source_id)
    before_cols = [r[0] for r in db.execute(f'DESCRIBE "{tname}"').fetchall()]

    fn_path = _write_fn_file(tmp_path, "always_true", "return data > 0")
    _seed_validation_step(db, source_id, col_id, "always_true", fn_path, position=0)

    run_pipeline(db, source_id, "validations")
    after_cols = [r[0] for r in db.execute(f'DESCRIBE "{tname}"').fetchall()]
    assert before_cols == after_cols


# ---------------------------------------------------------------------------
# run_type filtering
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_type_transforms_only_executes_transforms(db, tmp_path):
    """Guarantee 11: run_type=transforms skips validation steps."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_t = _write_fn_file(tmp_path, "t_fn", "return data")
    _seed_transform_step(db, source_id, col_id, "t_fn", fn_t, position=0)

    fn_v = _write_fn_file(tmp_path, "v_fn", "return data > 0")
    _seed_validation_step(db, source_id, col_id, "v_fn", fn_v, position=1)

    result = run_pipeline(db, source_id, "transforms")
    fn_types = [s["function_type"] for s in result["steps"]]
    assert all(t == "transform" for t in fn_types)
    assert len(fn_types) == 1


@pytest.mark.integration
def test_run_type_validations_only_executes_validations(db, tmp_path):
    """Guarantee 12: run_type=validations skips transform steps."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_t = _write_fn_file(tmp_path, "t2_fn", "return data")
    _seed_transform_step(db, source_id, col_id, "t2_fn", fn_t, position=0)

    fn_v = _write_fn_file(tmp_path, "v2_fn", "return data > 0")
    _seed_validation_step(db, source_id, col_id, "v2_fn", fn_v, position=1)

    result = run_pipeline(db, source_id, "validations")
    # Validation steps now return per-function entries (no function_type field)
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert "function_name" in step
    assert step["function_name"] == "v2_fn"


@pytest.mark.integration
def test_run_type_set_executes_only_specified_set(db, tmp_path):
    """Guarantee 13: run_type=set executes only the specified set."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_a = _write_fn_file(tmp_path, "step_a", "return data")
    sfm_a, set_a_id = _seed_transform_step(db, source_id, col_id, "step_a", fn_a, position=0)

    fn_b = _write_fn_file(tmp_path, "step_b", "return data")
    sfm_b, set_b_id = _seed_transform_step(db, source_id, col_id, "step_b", fn_b, output_mode="append", position=1)

    result = run_pipeline(db, source_id, "set", set_id=set_a_id)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["source_function_map_id"] == str(sfm_a)


# ---------------------------------------------------------------------------
# Element-wise execution for column_backed (str) validation functions
# ---------------------------------------------------------------------------

def _seed_str_validation_step(db, source_id, column_id, fn_name, module_path, position=1):
    """Seed a validation function with a str-typed param bound to column_id."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "scalar", fn_name, None, "bool",
         f"value: str", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
        [param_id, param_ch, "value", "str", fn_id],
    )

    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, set_ch, fn_name, None],
    )
    set_map_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_map_id, set_id, fn_id, 0],
    )

    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )

    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute(
        "INSERT INTO alias_map VALUES (?, ?, ?, ?)",
        [alias_id, column_id, param_id, source_id],
    )
    return sfm_id, set_id


def _seed_unbound_series_validation_step(db, source_id, fn_name, module_path, position=0):
    """Seed a validation function with an unbound pd.Series param."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "pd.series", fn_name, None, "pd.Series[bool]",
         "data: pd.Series", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
        [param_id, param_ch, "data", "pd.Series", fn_id],
    )

    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, set_ch, fn_name, None],
    )
    set_map_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [set_map_id, set_id, fn_id, 0],
    )

    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )
    # No alias_map row — param is intentionally unbound
    return sfm_id, set_id


@pytest.mark.integration
def test_str_bool_validation_column_backed_element_wise(db, tmp_path):
    """Guarantee: str->bool validation bound to a column runs element-wise.
    rows_passed + rows_failed must equal total rows in the source.
    """
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'id'",
        [source_id],
    ).fetchone()[0]

    # str -> bool: checks if value is not 'r1'
    fn_path = tmp_path / "is_not_r1.py"
    fn_path.write_text("def is_not_r1(value: str) -> bool:\n    return value != 'r1'\n")
    _seed_str_validation_step(db, source_id, col_id, "is_not_r1", str(fn_path), position=0)

    result = run_pipeline(db, source_id, "validations")
    assert result is not None
    steps = result["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["status"] == "ok", f"step failed: {step.get('error')}"
    # 3 rows total: r1 fails, r2 and r3 pass
    assert step["rows_passed"] + step["rows_failed"] == 3
    assert step["rows_passed"] == 2
    assert step["rows_failed"] == 1


@pytest.mark.integration
def test_unbound_series_param_fails_with_unbound_message(db, tmp_path):
    """Guarantee: pd.Series param with no column binding produces status='failed'
    and error message containing 'unbound'.
    """
    source_id, _ = _register_source_and_ingest(db, tmp_path)

    fn_path = tmp_path / "check_series.py"
    fn_path.write_text("import pandas as pd\ndef check_series(data: pd.Series) -> pd.Series:\n    return data > 0\n")
    _seed_unbound_series_validation_step(db, source_id, "check_series", str(fn_path), position=0)

    result = run_pipeline(db, source_id, "validations")
    assert result is not None
    steps = result["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["status"] == "failed"
    assert "unbound" in (step.get("error") or "").lower()
