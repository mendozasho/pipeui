"""Tests for SQL function scanning, registration, and execution — §10, §11, §13."""
from __future__ import annotations

import textwrap
import uuid
from pathlib import Path

import pytest

from pipeui.backend.domain.functions.discovery import discover_sql_functions_in_file
from pipeui.backend.domain.functions.registration import scan_functions
from pipeui.backend.domain.functions.function_read import list_functions
from pipeui.backend.domain.runner.sql_exec import _execute_sql_function
from pipeui.backend.data.base.tables import instance_table_name
from tests.conftest import make_registered_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_sql(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return p


# ---------------------------------------------------------------------------
# Unit tests — SQL header parsing
# ---------------------------------------------------------------------------

class TestParseSqlHeader:
    """discover_sql_functions_in_file parses comment headers correctly."""

    @pytest.mark.unit
    def test_valid_transform_header(self, tmp_path):
        """Guarantee: valid transform header registers with correct fields."""
        f = write_sql(tmp_path, "clean.sql", """
            -- name: clean_nulls
            -- description: Remove rows where key columns are null
            -- type: transform
            SELECT * FROM {source_table} WHERE col_a IS NOT NULL
        """)
        results = discover_sql_functions_in_file(f)
        assert len(results) == 1
        item = results[0]
        assert "skip_reason" not in item, item.get("skip_reason")
        assert item["function_name"] == "clean_nulls"
        data = item["data"]
        assert data["function_type"] == "transform"
        assert data["function_class"] == "pd.dataframe"
        assert data["function_return_type"] == "pd.DataFrame"
        assert "{source_table}: pd.DataFrame -> pd.DataFrame" in data["function_signature"]
        assert data["function_doc"] == "Remove rows where key columns are null"
        assert data["param_names"] == []
        assert data["param_types"] == []

    @pytest.mark.unit
    def test_valid_validation_header(self, tmp_path):
        """Guarantee: validation type produces pd.Series[bool] return type."""
        f = write_sql(tmp_path, "check.sql", """
            -- name: check_not_null
            -- type: validation
            SELECT col_a IS NOT NULL FROM {source_table}
        """)
        results = discover_sql_functions_in_file(f)
        assert len(results) == 1
        item = results[0]
        assert "skip_reason" not in item
        assert item["data"]["function_type"] == "validation"
        assert item["data"]["function_return_type"] == "pd.Series[bool]"
        assert "-> pd.Series[bool]" in item["data"]["function_signature"]

    @pytest.mark.unit
    def test_missing_type_stores_unknown(self, tmp_path):
        """Guarantee: omitting -- type stores function_type='unknown'."""
        f = write_sql(tmp_path, "mystery.sql", """
            -- name: mystery_fn
            SELECT * FROM {source_table}
        """)
        results = discover_sql_functions_in_file(f)
        assert len(results) == 1
        item = results[0]
        assert "skip_reason" not in item
        assert item["data"]["function_type"] == "unknown"
        assert item["data"]["function_return_type"] == "unknown"
        assert "-> unknown" in item["data"]["function_signature"]

    @pytest.mark.unit
    def test_missing_name_header_is_skipped(self, tmp_path):
        """Guarantee: SQL file without -- name: is skipped with a reason."""
        f = write_sql(tmp_path, "noname.sql", """
            -- type: transform
            SELECT * FROM {source_table}
        """)
        results = discover_sql_functions_in_file(f)
        assert len(results) == 1
        assert "skip_reason" in results[0]
        assert "name" in results[0]["skip_reason"]

    @pytest.mark.unit
    def test_no_description_is_none(self, tmp_path):
        """Guarantee: missing -- description results in function_doc=None."""
        f = write_sql(tmp_path, "nodoc.sql", """
            -- name: fn_no_doc
            -- type: transform
            SELECT * FROM {source_table}
        """)
        results = discover_sql_functions_in_file(f)
        assert results[0]["data"]["function_doc"] is None


# ---------------------------------------------------------------------------
# Integration tests — scan registers SQL functions
# ---------------------------------------------------------------------------

class TestScanSqlFunctions:
    """scan_functions() discovers and registers .sql files alongside .py files."""

    @pytest.mark.integration
    def test_scan_registers_sql_function(self, db, tmp_path):
        """Guarantee: .sql file in functions_paths is registered in function_registry."""
        write_sql(tmp_path, "clean.sql", """
            -- name: clean_nulls
            -- type: transform
            SELECT * FROM {source_table} WHERE id IS NOT NULL
        """)
        log = scan_functions(db, [str(tmp_path)])
        entry = next((e for e in log if e["function_name"] == "clean_nulls"), None)
        assert entry is not None, f"clean_nulls not in log: {log}"
        assert entry["status"] == "added"
        row = db.execute(
            "SELECT function_class, function_type, function_return_type "
            "FROM function_registry WHERE function_name = 'clean_nulls'"
        ).fetchone()
        assert row is not None
        assert row[0] == "pd.dataframe"
        assert row[1] == "transform"
        assert row[2] == "pd.DataFrame"

    @pytest.mark.integration
    def test_scan_registers_unknown_type_sql_function(self, db, tmp_path):
        """Guarantee: SQL file without -- type stores function_type='unknown'."""
        write_sql(tmp_path, "mystery.sql", """
            -- name: mystery_fn
            SELECT * FROM {source_table}
        """)
        scan_functions(db, [str(tmp_path)])
        row = db.execute(
            "SELECT function_type FROM function_registry WHERE function_name = 'mystery_fn'"
        ).fetchone()
        assert row is not None
        assert row[0] == "unknown"

    @pytest.mark.integration
    def test_scan_sql_no_parameter_rows(self, db, tmp_path):
        """Guarantee: SQL functions have no parameter rows (they receive the full table)."""
        write_sql(tmp_path, "clean.sql", """
            -- name: sql_no_params
            -- type: transform
            SELECT * FROM {source_table}
        """)
        scan_functions(db, [str(tmp_path)])
        fn_id = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'sql_no_params'"
        ).fetchone()[0]
        count = db.execute(
            "SELECT count(*) FROM parameter WHERE function_id = ?", [fn_id]
        ).fetchone()[0]
        assert count == 0, "SQL functions must have no parameter rows"

    @pytest.mark.integration
    def test_scan_py_and_sql_together(self, db, tmp_path):
        """Guarantee: scan_functions finds both .py and .sql files in same directory."""
        (tmp_path / "myfn.py").write_text(textwrap.dedent("""
            def add(x: int, y: int) -> int:
                return x + y
        """))
        write_sql(tmp_path, "clean.sql", """
            -- name: sql_clean
            -- type: transform
            SELECT * FROM {source_table}
        """)
        scan_functions(db, [str(tmp_path)])
        names = {
            r[0] for r in db.execute(
                "SELECT function_name FROM function_registry"
            ).fetchall()
        }
        assert "add" in names
        assert "sql_clean" in names

    @pytest.mark.integration
    def test_sql_functions_appear_in_list_functions(self, db, tmp_path):
        """Guarantee: GET /functions returns SQL functions alongside Python functions."""
        write_sql(tmp_path, "clean.sql", """
            -- name: list_test_sql
            -- type: transform
            SELECT * FROM {source_table}
        """)
        scan_functions(db, [str(tmp_path)])
        result = list_functions(db)
        names = {r["function_name"] for r in result}
        assert "list_test_sql" in names

    @pytest.mark.integration
    def test_reregistration_preserves_surrogate_id(self, db, tmp_path):
        """Guarantee: Principle 2 — re-scanning same SQL function preserves surrogate function_id."""
        f = write_sql(tmp_path, "clean.sql", """
            -- name: stable_sql
            -- description: First version
            -- type: transform
            SELECT * FROM {source_table}
        """)
        scan_functions(db, [str(tmp_path)])
        original_id = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'stable_sql'"
        ).fetchone()[0]

        # Update description (mutable column) — name/class/return_type unchanged → same content_hash_id
        f.write_text(textwrap.dedent("""
            -- name: stable_sql
            -- description: Updated description
            -- type: transform
            SELECT * FROM {source_table}
        """))
        log = scan_functions(db, [str(tmp_path)])
        entry = next(e for e in log if e["function_name"] == "stable_sql")
        assert entry["status"] == "re-registered"

        new_id_val = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'stable_sql'"
        ).fetchone()[0]
        assert new_id_val == original_id, "surrogate function_id must be preserved on re-register"


