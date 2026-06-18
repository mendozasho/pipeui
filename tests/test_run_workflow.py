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

import pandas as pd
import pytest

from pipeui.backend.data.base.ids import content_hash_id
from pipeui.backend.data.base.tables import instance_table_name
from pipeui.backend.domain.sources.create import create_source
from pipeui.backend.domain.sources.ingestion import ingest_source
from pipeui.backend.domain.runner.run import run_pipeline
from pipeui.backend.data.runner.staging import staging_prefix
from pipeui.backend.data.runner.step_loader import fetch_steps
from pipeui.backend.domain.functions.attach import AttachBinding, attach_function
from tests.conftest import make_registered_source


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


def _seed_transform_step(db, source_id, column_id, fn_name, module_path, output_mode="append",
                         position=0, append_name=None):
    """Seed a transform function + set attached to source_id, bound to column_id.

    append_name, when given, is persisted to source_function_map.append_name so the
    runtime names the appended column by it (slice 4b).
    """
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
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode, append_name) VALUES (?, ?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, output_mode, append_name],
    )

    # alias_map binding
    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
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
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )

    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
        [alias_id, column_id, param_id, source_id],
    )
    return sfm_id, set_id


def _list_staging_tables(db, source_id):
    prefix = staging_prefix(source_id)
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
    with patch("pipeui.backend.domain.runner.run.time") as mock_time:
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
    assert "doubled_val" in df.columns  # #264: auto-label = fn_name + bound column


@pytest.mark.integration
def test_append_run_names_column_by_persisted_append_name(db, tmp_path):
    """Slice 4b AC #2: an append-mode run whose attach carried a persisted (cleaned)
    append_name names the new column by that name, not the auto fn-name label.
    Verified end-to-end from the persisted source_function_map.append_name column."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    # Persisted append_name is the already-normalized "risk_score" (attach cleans it).
    _seed_transform_step(
        db, source_id, col_id, "doubled", fn_path,
        output_mode="append", append_name="risk_score",
    )

    result = run_pipeline(db, source_id, "transforms")
    assert result["steps"][0]["status"] == "ok"

    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "risk_score" in df.columns       # named by the persisted append_name
    assert "doubled" not in df.columns      # NOT the auto fn-name label
    assert "val" in df.columns              # original preserved


@pytest.mark.integration
def test_append_run_uses_auto_label_when_no_persisted_name(db, tmp_path):
    """Slice 4b AC #3 (regression): append mode with NO persisted name uses the cleaned
    auto-label, which is the function name + the bound column (#264)."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]

    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(
        db, source_id, col_id, "doubled", fn_path,
        output_mode="append", append_name=None,
    )

    result = run_pipeline(db, source_id, "transforms")
    assert result["steps"][0]["status"] == "ok"

    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "doubled_val" in df.columns      # #264: auto-label = fn_name + bound column


@pytest.mark.integration
def test_append_auto_label_multicol_is_fn_name_plus_each_column(db, tmp_path):
    """#264: append with no name bound to N columns -> one appended column per bound
    column, each named <fn>_<column> (readable + distinct), not <fn> / <fn>_2."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b")]
    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_multicol_transform_step(db, source_id, col_ids, "doubled", fn_path, output_mode="append")

    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "doubled_a" in df.columns and "doubled_b" in df.columns, list(df.columns)
    assert "doubled_2" not in df.columns  # not the old fn-name + numeric-suffix scheme


@pytest.mark.integration
def test_per_function_output_config_overrides_step_level(db, tmp_path):
    """#264: output config is per-function. A function_output_config row for
    (sfm, function) is used over the step-level source_function_map append_name."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "doubled", fn_path,
                         output_mode="append", append_name=None)

    fn_id = db.execute(
        "SELECT function_id FROM function_registry WHERE function_name = 'doubled'"
    ).fetchone()[0]
    sfm_id = db.execute(
        "SELECT source_function_map_id FROM source_function_map WHERE source_id = ?",
        [source_id],
    ).fetchone()[0]
    db.execute(
        "INSERT INTO function_output_config (source_function_map_id, function_id, output_mode, append_name) VALUES (?, ?, ?, ?)",
        [sfm_id, fn_id, "append", "myscore"],
    )

    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "myscore" in df.columns          # per-function append_name used
    assert "doubled_val" not in df.columns  # auto-label NOT used when per-function name set


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
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )

    alias_id = content_hash_id("alias_map", str(param_id), str(column_id), str(source_id))
    db.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
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
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
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
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
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


# ---------------------------------------------------------------------------
# Slice runner-execution/1 — stateful executor produces RunResults (N=1 path)
# Acceptance #0: a single-column validation run returns one RunResult carrying
#   status + pass/fail counts; identity = deterministic UUID5(function, bundle, source).
# Acceptance #1: a single-column transform run returns one RunResult; N=1 behavior
#   unchanged (regression-locked at the workflow seam).
# ---------------------------------------------------------------------------

import re as _re


def _val_col(db, source_id, col_name="val"):
    return db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = ?",
        [source_id, col_name],
    ).fetchone()[0]


@pytest.mark.integration
def test_single_column_validation_returns_runresult_with_identity(db, tmp_path):
    """Acceptance #0: one RunResult with status, pass/fail counts, and a UUID5 result_id."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id)

    fn_path = _write_fn_file(tmp_path, "gt15", "return data > 15")  # 10 fails; 20,30 pass
    _seed_validation_step(db, source_id, col_id, "gt15", fn_path, position=0)

    result = run_pipeline(db, source_id, "validations")
    steps = result["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["status"] == "ok"
    assert step["rows_passed"] == 2
    assert step["rows_failed"] == 1
    # RunResult identity is a deterministic shortened UUID5 (hex chars).
    assert "result_id" in step
    assert _re.fullmatch(r"[0-9a-f]+", step["result_id"])
    # Readable, normalized label (no leading underscore / odd tokens).
    assert step["label"]
    assert not step["label"].startswith("_")


@pytest.mark.integration
def test_single_column_validation_result_id_is_deterministic(db, tmp_path):
    """Acceptance #0: identity is stable across runs (deterministic UUID5)."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id)
    fn_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    _seed_validation_step(db, source_id, col_id, "gt0", fn_path, position=0)

    first = run_pipeline(db, source_id, "validations")["steps"][0]["result_id"]
    second = run_pipeline(db, source_id, "validations")["steps"][0]["result_id"]
    assert first == second


@pytest.mark.integration
def test_single_column_transform_returns_runresult_unchanged(db, tmp_path):
    """Acceptance #1: a single-column transform yields one RunResult; N=1 behavior intact."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id)

    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "doubled", fn_path, output_mode="append")

    result = run_pipeline(db, source_id, "transforms")
    steps = result["steps"]
    assert len(steps) == 1
    step = steps[0]
    # Regression lock: existing N=1 keys unchanged.
    assert step["status"] == "ok"
    assert step["function_type"] == "transform"
    assert step["rows_affected"] == 3
    # The new column is still appended (working-table behavior unchanged).
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "doubled_val" in df.columns  # #264: auto-label = fn_name + bound column
    # RunResult identity surfaced additively.
    assert "result_id" in step
    assert step["label"]


# ---------------------------------------------------------------------------
# Slice 2 — runner reads bound columns in add-order (alias_map.position)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fetch_steps_reads_bindings_in_position_order(db):
    """Slice 2 #2: fetch_steps returns a param's bound columns ORDER BY position,
    not alphabetically by column_name."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    # Register a multi-column pd.Series function and attach in non-alphabetical order
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "pd.series", "fn_runner_pos", "doc", "pd.Series",
         "cols: pd.Series", "validation", "/tmp/fn_runner_pos.py", True],
    )
    param_id = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_id, uuid.uuid4(), "cols", "pd.Series", fn_id],
    )
    ordered = [col_ids[2], col_ids[0], col_ids[1]]  # add-order: col_2, col_0, col_1
    attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=ordered)],
        function_id=fn_id,
    )

    steps = fetch_steps(db, source_id)
    # fetch_steps now returns typed FunctionStepContext carriers with FunctionSpec
    # members; params stay typed Mapping rows (the depth boundary).
    param = steps[0].functions[0].params[0]
    assert param["bindings"] == ["col_2", "col_0", "col_1"]


