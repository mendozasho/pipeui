"""Tests for helpers.py — behavioral guarantees per §13."""
from __future__ import annotations

import csv
import uuid
from unittest.mock import patch

import pytest

import datetime

from pipeui.helpers import infer_pattern
from pipeui.backend.data.base.ids import content_hash_id as _ch
from pipeui.workflow.create import create_source, update_source


def make_csv(tmp_path, name, columns, rows):
    """Write a minimal CSV to tmp_path/name. columns is list of names, rows is list of lists."""
    p = tmp_path / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return str(p)


# ---------------------------------------------------------------------------
# Unit tests — no DB
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_infer_pattern_extracts_digit_pattern():
    """filename with digits produces a pattern containing \\d+."""
    result = infer_pattern("sales_2024.07.08_bar.csv")
    assert result is not None
    assert r"\d+" in result


@pytest.mark.unit
def test_infer_pattern_returns_none_for_no_digits():
    """filename with no digits returns None."""
    assert infer_pattern("employees.csv") is None


# ---------------------------------------------------------------------------
# Integration tests — real DuckDB
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_source_create_registers_source_row(db, tmp_path):
    """source-create writes source_registry row with correct source_name and ingestion_method."""
    path = make_csv(tmp_path, "report.csv", ["id", "value"], [[1, "a"], [2, "b"]])
    source_id, failed = create_source(db, path, "my_report", "id", "upsert")

    assert not failed.has_failures()
    row = db.execute(
        "SELECT source_name, ingestion_method FROM source_registry WHERE source_id = ?",
        [source_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "my_report"
    assert row[1] == "upsert"


@pytest.mark.integration
def test_source_create_registers_column_rows(db, tmp_path):
    """§6: a column_registry row is written for each column in the file."""
    path = make_csv(
        tmp_path,
        "report.csv",
        ["id", "name", "score"],
        [[1, "alice", 10], [2, "bob", 20]],
    )
    source_id, failed = create_source(db, path, "col_test", "id")

    assert not failed.has_failures()
    names = {
        r[0]
        for r in db.execute("SELECT column_name FROM column_registry").fetchall()
    }
    assert {"id", "name", "score"} == names


@pytest.mark.integration
def test_source_create_writes_source_column_map(db, tmp_path):
    """§6: a source_column_map row is written for each column, linking source to column."""
    path = make_csv(
        tmp_path,
        "report.csv",
        ["id", "name", "score"],
        [[1, "alice", 10]],
    )
    source_id, failed = create_source(db, path, "map_test", "id")

    assert not failed.has_failures()
    count = db.execute(
        "SELECT count(*) FROM source_column_map WHERE source_id = ?", [source_id]
    ).fetchone()[0]
    assert count == 3


@pytest.mark.integration
def test_source_create_atomicity_on_duplicate_source_name(db, tmp_path):
    """§6 headline guarantee: second create with same name collides on content_hash_id; DB has exactly 1 row."""
    path = make_csv(tmp_path, "report.csv", ["id", "val"], [[1, "x"]])
    create_source(db, path, "dup_source", "id", "upsert")

    path2 = make_csv(tmp_path, "report2.csv", ["id", "val"], [[2, "y"]])
    _, failed2 = create_source(db, path2, "dup_source", "id", "upsert")

    assert failed2.has_failures()
    count = db.execute("SELECT count(*) FROM source_registry").fetchone()[0]
    assert count == 1


@pytest.mark.integration
def test_source_create_atomicity_rollback_leaves_db_unchanged(db, tmp_path):
    """§3/§6 atomicity: invalid ingestion_method causes full rollback; all table counts unchanged."""
    path = make_csv(tmp_path, "report.csv", ["id", "val"], [[1, "x"]])

    before = {
        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("source_registry", "column_registry", "source_column_map")
    }

    _, failed = create_source(db, path, "bad_method_source", "id", "bad")

    assert failed.has_failures()
    after = {
        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("source_registry", "column_registry", "source_column_map")
    }
    assert before == after


@pytest.mark.integration
def test_source_create_returns_source_id_on_success(db, tmp_path):
    """§6: returned source_id is a non-None UUID matching the row in source_registry."""
    path = make_csv(tmp_path, "report.csv", ["id", "val"], [[1, "x"]])
    source_id, failed = create_source(db, path, "id_test", "id")

    assert not failed.has_failures()
    assert isinstance(source_id, uuid.UUID)
    row = db.execute(
        "SELECT source_id FROM source_registry WHERE source_id = ?", [source_id]
    ).fetchone()
    assert row is not None
    assert uuid.UUID(str(row[0])) == source_id


@pytest.mark.integration
def test_source_create_column_type_inference(db, tmp_path):
    """§6 step 1.3: DuckDB-native type inference registers correct column_type values."""
    path = make_csv(
        tmp_path,
        "typed.csv",
        ["id", "label"],
        [[1, "hello"], [2, "world"]],
    )
    source_id, failed = create_source(db, path, "typed_source", "id")

    assert not failed.has_failures()
    types = {
        r[0]: r[1]
        for r in db.execute("SELECT column_name, column_type FROM column_registry").fetchall()
    }
    # DuckDB infers numeric id as a recognized int type
    assert types["id"] in ("INTEGER", "BIGINT", "INT", "SMALLINT", "TINYINT", "HUGEINT")
    # label is a string
    assert types["label"] in ("VARCHAR", "TEXT")


@pytest.mark.integration
def test_source_create_var_fallback(db, tmp_path):
    """§6 step 1.3: unrecognized DuckDB type falls back to 'VARCHAR'."""
    path = make_csv(tmp_path, "report.csv", ["id"], [[1]])

    # Patch infer_column_types to simulate an unrecognized type coming back from DuckDB
    with patch("pipeui.workflow.create.infer_column_types", return_value=[("id", "UNRECOGNIZED")]):
        source_id, failed = create_source(db, path, "var_source", "id")

    assert not failed.has_failures()
    col_type = db.execute(
        "SELECT column_type FROM column_registry WHERE column_name = 'id'"
    ).fetchone()[0]
    assert col_type == "VARCHAR"


def _insert_source(conn, source_name: str, primary_key: str = "id") -> uuid.UUID:
    """Insert a bare source_registry row for collision/update tests."""
    sid = uuid.uuid4()
    ch = _ch("source_registry", source_name, primary_key, "upsert")
    conn.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [sid, ch, source_name, datetime.date.today(), "upsert", primary_key],
    )
    return sid


@pytest.mark.integration
def test_update_source_collision_routes_to_failed(db):
    """§1 edit-collision rule: renaming a source onto an existing name surfaces as FailedRegistryEntry."""
    # Insert two sources directly to avoid the column_registry UNIQUE issue in create_source
    source_id_a = _insert_source(db, "alpha")
    source_id_b = _insert_source(db, "beta")

    # Rename beta → alpha; same (source_name, primary_key, ingestion_method) → hash collision
    result_id, failed = update_source(db, source_id_b, source_name="alpha")

    assert result_id is None
    assert failed.has_failures()
    _, reason = failed.failures[0]
    assert "collision" in reason

    # beta row must remain unchanged
    row = db.execute(
        "SELECT source_name FROM source_registry WHERE source_id = ?", [source_id_b]
    ).fetchone()
    assert row[0] == "beta"


@pytest.mark.integration
def test_update_source_applies_non_colliding_edit(db, tmp_path):
    """§1: a non-colliding edit commits and returns the source_id."""
    source_id = _insert_source(db, "original")

    result_id, failed = update_source(db, source_id, source_name="renamed")

    assert not failed.has_failures()
    assert result_id == source_id
    row = db.execute(
        "SELECT source_name FROM source_registry WHERE source_id = ?", [source_id]
    ).fetchone()
    assert row[0] == "renamed"