# ---------------------------------------------------------------------------
# Integration tests — SQL function execution
# ---------------------------------------------------------------------------

class TestSqlFunctionExecution:
    """SQL functions execute against the source instance table via DuckDB."""

    @pytest.mark.integration
    def test_sql_transform_returns_filtered_rows(self, db, tmp_path):
        """Guarantee: SQL transform returns correct rows after substituting {source_table}."""
        source_id, _ = make_registered_source(db, n_columns=0)

        # Create instance table with some rows
        tname = instance_table_name(source_id)
        db.execute(f'CREATE TABLE "{tname}" (id INTEGER, val VARCHAR, PRIMARY KEY (id))')
        db.execute(f'INSERT INTO "{tname}" VALUES (1, \'hello\'), (2, NULL), (3, \'world\')')

        sql_file = write_sql(tmp_path, "clean.sql", """
            -- name: filter_nulls
            -- type: transform
            SELECT * FROM {source_table} WHERE val IS NOT NULL
        """)

        result = _execute_sql_function(db, str(sql_file), source_id)
        import pandas as pd
        assert isinstance(result, pd.DataFrame), f"Expected DataFrame, got {type(result)}: {result}"
        assert len(result) == 2
        assert set(result["id"].tolist()) == {1, 3}

    @pytest.mark.integration
    def test_sql_execution_returns_failed_entry_on_bad_sql(self, db, tmp_path):
        """Guarantee: invalid SQL returns FailedFunctionEntry (not an exception)."""
        from pipeui.backend.data.base.fails import FailedFunctionEntry
        source_id = uuid.uuid4()

        sql_file = write_sql(tmp_path, "bad.sql", """
            -- name: bad_fn
            -- type: transform
            SELECT * FROM this_table_does_not_exist_xyz
        """)

        result = _execute_sql_function(db, str(sql_file), source_id)
        assert isinstance(result, FailedFunctionEntry), f"Expected FailedFunctionEntry, got {result}"
        assert result.has_failures()

    @pytest.mark.integration
    def test_sql_source_table_substituted_correctly(self, db, tmp_path):
        """Guarantee: {source_table} is replaced with the actual instance table name."""
        source_id, _ = make_registered_source(db, n_columns=0)
        tname = instance_table_name(source_id)
        db.execute(f'CREATE TABLE "{tname}" (id INTEGER, score INTEGER, PRIMARY KEY (id))')
        db.execute(f'INSERT INTO "{tname}" VALUES (1, 10), (2, 20), (3, 5)')

        sql_file = write_sql(tmp_path, "high.sql", """
            -- name: high_scores
            -- type: transform
            SELECT * FROM {source_table} WHERE score > 9 ORDER BY id
        """)

        import pandas as pd
        result = _execute_sql_function(db, str(sql_file), source_id)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert list(result["id"]) == [1, 2]