# ---------------------------------------------------------------------------
# Slice runner-execution/3 — executor bundle loop for validations
# Acceptance #2: a validation function bound to N columns -> N RunResults, one per
#   bundle, each labeled by its varying column(s).
# Acceptance #3: a scalar-shaped validation bound to a column runs once per record
#   -> normalized boolean vector (scalar run).
# NOTE (slice-2 caveat): NEW alias_map fixtures use explicit-column INSERTs.
# ---------------------------------------------------------------------------

def _register_multicol_source_and_ingest(db, tmp_path, name="multi", cols=("a", "b", "c")):
    """Create a source with id + N numeric columns and ingest 3 rows of ascending values."""
    header = ["id", *cols]
    rows = [
        ["r1", *[10 + j for j in range(len(cols))]],
        ["r2", *[20 + j for j in range(len(cols))]],
        ["r3", *[30 + j for j in range(len(cols))]],
    ]
    path = make_csv(tmp_path, f"{name}.csv", header, rows)
    source_id, failed = create_source(db, path, name, "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    return source_id, path


def _seed_multicol_validation_step(db, source_id, column_ids, fn_name, module_path,
                                   param_type="pd.Series", param_name="data", position=0):
    """Seed a validation function bound to MULTIPLE columns (one param, N columns).

    Writes explicit-column alias_map rows carrying add-order ``position`` (slice-2
    5th column). column_ids is an ordered list — position i is the i-th column.
    """
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "pd.series", fn_name, None, "pd.Series[bool]",
         f"{param_name}: {param_type}", "validation", module_path, True],
    )
    param_id = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_id, uuid.uuid4(), param_name, param_type, fn_id],
    )
    set_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, uuid.uuid4(), fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )
    for pos, col_id in enumerate(column_ids):
        alias_id = content_hash_id("alias_map", str(param_id), str(col_id), str(source_id))
        db.execute(
            "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
            [alias_id, col_id, param_id, source_id, pos],
        )
    return sfm_id, set_id, param_id


@pytest.mark.integration
def test_multi_column_validation_produces_one_runresult_per_bundle(db, tmp_path):
    """Acceptance #2: a validation bound to N columns yields N RunResults (one per bundle)."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b", "c"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b", "c")]

    fn_path = _write_fn_file(tmp_path, "gt15_multi", "return data > 15")
    _seed_multicol_validation_step(db, source_id, col_ids, "gt15_multi", fn_path)

    result = run_pipeline(db, source_id, "validations")
    steps = result["steps"]
    # 3 bound columns -> 3 bundles -> 3 RunResults.
    assert len(steps) == 3
    assert all(s["status"] == "ok" for s in steps), [s.get("error") for s in steps]


@pytest.mark.integration
def test_multi_column_validation_results_labeled_by_varying_column(db, tmp_path):
    """Acceptance #2: each per-bundle RunResult is labeled by its varying column."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b", "c"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b", "c")]

    fn_path = _write_fn_file(tmp_path, "gt0_multi", "return data > 0")
    _seed_multicol_validation_step(db, source_id, col_ids, "gt0_multi", fn_path)

    result = run_pipeline(db, source_id, "validations")
    labels = {s["label"] for s in result["steps"]}
    # One label per varying column, in normalized form.
    assert labels == {"a", "b", "c"}


@pytest.mark.integration
def test_multi_column_validation_result_ids_are_distinct_per_bundle(db, tmp_path):
    """Acceptance #2: per-bundle identity differs — UUID5(function, bundle, source)."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b", "c"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b", "c")]

    fn_path = _write_fn_file(tmp_path, "gt0_ids", "return data > 0")
    _seed_multicol_validation_step(db, source_id, col_ids, "gt0_ids", fn_path)

    result = run_pipeline(db, source_id, "validations")
    result_ids = [s["result_id"] for s in result["steps"]]
    assert len(set(result_ids)) == 3


@pytest.mark.integration
def test_multi_column_validation_counts_are_per_column(db, tmp_path):
    """Acceptance #2: each bundle's pass/fail counts reflect its own column's values.

    Threshold 25: column a = 10,20,30 -> 1 pass; ascending columns shift but each
    bundle reports counts independently (not a single merged count).
    """
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b", "c"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b", "c")]

    fn_path = _write_fn_file(tmp_path, "gt25_multi", "return data > 25")
    _seed_multicol_validation_step(db, source_id, col_ids, "gt25_multi", fn_path)

    result = run_pipeline(db, source_id, "validations")
    by_label = {s["label"]: s for s in result["steps"]}
    # Each column has rows 10/20/30 (+offset); only the 30-row passes > 25.
    for label in ("a", "b", "c"):
        assert by_label[label]["rows_passed"] + by_label[label]["rows_failed"] == 3
        assert by_label[label]["rows_passed"] == 1


@pytest.mark.integration
def test_scalar_validation_bound_to_column_normalizes_to_vector(db, tmp_path):
    """Acceptance #3: a scalar-shaped validation (str -> bool param) bound to a column
    runs once per record and yields a normalized boolean vector (passed+failed == rows)."""
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id, "id")

    fn_path = tmp_path / "scalar_not_r1.py"
    fn_path.write_text("def scalar_not_r1(value: str) -> bool:\n    return value != 'r1'\n")
    # Single-column scalar bind (N=1 bundle) — the scalar RUN is the per-row loop.
    _seed_multicol_validation_step(
        db, source_id, [col_id], "scalar_not_r1", str(fn_path),
        param_type="str", param_name="value",
    )

    result = run_pipeline(db, source_id, "validations")
    steps = result["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["status"] == "ok", step.get("error")
    # Normalized vector over 3 records: r1 fails, r2/r3 pass.
    assert step["rows_passed"] + step["rows_failed"] == 3
    assert step["rows_passed"] == 2
    assert step["rows_failed"] == 1


@pytest.mark.integration
def test_scalar_validation_over_multiple_columns_does_n_scalar_runs(db, tmp_path):
    """Acceptance #2+#3: a scalar-shaped validation bound to N columns does N scalar
    runs -> N RunResults, each a per-record normalized vector."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b")]

    fn_path = tmp_path / "scalar_gt15.py"
    fn_path.write_text("def scalar_gt15(value: int) -> bool:\n    return value > 15\n")
    _seed_multicol_validation_step(
        db, source_id, col_ids, "scalar_gt15", str(fn_path),
        param_type="int", param_name="value",
    )

    result = run_pipeline(db, source_id, "validations")
    steps = result["steps"]
    assert len(steps) == 2
    for s in steps:
        assert s["status"] == "ok", s.get("error")
        assert s["rows_passed"] + s["rows_failed"] == 3


# ---------------------------------------------------------------------------
# #258 — executor resolves + broadcasts scalar params (source_scalar_map / default)
# ---------------------------------------------------------------------------

