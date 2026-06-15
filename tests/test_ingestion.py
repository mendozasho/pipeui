"""Tests for workflow/ingestion.py — behavioral guarantees per §9 / §13."""
from __future__ import annotations

import csv
import uuid

import pytest

from pipeui.workflow.create import create_source
from pipeui.workflow.ingestion import get_source_detail, get_source_rows, ingest_source
from pipeui.sql_user_table import instance_table_name
from tests.conftest import make_quirky_file


def make_csv(tmp_path, name, columns, rows):
    """Write a minimal CSV to tmp_path/name."""
    p = tmp_path / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return str(p)


def register_and_ingest(db, tmp_path, source_name, columns, rows, ingestion_method="upsert"):
    """Register a source from a CSV then ingest it. Returns (source_id, file_path)."""
    path = make_csv(tmp_path, f"{source_name}.csv", columns, rows)
    source_id, failed = create_source(db, path, source_name, columns[0], ingestion_method)
    assert not failed.has_failures(), failed
    return source_id, path


# ---------------------------------------------------------------------------
# Integration tests — real DuckDB
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_ingest_creates_instance_table_jit(db, tmp_path):
    """Instance table is created on first ingest — not at registration time (§8)."""
    source_id, path = register_and_ingest(db, tmp_path, "sales", ["id", "val"], [])
    tname = instance_table_name(source_id)

    # Before ingest the table should not exist
    tables_before = [r[0] for r in db.execute("SHOW TABLES").fetchall()]
    assert tname not in tables_before

    rows_in, skipped, failed, _diff = ingest_source(db, source_id, path)
    assert not failed.has_failures(), failed

    tables_after = [r[0] for r in db.execute("SHOW TABLES").fetchall()]
    assert tname in tables_after


@pytest.mark.integration
def test_ingest_append_writes_rows(db, tmp_path):
    """append ingestion writes all rows from the file into the instance table."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 10], ["r2", 20]])
    source_id, failed = create_source(db, path, "data", "id", "append")
    assert not failed.has_failures()

    rows_in, skipped, failed, diff = ingest_source(db, source_id, path)
    assert not failed.has_failures(), failed
    assert rows_in == 2
    assert skipped == []

    tname = instance_table_name(source_id)
    count = db.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    assert count == 2


@pytest.mark.integration
def test_ingest_upsert_overwrites_existing_rows(db, tmp_path):
    """upsert replaces a row when the PK already exists in the instance table. §9"""
    path1 = make_csv(tmp_path, "v1.csv", ["id", "val"], [["r1", 10], ["r2", 20]])
    source_id, failed = create_source(db, path1, "report", "id", "upsert")
    assert not failed.has_failures()

    ingest_source(db, source_id, path1)

    # Re-ingest with updated value for r1
    path2 = make_csv(tmp_path, "v2.csv", ["id", "val"], [["r1", 99]])
    rows_in, skipped, failed, _diff = ingest_source(db, source_id, path2)
    assert not failed.has_failures(), failed

    tname = instance_table_name(source_id)
    val = db.execute(f'SELECT val FROM "{tname}" WHERE id = \'r1\'').fetchone()[0]
    assert val == 99
    count = db.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    assert count == 2  # r2 still present, r1 updated


@pytest.mark.integration
def test_ingest_skip_reports_dropped_pk_values(db, tmp_path):
    """skip returns the PK values of rows that were not inserted. §9 behavioral guarantee."""
    path1 = make_csv(tmp_path, "base.csv", ["id", "val"], [["r1", 10], ["r2", 20]])
    source_id, failed = create_source(db, path1, "report", "id", "skip")
    assert not failed.has_failures()
    ingest_source(db, source_id, path1)

    # Re-ingest: r1 already exists (skip), r3 is new (insert)
    path2 = make_csv(tmp_path, "update.csv", ["id", "val"], [["r1", 99], ["r3", 30]])
    rows_in, skipped, failed, _diff = ingest_source(db, source_id, path2)
    assert not failed.has_failures(), failed

    assert "r1" in skipped
    assert rows_in == 1

    tname = instance_table_name(source_id)
    # r1 must be unchanged; r3 must be inserted
    val_r1 = db.execute(f'SELECT val FROM "{tname}" WHERE id = \'r1\'').fetchone()[0]
    assert val_r1 == 10
    count = db.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    assert count == 3


@pytest.mark.integration
def test_ingest_atomicity_on_failure(db, tmp_path):
    """A failed ingest leaves the instance table at its last committed state. §9"""
    path1 = make_csv(tmp_path, "good.csv", ["id", "val"], [["r1", 10]])
    source_id, failed = create_source(db, path1, "report", "id", "append")
    assert not failed.has_failures()
    ingest_source(db, source_id, path1)

    # Duplicate id on append should fail and leave table unchanged
    path2 = make_csv(tmp_path, "dup.csv", ["id", "val"], [["r1", 99]])
    rows_in, skipped, failed, _diff = ingest_source(db, source_id, path2, ingestion_method="append")
    assert failed.has_failures()

    tname = instance_table_name(source_id)
    count = db.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    assert count == 1  # original row intact; failed ingest wrote nothing


@pytest.mark.integration
def test_ingest_idempotent_table_creation(db, tmp_path):
    """Ingesting a second file does not fail because the instance table already exists. §8"""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    ingest_source(db, source_id, path)
    # Second ingest — IF NOT EXISTS must not raise
    _, _, failed2, _diff = ingest_source(db, source_id, path)
    assert not failed2.has_failures()


@pytest.mark.integration
def test_ingest_invalid_method_returns_failure(db, tmp_path):
    """An unrecognised ingestion_method is routed to FailedRegistryEntry, not raised."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    rows_in, skipped, failed, _diff = ingest_source(db, source_id, path, ingestion_method="merge")
    assert failed.has_failures()
    assert rows_in == 0


