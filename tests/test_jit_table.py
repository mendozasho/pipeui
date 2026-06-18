"""Tests for sql_user_table — behavioral guarantees per §8 / §13."""
from __future__ import annotations

import uuid

import pytest

from pipeui.backend.data.base.tables import build_create_table_sql, instance_table_name


# ---------------------------------------------------------------------------
# Unit tests — no DB
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_instance_table_name_has_no_dashes():
    """instance_table_name produces a valid SQL identifier (no UUID dashes)."""
    sid = uuid.uuid4()
    name = instance_table_name(sid)
    assert "-" not in name
    assert name.startswith("src_")
    assert len(name) == 4 + 32  # "src_" + 32 hex chars


@pytest.mark.unit
def test_instance_table_name_is_deterministic():
    """Same source_id always produces the same table name."""
    sid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert instance_table_name(sid) == instance_table_name(sid)


@pytest.mark.unit
def test_build_create_table_sql_contains_if_not_exists():
    """DDL is safe to call on re-ingest — uses IF NOT EXISTS (§8)."""
    sql = build_create_table_sql("t", [("id", "VARCHAR"), ("val", "INTEGER")], "id")
    assert "IF NOT EXISTS" in sql


@pytest.mark.unit
def test_build_create_table_sql_pk_is_table_level():
    """PRIMARY KEY is a table-level constraint, not inline on the column definition."""
    sql = build_create_table_sql("t", [("id", "VARCHAR"), ("val", "INTEGER")], "id")
    # Table-level constraint appears after the column list
    pk_pos = sql.index("PRIMARY KEY")
    last_col_def_pos = sql.rindex('"val"')
    assert pk_pos > last_col_def_pos


@pytest.mark.unit
def test_build_create_table_sql_all_columns_present():
    """Every column appears in the generated DDL."""
    columns = [("id", "VARCHAR"), ("amount", "DOUBLE"), ("region", "VARCHAR")]
    sql = build_create_table_sql("sales", columns, "id")
    for name, col_type in columns:
        assert f'"{name}"' in sql
        assert col_type in sql


# ---------------------------------------------------------------------------
# Integration tests — real DuckDB
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_build_create_table_sql_creates_real_table(db):
    """Generated DDL creates a table with the expected schema in DuckDB."""
    columns = [("id", "VARCHAR"), ("amount", "DOUBLE")]
    sql = build_create_table_sql("test_src", columns, "id")
    db.execute(sql)

    rows = db.execute("DESCRIBE test_src").fetchall()
    names = [r[0] for r in rows]
    assert "id" in names
    assert "amount" in names


@pytest.mark.integration
def test_build_create_table_sql_idempotent(db):
    """Calling the DDL twice on the same table does not raise (IF NOT EXISTS). §8"""
    columns = [("id", "VARCHAR"), ("val", "INTEGER")]
    sql = build_create_table_sql("test_src2", columns, "id")
    db.execute(sql)
    db.execute(sql)  # must not raise

    count = db.execute("SELECT COUNT(*) FROM test_src2").fetchone()[0]
    assert count == 0