def _seed_scalar_param_validation(
    db, source_id, column_ids, fn_name, module_path,
    scalar_value="15", persist_scalar=True, has_default=False, default_value=None,
):
    """Seed a validation with a column-bound `value` param (N columns) + a scalar
    `threshold: int` param. Optionally persist threshold to source_scalar_map and/or
    mark it with a captured Python default. Mirrors how a real upload like
    is_above_threshold(value, threshold) is attached."""
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "scalar", fn_name, None, "bool",
         "(value: int, threshold: int)", "validation", module_path, True],
    )
    value_pid, threshold_pid = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [value_pid, uuid.uuid4(), "value", "int", fn_id],
    )
    db.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id, has_default, default_value) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [threshold_pid, uuid.uuid4(), "threshold", "int", fn_id, has_default, default_value],
    )
    set_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, uuid.uuid4(), fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, 0, "append"],
    )
    for pos, col_id in enumerate(column_ids):
        am_id = content_hash_id("alias_map", str(value_pid), str(col_id), str(source_id))
        db.execute(
            "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
            [am_id, col_id, value_pid, source_id, pos],
        )
    if persist_scalar:
        db.execute(
            "INSERT INTO source_scalar_map (scalar_map_id, source_id, param_id, value) VALUES (?, ?, ?, ?)",
            [uuid.uuid4(), source_id, threshold_pid, scalar_value],
        )
    return sfm_id


_THRESH_FN = "def is_above_threshold(value, threshold):\n    return value > threshold\n"


@pytest.mark.integration
def test_scalar_param_value_passed_from_source_scalar_map(db, tmp_path):
    """#258: the persisted scalar value reaches the function. Before the fix this
    crashed with TypeError: missing 'threshold' and surfaced as a failed 0/0."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_ids = [_val_col(db, source_id, "a")]
    fn_path = tmp_path / "thr.py"
    fn_path.write_text(_THRESH_FN)
    _seed_scalar_param_validation(db, source_id, col_ids, "is_above_threshold", str(fn_path), scalar_value="15")

    result = run_pipeline(db, source_id, "validations")
    s = result["steps"][0]
    assert s["status"] == "ok", s.get("error")
    assert s["rows_passed"] + s["rows_failed"] == 3  # real counts, not None/0


@pytest.mark.integration
def test_scalar_param_broadcasts_into_every_bundle(db, tmp_path):
    """#258 + bundles: value bound to N columns runs N bundles, each receiving the
    broadcast scalar — bundles still work, scalar reaches all of them."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b", "c"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b", "c")]
    fn_path = tmp_path / "thr3.py"
    fn_path.write_text(_THRESH_FN)
    _seed_scalar_param_validation(db, source_id, col_ids, "is_above_threshold", str(fn_path), scalar_value="15")

    result = run_pipeline(db, source_id, "validations")
    steps = result["steps"]
    assert len(steps) == 3  # 3 bundles, one RunResult each
    for s in steps:
        assert s["status"] == "ok", s.get("error")
        assert s["rows_passed"] + s["rows_failed"] == 3


@pytest.mark.integration
def test_scalar_param_falls_back_to_python_default(db, tmp_path):
    """#258: no persisted value but a captured default -> the default is used (runs)."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_ids = [_val_col(db, source_id, "a")]
    fn_path = tmp_path / "thrd.py"
    fn_path.write_text(_THRESH_FN)
    _seed_scalar_param_validation(
        db, source_id, col_ids, "is_above_threshold", str(fn_path),
        persist_scalar=False, has_default=True, default_value="15",
    )

    result = run_pipeline(db, source_id, "validations")
    s = result["steps"][0]
    assert s["status"] == "ok", s.get("error")
    assert s["rows_passed"] + s["rows_failed"] == 3


@pytest.mark.integration
def test_required_scalar_param_with_no_value_or_default_fails_cleanly(db, tmp_path):
    """#258: a required scalar (no value, no default) yields a clean structured failure
    naming the param — never a raw TypeError surfaced as 0/0."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_ids = [_val_col(db, source_id, "a")]
    fn_path = tmp_path / "thrr.py"
    fn_path.write_text(_THRESH_FN)
    _seed_scalar_param_validation(
        db, source_id, col_ids, "is_above_threshold", str(fn_path),
        persist_scalar=False, has_default=False,
    )

    result = run_pipeline(db, source_id, "validations")
    s = result["steps"][0]
    assert s["status"] == "failed"
    assert "threshold" in (s.get("error") or "")
    assert "required" in (s.get("error") or "").lower()


@pytest.mark.integration
def test_run_entry_points_delegate_to_single_runner(db, tmp_path, monkeypatch):
    """#258 single-runner invariant: run_validation_across_sources funnels through
    run_pipeline, so scalar resolution (and all execution policy) lives in one place.
    A future tab that runs functions must route through run_pipeline, not duplicate it."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_ids = [_val_col(db, source_id, "a")]
    fn_path = tmp_path / "thr_single.py"
    fn_path.write_text(_THRESH_FN)
    _seed_scalar_param_validation(db, source_id, col_ids, "is_above_threshold", str(fn_path), scalar_value="15")
    fn_id = db.execute(
        "SELECT function_id FROM function_registry WHERE function_name = 'is_above_threshold'"
    ).fetchone()[0]

    import pipeui.backend.domain.runner.run as run_mod
    seen: list[str] = []
    real = run_mod.run_pipeline

    def _spy(conn, source_id, run_type, **kw):
        seen.append(run_type)
        return real(conn, source_id, run_type, **kw)

    monkeypatch.setattr(run_mod, "run_pipeline", _spy)
    run_mod.run_validation_across_sources(db, fn_id)
    assert seen, "run_validation_across_sources must delegate to run_pipeline (single runner)"


# ---------------------------------------------------------------------------
# Slice runner-execution/4 — executor bundle loop for transforms (output_mode)
# Acceptance #0 append (N new columns, cleaned label), #1 replace (ordered
#   targets, count==bundles), #3 pd.DataFrame whole-table edge, #4 RunResult.
# Reuses the slice-3 _register_multicol_source_and_ingest / _val_col helpers.
# NOTE (slice-2 caveat): NEW alias_map fixtures use explicit-column INSERTs.
# ---------------------------------------------------------------------------

def _seed_multicol_transform_step(
    db, source_id, column_ids, fn_name, module_path,
    output_mode="append", output_targets=None, position=0,
):
    """Seed a pd.Series transform bound to N columns (argument bundles), with
    optional ordered output_target_map rows for a replace step. Returns sfm_id."""
    fn_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, uuid.uuid4(), "pd.series", fn_name, None, "pd.Series",
         "data: pd.Series", "transform", module_path, True],
    )
    param_id = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_id, uuid.uuid4(), "data", "pd.Series", fn_id],
    )
    set_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)", [set_id, uuid.uuid4(), fn_name, None])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, fn_id, 0])
    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, output_mode],
    )
    for pos, col_id in enumerate(column_ids):
        am_id = content_hash_id("alias_map", str(param_id), str(col_id), str(source_id))
        db.execute(
            "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
            [am_id, col_id, param_id, source_id, pos],
        )
    if output_targets:
        for pos, col_id in enumerate(output_targets):
            otm_id = content_hash_id("output_target_map", str(sfm_id), str(col_id), str(pos))
            db.execute(
                "INSERT INTO output_target_map (output_target_map_id, source_function_map_id, function_id, column_id, position) VALUES (?, ?, ?, ?, ?)",
                [otm_id, sfm_id, fn_id, col_id, pos],
            )
    return sfm_id


@pytest.mark.integration
def test_transform_append_multicol_adds_n_new_columns_no_collision(db, tmp_path):
    """Slice 4 #0: an append transform bound to N=2 columns adds exactly 2 new columns
    to the transformed report (one per bundle); originals preserved, no collisions."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b")]

    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_multicol_transform_step(db, source_id, col_ids, "doubled", fn_path, output_mode="append")

    result = run_pipeline(db, source_id, "transforms")
    assert all(s["status"] == "ok" for s in result["steps"]), [s.get("error") for s in result["steps"]]

    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "a" in df.columns and "b" in df.columns          # originals preserved
    new_cols = [c for c in df.columns if c not in ("id", "a", "b")]
    assert len(new_cols) == 2, f"expected 2 new append columns, got {new_cols}"
    # a=[10,20,30]->[20,40,60]; b=[11,21,31]->[22,42,62]
    vals = {tuple(sorted(df[c].tolist())) for c in new_cols}
    assert (20, 40, 60) in vals and (22, 42, 62) in vals