@pytest.mark.integration
def test_get_source_detail_row_count_reflects_ingested_rows(db, tmp_path):
    """get_source_detail returns the live row_count from the instance table."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1], ["r2", 2], ["r3", 3]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    detail_before = get_source_detail(db, source_id)
    assert detail_before["row_count"] == 0

    ingest_source(db, source_id, path)
    detail_after = get_source_detail(db, source_id)
    assert detail_after["row_count"] == 3


@pytest.mark.integration
def test_get_source_detail_includes_columns(db, tmp_path):
    """get_source_detail includes the full column list from column_registry."""
    path = make_csv(tmp_path, "data.csv", ["id", "amount"], [["r1", 1.5]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    detail = get_source_detail(db, source_id)
    col_names = [c["column_name"] for c in detail["columns"]]
    assert "id" in col_names
    assert "amount" in col_names


@pytest.mark.integration
def test_get_source_detail_returns_none_for_unknown_id(db):
    """get_source_detail returns None when the source_id is not registered."""
    result = get_source_detail(db, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# get_source_rows — §9 Row preview behavioral guarantees
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_source_rows_empty_when_not_ingested(db, tmp_path):
    """get_source_rows returns [] when registered but never ingested (instance table absent)."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    result = get_source_rows(db, source_id)
    assert result == []