@pytest.mark.integration
def test_transform_append_multicol_yields_one_runresult_per_bundle(db, tmp_path):
    """Slice 4 #4: each transform run yields a RunResult — N=2 bundles -> 2 RunResults,
    each labelled by its varying column."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b"))
    col_ids = [_val_col(db, source_id, c) for c in ("a", "b")]

    fn_path = _write_fn_file(tmp_path, "doubled2", "return data * 2")
    _seed_multicol_transform_step(db, source_id, col_ids, "doubled2", fn_path, output_mode="append")

    result = run_pipeline(db, source_id, "transforms")
    assert len(result["steps"]) == 2
    for entry in result["steps"]:
        assert entry["function_type"] == "transform"
        assert entry.get("result_id")
        assert entry.get("label")
    assert {e["label"] for e in result["steps"]} == {"a", "b"}


@pytest.mark.integration
def test_transform_replace_multicol_overwrites_ordered_targets(db, tmp_path):
    """Slice 4 #1/#2: a replace transform bound to 2 columns overwrites the 2 ordered
    target columns read from output_target_map (bundle i -> target i), in position order."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b"))
    col_a, col_b = [_val_col(db, source_id, c) for c in ("a", "b")]

    fn_path = _write_fn_file(tmp_path, "plus100", "return data + 100")
    # Bundles bind [a, b]; targets reversed [b, a] proves order-by-position:
    # bundle 0 (a+100) -> target b, bundle 1 (b+100) -> target a.
    _seed_multicol_transform_step(
        db, source_id, [col_a, col_b], "plus100",
        fn_path, output_mode="replace", output_targets=[col_b, col_a],
    )

    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert set(df.columns) == {"id", "a", "b"}             # no new columns
    assert sorted(df["b"].tolist()) == [110, 120, 130]     # a=[10,20,30]+100
    assert sorted(df["a"].tolist()) == [111, 121, 131]     # b=[11,21,31]+100


@pytest.mark.integration
def test_transform_replace_single_varying_defaults_to_input_column(db, tmp_path):
    """Slice 4 #1: with no explicit output-target, replace defaults to overwriting the
    input varying column (single-varying default, PRD)."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_a = _val_col(db, source_id, "a")

    fn_path = _write_fn_file(tmp_path, "triple", "return data * 3")
    _seed_multicol_transform_step(db, source_id, [col_a], "triple", fn_path, output_mode="replace")

    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert set(df.columns) == {"id", "a"}
    assert sorted(df["a"].tolist()) == [30, 60, 90]        # a=[10,20,30]*3


@pytest.mark.integration
def test_dataframe_transform_one_run_regardless_of_output_mode(db, tmp_path):
    """Slice 4 #3: a pd.DataFrame transform receives and returns the whole table in ONE
    run (no bundle expansion), regardless of output_mode — one RunResult."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a", "b"))
    fn_path = tmp_path / "addcol.py"
    fn_path.write_text(
        "def addcol(df):\n"
        "    out = df.copy()\n"
        "    out['sum_ab'] = out['a'] + out['b']\n"
        "    return out\n"
    )
    _seed_df_transform_step(db, source_id, "addcol", str(fn_path), output_mode="replace")

    result = run_pipeline(db, source_id, "transforms")
    assert len(result["steps"]) == 1                       # ONE run, not one per column
    assert result["steps"][0]["status"] == "ok", result["steps"][0].get("error")
    staging = _list_staging_tables(db, source_id)[0]
    df = db.execute(f'SELECT * FROM "{staging}"').df()
    assert "sum_ab" in df.columns
    # a=[10,20,30], b=[11,21,31] -> [21,41,61]
    assert sorted(df["sum_ab"].tolist()) == [21, 41, 61]


# ---------------------------------------------------------------------------
# #266 — a function set is a transparent container: every function in it runs
# by its own type (a set must not be routed by a single dominant type).
# ---------------------------------------------------------------------------

def _seed_mixed_set(db, source_id, col_id, val_path, tfm_path):
    """One function_set holding a validation (gt0) + a transform (dbl), both bound
    to col_id. Returns (set_id, val_fn_id, tfm_fn_id)."""
    set_id = uuid.uuid4()
    db.execute("INSERT INTO function_set VALUES (?, ?, ?, ?)",
               [set_id, uuid.uuid4(), "mixedset", None])
    val_fn, tfm_fn = uuid.uuid4(), uuid.uuid4()
    db.execute("INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               [val_fn, uuid.uuid4(), "pd.series", "gt0", None, "pd.Series[bool]",
                "data: pd.Series", "validation", val_path, True])
    db.execute("INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               [tfm_fn, uuid.uuid4(), "pd.series", "dbl", None, "pd.Series",
                "data: pd.Series", "transform", tfm_path, True])
    val_param, tfm_param = uuid.uuid4(), uuid.uuid4()
    for pid, fid in ((val_param, val_fn), (tfm_param, tfm_fn)):
        db.execute("INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
                   [pid, uuid.uuid4(), "data", "pd.Series", fid])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, val_fn, 0])
    db.execute("INSERT INTO function_set_map VALUES (?, ?, ?, ?)", [uuid.uuid4(), set_id, tfm_fn, 1])
    sfm_id = uuid.uuid4()
    db.execute("INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode, append_name) VALUES (?, ?, ?, ?, ?, ?)",
               [sfm_id, source_id, set_id, 0, "append", None])
    for pid in (val_param, tfm_param):
        am = content_hash_id("alias_map", str(pid), str(col_id), str(source_id))
        db.execute("INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
                   [am, col_id, pid, source_id, 0])
    return set_id, val_fn, tfm_fn


@pytest.mark.integration
def test_mixed_set_validations_run_not_dropped(db, tmp_path):
    """#266: a validation inside a transform-containing set must run on a validations
    run — the set's dominant type must not route the whole set to one executor."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_id = _val_col(db, source_id, "a")
    val_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    tfm_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_mixed_set(db, source_id, col_id, val_path, tfm_path)

    vres = run_pipeline(db, source_id, "validations")
    names = [s.get("function_name") for s in vres["steps"]]
    assert "gt0" in names, f"validation in a mixed set was dropped: {names}"


@pytest.mark.integration
def test_mixed_set_all_run_processes_every_function(db, tmp_path):
    """#266: an `all` run on a mixed set processes BOTH the validation and the transform."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_id = _val_col(db, source_id, "a")
    val_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    tfm_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_mixed_set(db, source_id, col_id, val_path, tfm_path)

    ares = run_pipeline(db, source_id, "all")
    names = [s.get("function_name") for s in ares["steps"]]
    assert "gt0" in names and "dbl" in names, f"every function in the set must run: {names}"


# ---------------------------------------------------------------------------
# Slice runner-resolution-model #1 — resolve_frame input-source seam
#
# resolve_frame(conn, source_id, mode) -> (frame, ref):
#   raw         -> the source's instance table contents
#   transformed -> the source's latest staging table, else materialize-if-absent
#                  (run the source's pipeline once) — cycle-guarded, snapshot semantics
#   ref carries a deterministic UUID5 result_id (transformed) tied to the RunResult
#   identity scheme.  This slice does NOT change the join.
# ---------------------------------------------------------------------------

def _messy_source_and_ingest(db, tmp_path, name="messy"):
    """A source with null-containing / type-messy real-world rows.

    'amount' mixes ints with empty cells (NULL); 'region' mixes strings with empty
    cells.  Ingested as a real instance table so resolve_frame reads true data.
    """
    path = make_csv(
        tmp_path,
        f"{name}.csv",
        ["id", "amount", "region"],
        [
            ["r1", "10", "north"],
            ["r2", "", "south"],     # null amount
            ["r3", "30", ""],        # null region
            ["r4", "", ""],          # both null
        ],
    )
    source_id, failed = create_source(db, path, name, "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    return source_id, path


@pytest.mark.integration
def test_resolve_frame_raw_returns_instance_table(db, tmp_path):
    """AC1: resolve_frame(source, raw) returns the source's instance table contents."""
    from pipeui.backend.domain.runner.resolve import resolve_frame

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    expected = db.execute(
        f'SELECT * FROM "{instance_table_name(source_id)}"'
    ).df()

    frame, ref = resolve_frame(db, source_id, "raw")

    assert list(frame.columns) == list(expected.columns)
    assert len(frame) == len(expected)
    # value-level equality (no rows dropped / corrupted)
    assert frame.sort_values("id").reset_index(drop=True).equals(
        expected.sort_values("id").reset_index(drop=True)
    )
    assert ref.mode == "raw"
    assert ref.source_id == source_id


@pytest.mark.integration
def test_resolve_frame_transformed_returns_latest_staging(db, tmp_path):
    """AC2: resolve_frame(source, transformed) returns the latest staging table
    contents when one exists (no re-run)."""
    from pipeui.backend.domain.runner.resolve import resolve_frame

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "doubled", fn_path, output_mode="append")

    # Produce a staging table.
    run_pipeline(db, source_id, "transforms")
    staging = _list_staging_tables(db, source_id)
    assert len(staging) == 1
    expected = db.execute(f'SELECT * FROM "{staging[0]}"').df()

    frame, ref = resolve_frame(db, source_id, "transformed")

    # Latest staging used as-is (the appended transform column is present).
    assert "doubled_val" in frame.columns
    assert len(frame) == len(expected)
    # No new staging table was written (snapshot semantics — used existing).
    assert _list_staging_tables(db, source_id) == staging
    assert ref.mode == "transformed"
    assert ref.result_id is not None


@pytest.mark.integration
def test_resolve_frame_transformed_materializes_if_absent(db, tmp_path):
    """AC3: resolve_frame(source, transformed) for a source with NO staging table
    runs that source's pipeline once and returns its produced output."""
    from pipeui.backend.domain.runner.resolve import resolve_frame

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "doubled", fn_path, output_mode="append")

    # No staging table exists yet.
    assert _list_staging_tables(db, source_id) == []

    frame, ref = resolve_frame(
        db, source_id, "transformed",
        run_transforms=lambda c, sid: run_pipeline(c, sid, "transforms"),
    )

    # Materialized on demand: the transform output column is present...
    assert "doubled_val" in frame.columns
    # ...and a staging table now exists.
    assert len(_list_staging_tables(db, source_id)) == 1
    # produced output reflects the transform (val * 2)
    assert frame.sort_values("val")["doubled_val"].tolist() == [20, 40, 60]
    assert ref.mode == "transformed"


@pytest.mark.integration
def test_resolve_frame_transformed_no_transforms_falls_back_to_raw(db, tmp_path):
    """MINOR-2 (hostile-audit): a source with NO transform steps has no transformed
    output; resolve_frame(transformed) falls back to a staged copy of the RAW instance
    table so transformed resolution still yields a frame. DECIDED behavior (keep the
    fallback, not an error) — a source with nothing to transform has its raw data as
    its 'transformed output'. See resolve._materialize."""
    from pipeui.backend.domain.runner.resolve import resolve_frame
    from pipeui.backend.data.base.tables import instance_table_name

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    # A validations-only pipeline: it has a step, but nothing that produces transformed output.
    val_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    _seed_validation_step(db, source_id, col_id, "gt0", val_path, position=0)
    assert _list_staging_tables(db, source_id) == []

    frame, ref = resolve_frame(
        db, source_id, "transformed",
        run_transforms=lambda c, sid: run_pipeline(c, sid, "transforms"),
    )

    raw = db.execute(f'SELECT * FROM "{instance_table_name(source_id)}"').df()
    # Fallback: raw content returned as the transformed frame (no transform columns added).
    assert frame.sort_values("val")["val"].tolist() == raw.sort_values("val")["val"].tolist()
    assert frame.sort_values("val")["val"].tolist() == [10, 20, 30]
    # A staged copy was created so the reference resolves to a real table.
    assert len(_list_staging_tables(db, source_id)) == 1
    assert ref.mode == "transformed"


@pytest.mark.integration
def test_resolve_frame_transformed_cycle_raises_naming_sources(db, tmp_path):
    """AC4: a transformed reference forming a cycle (A->C->A) raises an error naming
    the sources in the cycle and does not loop."""
    from pipeui.backend.domain.runner.resolve import resolve_frame, TransformedCycleError
    from pipeui.backend.domain.functions.builtins import attach_builtin

    # Two sources, each with a transformed-join pointing at the other.
    src_a, _ = _register_source_and_ingest(db, tmp_path, name="a_src")
    src_c, _ = _register_source_and_ingest(db, tmp_path, name="c_src")

    # A joins C's transformed output; C joins A's transformed output.
    join_a = {
        "right_source_id": str(src_c), "join_type": "inner",
        "use_transformed": True,
        "on": [{"left_col": "id", "right_col": "id"}],
    }
    join_c = {
        "right_source_id": str(src_a), "join_type": "inner",
        "use_transformed": True,
        "on": [{"left_col": "id", "right_col": "id"}],
    }
    assert attach_builtin(db, src_a, "join", join_a)["ok"]
    assert attach_builtin(db, src_c, "join", join_c)["ok"]

    with pytest.raises(TransformedCycleError) as exc:
        resolve_frame(
            db, src_a, "transformed",
            run_transforms=lambda c, sid: run_pipeline(c, sid, "transforms"),
        )

    msg = str(exc.value)
    assert str(src_a) in msg and str(src_c) in msg


@pytest.mark.integration
def test_resolve_frame_transformed_result_id_is_deterministic(db, tmp_path):
    """AC5: the ref returned for a transformed frame carries a deterministic UUID5
    result_id (equal inputs -> equal id) consistent with the RunResult identity
    scheme."""
    from pipeui.backend.domain.runner.resolve import resolve_frame
    from pipeui.backend.data.base.results import RunResult

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = 'val'",
        [source_id],
    ).fetchone()[0]
    fn_path = _write_fn_file(tmp_path, "doubled", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "doubled", fn_path, output_mode="append")

    run_pipeline(db, source_id, "transforms")

    _, ref1 = resolve_frame(db, source_id, "transformed")
    _, ref2 = resolve_frame(db, source_id, "transformed")

    # Same source + same latest staging snapshot -> same id.
    assert ref1.result_id == ref2.result_id
    # Consistent with the RunResult identity scheme (short UUID5 hex).
    assert isinstance(ref1.result_id, str)
    assert len(ref1.result_id) == len(
        RunResult(
            function_name="x", function_type="transform",
            source_id=source_id, bundle_key="", label="x", status="ok",
        ).result_id
    )