@pytest.mark.integration
def test_get_source_rows_returns_correct_rows_after_ingest(db, tmp_path):
    """get_source_rows returns the correct row dicts after ingestion."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 10], ["r2", 20]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)

    rows = get_source_rows(db, source_id)
    assert len(rows) == 2
    ids = {r["id"] for r in rows}
    vals = {r["val"] for r in rows}
    assert ids == {"r1", "r2"}
    assert vals == {10, 20}


@pytest.mark.integration
def test_get_source_rows_respects_limit(db, tmp_path):
    """get_source_rows never returns more rows than the limit parameter."""
    data_rows = [[f"r{i}", i] for i in range(10)]
    path = make_csv(tmp_path, "data.csv", ["id", "val"], data_rows)
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)

    rows = get_source_rows(db, source_id, limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# make_quirky_file: varchar_fallback forces VARCHAR inference
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_quirky_varchar_fallback_infers_varchar(db, tmp_path):
    """make_quirky_file varchar_fallback=True generates a column with mixed content
    (str, int, bool) that must be inferred as VARCHAR after ingestion."""
    p = make_quirky_file(tmp_path, {"varchar_fallback": True})
    source_id, failed = create_source(db, str(p), "quirky_varchar", "id", "upsert")
    assert not failed.has_failures(), str(failed)

    # The column_registry must record VARCHAR for the mixed-content column
    col_rows = db.execute(
        """
        SELECT cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        """,
        [source_id],
    ).fetchall()
    col_types = {name: ctype for name, ctype in col_rows}
    assert "varchar_col" in col_types, f"varchar_col missing; got {col_types}"
    assert col_types["varchar_col"] == "VARCHAR", (
        f"Expected VARCHAR fallback, got {col_types['varchar_col']}"
    )

    # Ingestion must succeed and rows must be present
    rows_in, skipped, failed_ing, _schema_diff = ingest_source(db, source_id, str(p))
    assert not failed_ing.has_failures(), str(failed_ing)
    assert rows_in == 3


# ---------------------------------------------------------------------------
# get_source_detail — include_functions
# ---------------------------------------------------------------------------

def _seed_function_on_source(db, source_id, fn_name, fn_type, position=0):
    """Attach a minimal function set to source_id. Returns set_id."""
    from pipeui.ids import content_hash_id as _cid
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "scalar", fn_name, None, "bool",
         "value: str", fn_type, "/dev/null", True],
    )
    param_id = uuid.uuid4()
    param_ch = uuid.uuid4()
    db.execute(
        "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
        [param_id, param_ch, "value", "str", fn_id],
    )

    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    set_name = f"set_{fn_name}"
    db.execute(
        "INSERT INTO function_set VALUES (?, ?, ?, ?)",
        [set_id, set_ch, set_name, None],
    )
    fsm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map VALUES (?, ?, ?, ?)",
        [fsm_id, set_id, fn_id, 0],
    )

    sfm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
        [sfm_id, source_id, set_id, position, "append"],
    )
    return set_id, set_name


@pytest.mark.integration
def test_get_source_detail_functions_empty_when_none_attached(db, tmp_path):
    """get_source_detail include_functions=True returns functions==[] when none attached."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    detail = get_source_detail(db, source_id, include_functions=True)
    assert detail is not None
    assert detail["functions"] == []


@pytest.mark.integration
def test_get_source_detail_functions_returns_both_in_position_order(db, tmp_path):
    """get_source_detail include_functions=True returns both functions in position order."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()

    _, set_name_a = _seed_function_on_source(db, source_id, "fn_alpha", "validation", position=0)
    _, set_name_b = _seed_function_on_source(db, source_id, "fn_beta", "transform", position=1)

    detail = get_source_detail(db, source_id, include_functions=True)
    assert detail is not None
    fns = detail["functions"]
    assert len(fns) == 2
    assert fns[0]["function_name"] == "fn_alpha"
    assert fns[0]["function_type"] == "validation"
    assert fns[0]["set_name"] == set_name_a
    assert fns[1]["function_name"] == "fn_beta"
    assert fns[1]["function_type"] == "transform"
    assert fns[1]["set_name"] == set_name_b


@pytest.mark.integration
def test_get_source_detail_no_functions_key_when_include_false(db, tmp_path):
    """get_source_detail include_functions=False does not include 'functions' key."""
    path = make_csv(tmp_path, "data.csv", ["id", "val"], [["r1", 1]])
    source_id, failed = create_source(db, path, "data", "id", "upsert")
    assert not failed.has_failures()
    _seed_function_on_source(db, source_id, "fn_alpha", "validation", position=0)

    detail = get_source_detail(db, source_id, include_functions=False)
    assert detail is not None
    assert "functions" not in detail