@pytest.mark.integration
def test_resolve_frame_correct_over_messy_null_data(db, tmp_path):
    """AC6: resolve_frame returns correct rows over null-containing / type-messy
    real-world data without dropping or corrupting rows."""
    from pipeui.backend.domain.runner.resolve import resolve_frame

    source_id, _ = _messy_source_and_ingest(db, tmp_path)
    expected = db.execute(
        f'SELECT * FROM "{instance_table_name(source_id)}"'
    ).df()

    frame, _ = resolve_frame(db, source_id, "raw")

    # All four rows present — nulls not dropped.
    assert len(frame) == 4
    assert sorted(frame["id"].tolist()) == ["r1", "r2", "r3", "r4"]
    # Null cells preserved (not coerced to 0 / "").
    assert frame.sort_values("id").reset_index(drop=True).equals(
        expected.sort_values("id").reset_index(drop=True)
    )
    # The both-null row r4 still has nulls in amount and region.
    r4 = frame[frame["id"] == "r4"].iloc[0]
    assert pd.isna(r4["amount"]) and pd.isna(r4["region"])


# ---------------------------------------------------------------------------
# Slice runner-resolution-model #3 — Uniform StepExecutor contract +
# step-type registry (sub-issues #19, #20).
#
# Behavior-preserving refactor: the inline if/elif step dispatch in run_pipeline
# is replaced by a StepExecutor resolved from a step-type registry, backed by
# class-based StepContext factory constructors over the existing map tables.
# Every test below also relies on the full suite staying green (the pre-existing
# function/built-in behavioral guarantees above).
# ---------------------------------------------------------------------------


def _seed_builtin_filter_step(db, source_id, column, value, position=1, operator="gte"):
    """Attach a built-in filter step keeping rows where `column operator value`."""
    from pipeui.backend.domain.functions.builtins import attach_builtin

    res = attach_builtin(
        db, source_id, "filter",
        {"column": column, "operator": operator, "value": str(value)},
    )
    assert res["ok"], res
    return res["step_id"]


# --- #19: StepContext factory constructors -------------------------------

@pytest.mark.unit
def test_stepcontext_from_function_carries_step_dict_keys(db, tmp_path):
    """#19: fetch_steps produces the typed FunctionStepContext carrier — its fields
    (position, set_id, set_name, source_function_map_id, functions) are typed
    attributes, not dict keys. The loader tags the step SET so it routes to the
    function-set adapter; from_function (exercised by the adapter and below) is the
    FUNCTION-tagged sibling — both build the same FunctionStepContext shape."""
    from pipeui.backend.data.runner.step_loader import fetch_steps
    from pipeui.backend.data.runner.steps import (
        FUNCTION,
        FunctionSpec,
        FunctionStepContext,
        StepContext,
    )

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id, "val")
    fn_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "dbl", fn_path, output_mode="append")

    ctx = fetch_steps(db, source_id)[0]
    assert isinstance(ctx, FunctionStepContext)
    assert isinstance(ctx.position, int)
    # Properties the step dict held are all typed attributes on the context.
    assert ctx.set_id and isinstance(ctx.set_id, str)
    assert ctx.set_name == "dbl"
    assert ctx.source_function_map_id and isinstance(ctx.source_function_map_id, str)
    assert isinstance(ctx.functions, tuple)
    assert all(isinstance(m, FunctionSpec) for m in ctx.functions)
    assert ctx.functions[0].function_name == "dbl"

    # from_function builds the same shape, FUNCTION-tagged (the per-member dispatch tag).
    fn_ctx = StepContext.from_function({
        "source_function_map_id": ctx.source_function_map_id,
        "set_id": ctx.set_id, "set_name": ctx.set_name, "position": ctx.position,
        "output_mode": ctx.output_mode, "append_name": ctx.append_name,
        "output_targets": ctx.output_targets, "functions": ctx.functions,
    })
    assert fn_ctx.step_type == FUNCTION
    assert fn_ctx.functions == ctx.functions


@pytest.mark.unit
def test_stepcontext_from_set_carries_step_dict_keys(db, tmp_path):
    """#19: StepContext.from_set builds the FunctionStepContext from a loader row,
    tagged SET (the set-adapter dispatch tag) but carrying the same typed fields."""
    from pipeui.backend.data.runner.step_loader import fetch_steps
    from pipeui.backend.data.runner.steps import SET, StepContext

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id, "val")
    fn_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "dbl", fn_path, output_mode="append")

    fn_ctx = fetch_steps(db, source_id)[0]
    # Rebuild the same row through from_set; the loader is the producer, so reuse its
    # captured fields to feed the factory exactly as the loader does internally.
    ctx = StepContext.from_set({
        "source_function_map_id": fn_ctx.source_function_map_id,
        "set_id": fn_ctx.set_id,
        "set_name": fn_ctx.set_name,
        "position": fn_ctx.position,
        "output_mode": fn_ctx.output_mode,
        "append_name": fn_ctx.append_name,
        "output_targets": fn_ctx.output_targets,
        "functions": fn_ctx.functions,
    })
    assert ctx.step_type == SET
    assert ctx.set_id == fn_ctx.set_id
    assert ctx.functions == fn_ctx.functions
    assert ctx.position == fn_ctx.position


@pytest.mark.unit
def test_stepcontext_from_builtin_carries_builtin_keys(db, tmp_path):
    """#19: get_builtin_steps produces the typed BuiltinStepContext via
    StepContext.from_builtin — step_id / builtin_type / builtin_config / position
    are typed attributes."""
    from pipeui.backend.domain.functions.builtins import get_builtin_steps
    from pipeui.backend.data.runner.steps import BuiltinStepContext

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    _seed_builtin_filter_step(db, source_id, "val", 20, position=0)

    ctx = get_builtin_steps(db, source_id)[0]
    assert isinstance(ctx, BuiltinStepContext)
    assert ctx.step_type == "builtin"
    assert isinstance(ctx.position, int)
    assert ctx.step_id and isinstance(ctx.step_id, str)
    assert ctx.builtin_type == "filter"
    assert ctx.builtin_config["column"] == "val"


# --- #20: StepExecutor registry + dispatch swap --------------------------

@pytest.mark.unit
def test_step_executor_registry_has_function_and_builtin_executors():
    """#20: the step-type registry resolves an executor for each step type
    (function/set -> function executor; builtin -> builtin executor)."""
    from pipeui.backend.domain.runner.executors import STEP_EXECUTORS, StepExecutor

    assert "function" in STEP_EXECUTORS
    assert "builtin" in STEP_EXECUTORS
    for ex in STEP_EXECUTORS.values():
        assert isinstance(ex, StepExecutor)


@pytest.mark.integration
def test_run_pipeline_dispatches_through_registry(db, tmp_path):
    """#20: run_pipeline routes every step through the StepExecutor registry — a
    mixed function+builtin pipeline produces results only if both registry
    executors are invoked. Patching the registry to drop the builtin executor
    must make the builtin step vanish from the output (proving dispatch goes
    through the registry, not an inline branch)."""
    from pipeui.backend.domain.runner import executors as ex_mod

    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id, "val")
    fn_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "dbl", fn_path,
                         output_mode="append", position=0)
    _seed_builtin_filter_step(db, source_id, "val", 20, position=1, operator="gte")

    # Baseline: both the function transform and the builtin filter appear.
    full = run_pipeline(db, source_id, "all")
    names = [s.get("function_name") for s in full["steps"]]
    assert "dbl" in names
    assert "filter" in names

    # Remove the builtin executor from the registry; the builtin step must drop
    # out — proof the runner dispatches via the registry and not an inline if.
    patched = {k: v for k, v in ex_mod.STEP_EXECUTORS.items() if k != "builtin"}
    with patch.object(ex_mod, "STEP_EXECUTORS", patched):
        partial = run_pipeline(db, source_id, "all")
    names2 = [s.get("function_name") for s in partial["steps"]]
    assert "dbl" in names2
    assert "filter" not in names2


@pytest.mark.integration
def test_run_pipeline_mixed_output_matches_golden(db, tmp_path):
    """#20 idx0/idx2/idx3: registry dispatch produces results identical to the
    pre-refactor inline-dispatch output on a mixed function+builtin pipeline.

    The golden values below were captured from the PRE-refactor run_pipeline
    (inline if/elif dispatch) on this exact fixture; the refactor must reproduce
    them, proving the superseded path's behavior is preserved before and after.
    """
    source_id, _ = _register_source_and_ingest(db, tmp_path)
    col_id = _val_col(db, source_id, "val")
    fn_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_transform_step(db, source_id, col_id, "dbl", fn_path,
                         output_mode="append", position=0)
    _seed_builtin_filter_step(db, source_id, "val", 20, position=1, operator="gte")

    result = run_pipeline(db, source_id, "all")
    steps = result["steps"]

    # Two step-result entries: the function transform, then the builtin filter.
    by_name = {s.get("function_name"): s for s in steps}
    assert set(by_name) == {"dbl", "filter"}

    # Function transform: ran ok, appended a column, all 3 rows survive in working.
    tfm = by_name["dbl"]
    assert tfm["function_type"] == "transform"
    assert tfm["status"] == "ok"
    assert tfm["rows_affected"] == 3

    # Builtin filter: ran ok over the (transformed) working table; val>=20 keeps 2.
    flt = by_name["filter"]
    assert flt["step_type"] == "builtin"
    assert flt["builtin_type"] == "filter"
    assert flt["status"] == "ok"
    assert flt["rows_affected"] == 2

    # The final staging table reflects BOTH steps: the appended transform column
    # is present AND only the val>=20 rows remain (filter ran after transform).
    staging = _list_staging_tables(db, source_id)
    assert len(staging) == 1
    final = db.execute(f'SELECT * FROM "{staging[0]}"').df()
    assert "dbl_val" in final.columns
    assert sorted(final["val"].tolist()) == [20, 30]


# ---------------------------------------------------------------------------
# Slice runner-resolution-model #4 — Function-set adapter
#   A function set is flattened by an adapter into a stream of per-member
#   executions dispatched through the StepExecutor registry by each member's
#   step type, so a set behaves exactly like its members placed individually
#   and built-in members are additive later (heterogeneous-member readiness).
# ---------------------------------------------------------------------------

def _comparable_entry(entry):
    """The behavior-bearing fields of a step-result entry, identity stripped.

    result_id / set_id / set_name / source_function_map_id are identity/origin
    keys that differ between a set and the same functions placed on separate
    sources; the *behavior* of a flattened member is its function name, type,
    status and row counts.
    """
    return {
        "function_name": entry.get("function_name"),
        "function_type": entry.get("function_type"),
        "status": entry.get("status"),
        "rows_affected": entry.get("rows_affected"),
        "rows_passed": entry.get("rows_passed"),
        "rows_failed": entry.get("rows_failed"),
        "error": entry.get("error"),
    }


@pytest.mark.integration
def test_function_set_flattened_equals_members_placed_individually(db, tmp_path):
    """#14 idx0: a function set is flattened into per-member executions whose
    results are identical to the same functions placed individually on a source.

    A two-member set (validation gt0 @0, transform dbl @1) bound to column `a` is
    run via the adapter; the SAME two functions placed as separate single-function
    steps (same positions) on a second identical source are run individually. The
    behavior-bearing result fields must match member-for-member."""
    val_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    tfm_path = _write_fn_file(tmp_path, "dbl", "return data * 2")

    # Set source: gt0 + dbl in ONE function_set.
    set_src, _ = _register_multicol_source_and_ingest(db, tmp_path, name="setsrc", cols=("a",))
    set_col = _val_col(db, set_src, "a")
    _seed_mixed_set(db, set_src, set_col, val_path, tfm_path)
    set_result = run_pipeline(db, set_src, "all")

    # Individual source: gt0 and dbl placed as two separate steps, same positions.
    ind_src, _ = _register_multicol_source_and_ingest(db, tmp_path, name="indsrc", cols=("a",))
    ind_col = _val_col(db, ind_src, "a")
    _seed_validation_step(db, ind_src, ind_col, "gt0", val_path, position=0)
    _seed_transform_step(db, ind_src, ind_col, "dbl", tfm_path, output_mode="append", position=1)
    ind_result = run_pipeline(db, ind_src, "all")

    set_by_name = {e["function_name"]: _comparable_entry(e) for e in set_result["steps"]}
    ind_by_name = {e["function_name"]: _comparable_entry(e) for e in ind_result["steps"]}

    assert set(set_by_name) == {"gt0", "dbl"}
    assert set_by_name == ind_by_name


@pytest.mark.integration
def test_set_containing_pipeline_output_unchanged_golden(db, tmp_path):
    """#14 idx1: run_pipeline output for a set-containing pipeline is unchanged.

    Golden values for a mixed set (gt0 validation + dbl transform on column `a`,
    rows 1,2,3): the transform doubles into an appended column (all 3 rows survive)
    and the validation passes all 3 rows. These are the pre-refactor emissions the
    flattening adapter must reproduce."""
    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, cols=("a",))
    col_id = _val_col(db, source_id, "a")
    val_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    tfm_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_mixed_set(db, source_id, col_id, val_path, tfm_path)

    steps = run_pipeline(db, source_id, "all")["steps"]
    # Keyed by name ON PURPOSE: per-member emission ORDER may interleave (a set behaves
    # as if its members were placed individually; #13 reconciliation note), so a mixed
    # set is no longer grouped transforms-then-validations. This asserts the real
    # invariant — each result's CONTENT and the transform/validation SPLIT into distinct
    # result types — NOT list order. Do not re-add an order assertion.
    by_name = {e["function_name"]: e for e in steps}
    assert set(by_name) == {"gt0", "dbl"}

    # The transform/validation split: the transform member yields a transform result
    # (+ row count), the validation member a validation result (+ pass/fail) — distinct.
    tfm = by_name["dbl"]
    assert tfm["function_type"] == "transform"
    assert tfm["status"] == "ok"
    assert tfm["rows_affected"] == 3
    assert tfm["rows_passed"] is None and tfm["rows_failed"] is None

    val = by_name["gt0"]
    assert val["function_type"] == "validation"
    assert val["status"] == "ok"
    assert val["rows_passed"] == 3
    assert val["rows_failed"] == 0

    # The flattened transform still chains into the staging frame (appended column).
    staging = _list_staging_tables(db, source_id)
    assert len(staging) == 1
    final = db.execute(f'SELECT * FROM "{staging[0]}"').df()
    assert any(c.startswith("dbl") for c in final.columns)


@pytest.mark.unit
def test_adapter_dispatches_member_by_step_type_not_hardcoded_function(db, tmp_path, monkeypatch):
    """#14 idx2 (reframed for the typed-carrier contract): the adapter builds EACH
    member through ``StepContext.from_function`` and dispatches it BY the member
    context's ``step_type`` resolved through the StepExecutor REGISTRY — never by a
    hardcoded ``FunctionStepExecutor().execute(...)`` call.

    Members are now ``FunctionSpec`` (function members); a built-in member is not yet
    type-expressible — storing built-ins in a set is #275, which will widen the member
    type to a union and re-add a genuinely heterogeneous member list here. Until then
    the falsifiable guarantee against "hardcoded to function" is exercised two ways at
    once, both of which break if the dispatch is hardcoded:
      (1) each member context is built via the ``from_function`` factory (spied), and
      (2) dispatch goes through ``STEP_EXECUTORS[member_ctx.step_type]`` — proven by
          replacing the registry's FUNCTION slot with a recording executor: a
          hardcoded direct call would bypass the patched registry and the recorder
          would never fire (and the spy count would not match the member count)."""
    from pipeui.backend.domain.runner import executors as ex_mod
    from pipeui.backend.data.runner import steps as step_mod
    from pipeui.backend.data.runner.steps import FUNCTION, FunctionSpec, StepContext
    from pipeui.backend.domain.runner.executors import FunctionSetExecutor, StepExecResult, StepRunEnv

    seen = {"types": []}

    class _RecordingExecutor:
        def execute(self, ctx, working, env):
            seen["types"].append(ctx.step_type)
            return StepExecResult(working=working, entries=[{"member_type": ctx.step_type}])

    def _member(name):
        return FunctionSpec(
            function_id=name, function_name=name, function_type="transform",
            function_class="pd.series", function_return_type="pd.Series",
            module_path="/tmp/x.py", params=(), output_mode=None, append_name=None,
            output_targets=(), step_type=FUNCTION,
        )

    # A two-member function set. Each member is a FunctionSpec; the adapter must build
    # a per-member FunctionStepContext via from_function and dispatch it FUNCTION.
    set_ctx = StepContext.from_set({
        "source_function_map_id": "sfm", "set_id": "s", "set_name": "two",
        "position": 0, "output_mode": None, "append_name": None, "output_targets": (),
        "functions": [_member("m1"), _member("m2")],
    })
    env = StepRunEnv(conn=db, source_id=uuid.uuid4(),
                     original_df=pd.DataFrame({"a": [1]}), ts=0,
                     want_transforms=True, want_validations=True, run_transforms=None)

    # Spy on the factory: the adapter must build every member context through it.
    calls = {"n": 0}
    real_from_function = step_mod.StepContext.from_function.__func__

    def _spy_from_function(cls, step):
        calls["n"] += 1
        return real_from_function(cls, step)

    monkeypatch.setattr(step_mod.StepContext, "from_function",
                        classmethod(_spy_from_function))

    patched = dict(ex_mod.STEP_EXECUTORS)
    patched[FUNCTION] = _RecordingExecutor()
    with patch.object(ex_mod, "STEP_EXECUTORS", patched):
        outcome = FunctionSetExecutor().execute(set_ctx, pd.DataFrame({"a": [1]}), env)

    # (1) each member context built via from_function (one per member).
    assert calls["n"] == 2
    # (2) each member dispatched FUNCTION through the patched registry — a hardcoded
    #     FunctionStepExecutor call would bypass this recorder and leave it empty.
    assert seen["types"] == [FUNCTION, FUNCTION]
    assert [e["member_type"] for e in outcome.entries] == [FUNCTION, FUNCTION]


def test_adapter_dispatches_non_function_member_to_its_executor(db, monkeypatch):
    """Slice 4 AC3 (heterogeneous-member readiness): a member whose per-member context
    resolves to a NON-function ``step_type`` is dispatched to THAT type's executor via
    ``STEP_EXECUTORS`` — never to the function executor. The real built-in-in-a-set
    storage path is #275; this proves the dispatch *generality* now by making one
    member's per-member context a ``BuiltinStepContext`` and asserting it lands on the
    BUILTIN registry slot.

    Falsifiable: the recorders key on the *registry slot that fired* (not the context's
    own step_type), so a function-hardcoded dispatch would fire FUNCTION_SLOT and the
    assertion would read ['FUNCTION_SLOT']."""
    from pipeui.backend.domain.runner import executors as ex_mod
    from pipeui.backend.data.runner.steps import BUILTIN, FUNCTION, FunctionSpec, StepContext
    from pipeui.backend.domain.runner.executors import FunctionSetExecutor, StepExecResult, StepRunEnv

    seen = {"slots": []}

    class _RecordingExecutor:
        def __init__(self, slot):
            self.slot = slot

        def execute(self, ctx, working, env):
            seen["slots"].append(self.slot)
            return StepExecResult(working=working, entries=[{"slot": self.slot}])

    member = FunctionSpec(
        function_id="m1", function_name="m1", function_type="transform",
        function_class="pd.series", function_return_type="pd.Series",
        module_path="/tmp/x.py", params=(), output_mode=None, append_name=None,
        output_targets=(), step_type=FUNCTION,
    )
    set_ctx = StepContext.from_set({
        "source_function_map_id": "sfm", "set_id": "s", "set_name": "het",
        "position": 0, "output_mode": None, "append_name": None, "output_targets": (),
        "functions": [member],
    })
    env = StepRunEnv(conn=db, source_id=uuid.uuid4(),
                     original_df=pd.DataFrame({"a": [1]}), ts=0,
                     want_transforms=True, want_validations=True, run_transforms=None)

    # Simulate a genuinely heterogeneous member: its per-member context is a BUILTIN
    # step (the #275 storage path will produce this for real). step_type drives dispatch.
    builtin_ctx = StepContext.from_builtin({
        "step_id": "b1", "builtin_type": "filter", "builtin_config": {}, "position": 0,
    })
    monkeypatch.setattr(FunctionSetExecutor, "_member_context",
                        staticmethod(lambda set_ctx, member: builtin_ctx))

    patched = dict(ex_mod.STEP_EXECUTORS)
    patched[FUNCTION] = _RecordingExecutor("FUNCTION_SLOT")
    patched[BUILTIN] = _RecordingExecutor("BUILTIN_SLOT")
    with patch.object(ex_mod, "STEP_EXECUTORS", patched):
        FunctionSetExecutor().execute(set_ctx, pd.DataFrame({"a": [1]}), env)

    # Routed to the BUILTIN slot BY member_ctx.step_type — a function-hardcoded
    # dispatch would have fired FUNCTION_SLOT instead.
    assert seen["slots"] == ["BUILTIN_SLOT"]


@pytest.mark.integration
def test_adapter_builds_function_members_via_from_function_factory(db, tmp_path, monkeypatch):
    """The function-set adapter builds each function member's context through the
    ``StepContext.from_function`` factory (the convergence-model per-member factory),
    not a bare ``StepContext(...)`` constructor — AND the run's results are unchanged.

    Spy on ``StepContext.from_function`` to record calls during a real ``run_pipeline``
    over a two-member function set (validation gt0 + transform dbl on column `a`). The
    factory must be invoked for the function member(s); the result entries must match
    the golden set behavior member-for-member."""
    import pipeui.backend.data.runner.steps as ctx_mod

    source_id, _ = _register_multicol_source_and_ingest(db, tmp_path, name="ffac", cols=("a",))
    col_id = _val_col(db, source_id, "a")
    val_path = _write_fn_file(tmp_path, "gt0", "return data > 0")
    tfm_path = _write_fn_file(tmp_path, "dbl", "return data * 2")
    _seed_mixed_set(db, source_id, col_id, val_path, tfm_path)

    calls = {"n": 0}
    real_from_function = ctx_mod.StepContext.from_function.__func__

    def _spy_from_function(cls, step):
        calls["n"] += 1
        return real_from_function(cls, step)

    monkeypatch.setattr(
        ctx_mod.StepContext, "from_function", classmethod(_spy_from_function)
    )

    steps = run_pipeline(db, source_id, "all")["steps"]

    # The adapter routed each function member through from_function (one per member).
    assert calls["n"] >= 2

    # Results unchanged: each member's content matches the golden mixed-set behavior.
    by_name = {e["function_name"]: e for e in steps}
    assert set(by_name) == {"gt0", "dbl"}

    tfm = by_name["dbl"]
    assert tfm["function_type"] == "transform"
    assert tfm["status"] == "ok"
    assert tfm["rows_affected"] == 3

    val = by_name["gt0"]
    assert val["function_type"] == "validation"
    assert val["status"] == "ok"
    assert val["rows_passed"] == 3
    assert val["rows_failed"] == 0
