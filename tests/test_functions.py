"""Tests for function discovery, classification, and registration — §10, §11, §13."""
from __future__ import annotations

import textwrap
import uuid
from pathlib import Path

import pytest

from pipeui.workflow.functions import (
    derive_function_class,
    derive_function_return_type,
    derive_function_type,
    discover_functions_in_file,
    get_function,
    list_functions,
    scan_functions,
    _inspect_function,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_py(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return p


# ---------------------------------------------------------------------------
# Unit tests — §11 full derivation table
# ---------------------------------------------------------------------------


class TestDeriveClass:
    """function_class is driven by the least-granular param (§11 / CONTEXT.md)."""

    @pytest.mark.unit
    def test_all_scalar_int_float_bool(self):
        assert derive_function_class(["int", "float", "bool"]) == "scalar"

    @pytest.mark.unit
    def test_str_alone_is_scalar(self):
        # str unaliased → scalar at scan time (column_backed resolved at attach)
        assert derive_function_class(["str"]) == "scalar"

    @pytest.mark.unit
    def test_mixed_scalar_str(self):
        assert derive_function_class(["int", "str"]) == "scalar"

    @pytest.mark.unit
    def test_pd_series(self):
        assert derive_function_class(["pd.Series"]) == "pd.Series"

    @pytest.mark.unit
    def test_pd_series_bool(self):
        assert derive_function_class(["pd.Series[bool]"]) == "pd.Series"

    @pytest.mark.unit
    def test_pd_series_mixed_with_scalar(self):
        assert derive_function_class(["int", "pd.Series"]) == "pd.Series"

    @pytest.mark.unit
    def test_pd_dataframe(self):
        assert derive_function_class(["pd.DataFrame"]) == "pd.dataframe"

    @pytest.mark.unit
    def test_pd_dataframe_beats_series(self):
        assert derive_function_class(["pd.Series", "pd.DataFrame"]) == "pd.dataframe"

    @pytest.mark.unit
    def test_pd_dataframe_beats_scalar(self):
        assert derive_function_class(["int", "str", "pd.DataFrame"]) == "pd.dataframe"


class TestDeriveReturnType:
    """function_return_type vocabulary matches CONTEXT.md table."""

    @pytest.mark.unit
    def test_int_returns_scalar(self):
        assert derive_function_return_type("int") == "scalar"

    @pytest.mark.unit
    def test_float_returns_scalar(self):
        assert derive_function_return_type("float") == "scalar"

    @pytest.mark.unit
    def test_str_returns_scalar(self):
        assert derive_function_return_type("str") == "scalar"

    @pytest.mark.unit
    def test_bool_returns_boolean(self):
        assert derive_function_return_type("bool") == "boolean"

    @pytest.mark.unit
    def test_pd_series_returns_pd_series(self):
        assert derive_function_return_type("pd.Series") == "pd.Series"

    @pytest.mark.unit
    def test_pd_series_bool_returns_pd_series_bool(self):
        assert derive_function_return_type("pd.Series[bool]") == "pd.Series[bool]"

    @pytest.mark.unit
    def test_pd_dataframe_returns_pd_dataframe(self):
        assert derive_function_return_type("pd.DataFrame") == "pd.DataFrame"


class TestDeriveFunctionType:
    """function_type: validation iff return is boolean or pd.Series[bool] (§11 / CONTEXT.md)."""

    @pytest.mark.unit
    def test_scalar_return_is_transform(self):
        assert derive_function_type("scalar") == "transform"

    @pytest.mark.unit
    def test_boolean_return_is_validation(self):
        assert derive_function_type("boolean") == "validation"

    @pytest.mark.unit
    def test_pd_series_return_is_transform(self):
        assert derive_function_type("pd.Series") == "transform"

    @pytest.mark.unit
    def test_pd_series_bool_return_is_validation(self):
        assert derive_function_type("pd.Series[bool]") == "validation"

    @pytest.mark.unit
    def test_pd_dataframe_return_is_transform(self):
        assert derive_function_type("pd.DataFrame") == "transform"


class TestInspectFunction:
    """Eligibility checks and skip reasons for individual functions."""

    @pytest.mark.unit
    def test_skip_no_params(self):
        def f() -> int: ...
        reason = _inspect_function("f", f)
        assert isinstance(reason, str)
        assert "at least one parameter" in reason

    @pytest.mark.unit
    def test_skip_args(self):
        def f(*args: int) -> int: ...
        reason = _inspect_function("f", f)
        assert isinstance(reason, str)
        assert "variadic" in reason

    @pytest.mark.unit
    def test_skip_kwargs(self):
        def f(**kwargs: int) -> int: ...
        reason = _inspect_function("f", f)
        assert isinstance(reason, str)
        assert "variadic" in reason

    @pytest.mark.unit
    def test_skip_missing_return_annotation(self):
        def f(x: int): ...
        reason = _inspect_function("f", f)
        assert isinstance(reason, str)
        assert "return annotation" in reason

    @pytest.mark.unit
    def test_skip_untyped_parameter(self):
        def f(x) -> int: ...
        reason = _inspect_function("f", f)
        assert isinstance(reason, str)
        assert "untyped parameter" in reason
        assert "`x`" in reason

    @pytest.mark.unit
    def test_eligible_scalar(self):
        def f(x: int) -> int: ...
        result = _inspect_function("f", f)
        assert isinstance(result, dict)
        assert result["function_class"] == "scalar"
        assert result["function_return_type"] == "scalar"
        assert result["function_type"] == "transform"

    @pytest.mark.unit
    def test_eligible_validation_bool_return(self):
        def f(x: int) -> bool: ...
        result = _inspect_function("f", f)
        assert isinstance(result, dict)
        assert result["function_type"] == "validation"
        assert result["function_return_type"] == "boolean"

    @pytest.mark.unit
    def test_eligible_pd_series(self):
        import pandas as pd
        def f(col: pd.Series) -> pd.Series: ...
        result = _inspect_function("f", f)
        assert isinstance(result, dict)
        assert result["function_class"] == "pd.Series"
        assert result["function_return_type"] == "pd.Series"

    @pytest.mark.unit
    def test_eligible_pd_dataframe(self):
        import pandas as pd
        def f(df: pd.DataFrame) -> pd.DataFrame: ...
        result = _inspect_function("f", f)
        assert isinstance(result, dict)
        assert result["function_class"] == "pd.dataframe"


# ---------------------------------------------------------------------------
# Integration tests — real DuckDB sandbox
# ---------------------------------------------------------------------------


class TestScanFunctions:
    """scan_functions() writes correct rows and produces correct scan log (§10, §11, §13)."""

    @pytest.mark.integration
    def test_scan_adds_new_function(self, db, tmp_path):
        """Guarantee: eligible function in functions_paths is written to function_registry."""
        py = write_py(tmp_path, "mod.py", """
            def add(x: int, y: int) -> int:
                \"\"\"Add two integers.\"\"\"
                return x + y
        """)
        log = scan_functions(db, [str(tmp_path)])
        # One entry, status "added"
        entry = next((e for e in log if e["function_name"] == "add"), None)
        assert entry is not None, f"'add' not in log: {log}"
        assert entry["status"] == "added"
        row = db.execute("SELECT function_name FROM function_registry WHERE function_name = 'add'").fetchone()
        assert row is not None

    @pytest.mark.integration
    def test_scan_adds_parameter_rows(self, db, tmp_path):
        """Guarantee: parameter rows are written for each param (§10 transaction includes parameters)."""
        write_py(tmp_path, "mod.py", """
            def multiply(a: int, b: float) -> float:
                return a * b
        """)
        scan_functions(db, [str(tmp_path)])
        fn_id = db.execute("SELECT function_id FROM function_registry WHERE function_name = 'multiply'").fetchone()[0]
        params = db.execute("SELECT param_name, param_type FROM parameter WHERE function_id = ?", [fn_id]).fetchall()
        assert len(params) == 2
        names = {p[0] for p in params}
        assert names == {"a", "b"}

    @pytest.mark.integration
    def test_scan_reregisters_preserves_surrogate_id(self, db, tmp_path):
        """Guarantee: Principle 2 — re-scanning same function preserves surrogate function_id."""
        py = write_py(tmp_path, "mod.py", """
            def trim(col: int) -> int:
                \"\"\"First version.\"\"\"
                return col
        """)
        scan_functions(db, [str(tmp_path)])
        original_id = db.execute("SELECT function_id FROM function_registry WHERE function_name = 'trim'").fetchone()[0]

        # Update the docstring (mutable column) — name/class/return_type unchanged → same content_hash_id
        py.write_text(textwrap.dedent("""
            def trim(col: int) -> int:
                \"\"\"Updated doc.\"\"\"
                return col
        """))
        log = scan_functions(db, [str(tmp_path)])
        entry = next(e for e in log if e["function_name"] == "trim")
        assert entry["status"] == "re-registered"

        new_id_val = db.execute("SELECT function_id FROM function_registry WHERE function_name = 'trim'").fetchone()[0]
        assert new_id_val == original_id, "surrogate function_id must be preserved on re-register"

    @pytest.mark.integration
    def test_scan_updates_mutable_columns_on_reregister(self, db, tmp_path):
        """Guarantee: mutable columns (function_doc) are overwritten on re-register."""
        py = write_py(tmp_path, "mod.py", """
            def trim(col: int) -> int:
                \"\"\"Old doc.\"\"\"
                return col
        """)
        scan_functions(db, [str(tmp_path)])

        py.write_text(textwrap.dedent("""
            def trim(col: int) -> int:
                \"\"\"New doc.\"\"\"
                return col
        """))
        scan_functions(db, [str(tmp_path)])

        doc = db.execute("SELECT function_doc FROM function_registry WHERE function_name = 'trim'").fetchone()[0]
        assert doc == "New doc."

    @pytest.mark.integration
    def test_scan_skips_ineligible_function(self, db, tmp_path):
        """Guarantee: ineligible function appears in log with skipped status and reason."""
        write_py(tmp_path, "mod.py", """
            def bad_func(x):  # no type annotation
                return x
        """)
        log = scan_functions(db, [str(tmp_path)])
        entry = next((e for e in log if e["function_name"] == "bad_func"), None)
        assert entry is not None
        assert entry["status"].startswith("skipped:")
        row = db.execute("SELECT 1 FROM function_registry WHERE function_name = 'bad_func'").fetchone()
        assert row is None

    @pytest.mark.integration
    def test_scan_skips_private_functions(self, db, tmp_path):
        """Guarantee: functions whose names start with _ are silently excluded."""
        write_py(tmp_path, "mod.py", """
            def _helper(x: int) -> int:
                return x
        """)
        log = scan_functions(db, [str(tmp_path)])
        names = [e["function_name"] for e in log]
        assert "_helper" not in names

    @pytest.mark.integration
    def test_registration_atomicity_parameter_failure_rolls_back(self, db, tmp_path, monkeypatch):
        """Guarantee: §10 — if parameter write fails, function_registry row is absent (full rollback)."""
        write_py(tmp_path, "mod.py", """
            def stable(x: int) -> int:
                return x
        """)

        # Monkeypatch new_id so it raises on the second call (first: function_id, second: param_id)
        call_count = {"n": 0}
        real_new_id = __import__("pipeui.ids", fromlist=["new_id"]).new_id

        def failing_new_id():
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("injected failure for param_id")
            return real_new_id()

        monkeypatch.setattr("pipeui.ids.new_id", failing_new_id)
        monkeypatch.setattr("pipeui.workflow.functions.new_id", failing_new_id)

        log = scan_functions(db, [str(tmp_path)])
        # The function should be absent from function_registry
        row = db.execute("SELECT 1 FROM function_registry WHERE function_name = 'stable'").fetchone()
        assert row is None, "function_registry must be absent when parameter write fails (atomicity)"

    @pytest.mark.integration
    def test_scan_handles_missing_directory(self, db, tmp_path):
        """Guarantee: missing directory in functions_paths produces a skipped log entry (not a crash)."""
        missing = str(tmp_path / "does_not_exist")
        log = scan_functions(db, [missing])
        assert len(log) == 1
        assert log[0]["status"].startswith("skipped:")

    @pytest.mark.integration
    def test_list_functions_returns_all_with_parameters(self, db, tmp_path):
        """Guarantee: GET /functions returns all registered functions with parameter rows."""
        write_py(tmp_path, "mod.py", """
            def fn_a(x: int) -> int:
                \"\"\"doc a\"\"\"
                return x

            def fn_b(s: str) -> bool:
                \"\"\"doc b\"\"\"
                return bool(s)
        """)
        scan_functions(db, [str(tmp_path)])
        result = list_functions(db)
        assert len(result) == 2
        names = {r["function_name"] for r in result}
        assert names == {"fn_a", "fn_b"}
        for fn in result:
            assert "parameters" in fn
            assert len(fn["parameters"]) == 1
            assert "is_active" in fn

    @pytest.mark.integration
    def test_reregister_replaces_parameter_rows(self, db, tmp_path):
        """Guarantee: re-registration removes old parameter rows and inserts fresh ones."""
        py = write_py(tmp_path, "mod.py", """
            def fn(x: int) -> int:
                return x
        """)
        scan_functions(db, [str(tmp_path)])
        fn_id = db.execute("SELECT function_id FROM function_registry WHERE function_name = 'fn'").fetchone()[0]
        count_before = db.execute("SELECT count(*) FROM parameter WHERE function_id = ?", [fn_id]).fetchone()[0]
        assert count_before == 1

        # Re-scan identical file
        scan_functions(db, [str(tmp_path)])
        count_after = db.execute("SELECT count(*) FROM parameter WHERE function_id = ?", [fn_id]).fetchone()[0]
        assert count_after == 1, "re-registration must not accumulate duplicate parameter rows"

    @pytest.mark.integration
    def test_derived_fields_stored_correctly(self, db, tmp_path):
        """Guarantee: function_class, function_type, function_return_type are derived correctly and stored."""
        write_py(tmp_path, "mod.py", """
            import pandas as pd

            def validate_series(col: pd.Series) -> pd.Series:
                \"\"\"Transform a series.\"\"\"
                return col
        """)
        scan_functions(db, [str(tmp_path)])
        row = db.execute(
            "SELECT function_class, function_type, function_return_type FROM function_registry WHERE function_name = 'validate_series'"
        ).fetchone()
        assert row is not None
        assert row[0] == "pd.Series"
        assert row[1] == "transform"
        assert row[2] == "pd.Series"


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def fn_client(tmp_path, monkeypatch):
    """TestClient with isolated DB and config so tests don't touch real state."""
    monkeypatch.chdir(tmp_path)

    import importlib
    import pipeui.api.settings as settings_mod
    importlib.reload(settings_mod)

    import duckdb
    from fastapi import FastAPI
    from pipeui.duckdb import create_schema
    import pipeui.api.functions as fn_mod
    importlib.reload(fn_mod)

    mem_conn = duckdb.connect(":memory:")
    create_schema(mem_conn)

    app = FastAPI()

    # Override get_conn to use the in-memory DB
    def override_conn():
        yield mem_conn

    fn_mod.router.dependency_overrides = {}
    app.include_router(fn_mod.router)
    app.dependency_overrides[fn_mod.get_conn] = override_conn

    from fastapi.testclient import TestClient
    return TestClient(app), mem_conn, tmp_path


class TestGetFunctions:
    @pytest.mark.integration
    def test_returns_empty_list_when_no_functions(self, fn_client):
        """Guarantee: GET /functions returns [] when registry is empty."""
        client, conn, _ = fn_client
        res = client.get("/functions")
        assert res.status_code == 200
        assert res.json() == []

    @pytest.mark.integration
    def test_returns_registered_functions_with_parameters(self, fn_client, tmp_path):
        """Guarantee: GET /functions returns all functions with is_active and parameters."""
        client, conn, _ = fn_client
        py_dir = tmp_path / "fns"
        py_dir.mkdir()
        (py_dir / "mod.py").write_text(textwrap.dedent("""
            def greet(name: str) -> str:
                \"\"\"Return a greeting.\"\"\"
                return name
        """))
        scan_functions(conn, [str(py_dir)])
        res = client.get("/functions")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        fn = data[0]
        assert fn["function_name"] == "greet"
        assert fn["is_active"] is True
        assert len(fn["parameters"]) == 1
        assert fn["parameters"][0]["param_name"] == "name"

    @pytest.mark.integration
    def test_ordered_by_function_name(self, fn_client, tmp_path):
        """Guarantee: GET /functions returns rows ordered by function_name."""
        client, conn, _ = fn_client
        py_dir = tmp_path / "fns"
        py_dir.mkdir()
        (py_dir / "mod.py").write_text(textwrap.dedent("""
            def zebra(x: int) -> int: return x
            def alpha(x: int) -> int: return x
        """))
        scan_functions(conn, [str(py_dir)])
        res = client.get("/functions")
        names = [f["function_name"] for f in res.json()]
        assert names == sorted(names)


class TestPostScan:
    @pytest.mark.integration
    def test_scan_returns_log(self, fn_client, tmp_path, monkeypatch):
        """Guarantee: POST /functions/scan returns {"log": [...]}."""
        client, conn, _ = fn_client
        py_dir = tmp_path / "fns"
        py_dir.mkdir()
        (py_dir / "mod.py").write_text(textwrap.dedent("""
            def square(x: int) -> int:
                return x * x
        """))

        # Patch load_settings to return our tmp dir
        import pipeui.api.functions as fn_mod
        import pipeui.api.settings as sm
        from pipeui.api.settings import AppSettings

        monkeypatch.setattr(fn_mod, "scan_functions", lambda conn, paths: scan_functions(conn, [str(py_dir)]))

        res = client.post("/functions/scan")
        assert res.status_code == 200
        data = res.json()
        assert "log" in data

    @pytest.mark.integration
    def test_scan_and_list_round_trip(self, fn_client, tmp_path):
        """Guarantee: function registered via scan_functions is returned by GET /functions."""
        client, conn, _ = fn_client
        py_dir = tmp_path / "fns2"
        py_dir.mkdir()
        (py_dir / "mod.py").write_text(textwrap.dedent("""
            def process(x: int) -> bool:
                return bool(x)
        """))
        scan_functions(conn, [str(py_dir)])
        res = client.get("/functions")
        assert any(f["function_name"] == "process" for f in res.json())


# ---------------------------------------------------------------------------
# Inactive function tests
# ---------------------------------------------------------------------------


class TestInactiveFunctions:
    """scan_functions() marks is_active=False when a file disappears, restores on reappearance."""

    @pytest.mark.integration
    def test_missing_file_sets_is_active_false(self, db, tmp_path):
        """Guarantee: after rescan where module_path file no longer exists, is_active becomes False."""
        py = write_py(tmp_path, "mod.py", """
            def fn(x: int) -> int:
                return x
        """)
        scan_functions(db, [str(tmp_path)])
        # Confirm registered and active
        row = db.execute("SELECT is_active FROM function_registry WHERE function_name = 'fn'").fetchone()
        assert row is not None and row[0] is True

        # Remove the file, rescan
        py.unlink()
        log = scan_functions(db, [str(tmp_path)])

        row = db.execute("SELECT is_active FROM function_registry WHERE function_name = 'fn'").fetchone()
        assert row is not None, "row must still exist — never deleted"
        assert row[0] is False, "is_active must be False when file is gone"

        # Scan log must include a file_missing entry
        missing = [e for e in log if e["status"] == "file_missing"]
        assert len(missing) == 1
        assert missing[0]["function_name"] == "fn"
        assert "mod.py" in missing[0]["file"]

    @pytest.mark.integration
    def test_surrogate_id_preserved_after_inactivation(self, db, tmp_path):
        """Guarantee: function_id surrogate is unchanged when is_active flips to False."""
        py = write_py(tmp_path, "mod.py", """
            def fn(x: int) -> int:
                return x
        """)
        scan_functions(db, [str(tmp_path)])
        original_id = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'fn'"
        ).fetchone()[0]

        py.unlink()
        scan_functions(db, [str(tmp_path)])

        new_id_val = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'fn'"
        ).fetchone()[0]
        assert new_id_val == original_id, "surrogate function_id must be preserved after inactivation"

    @pytest.mark.integration
    def test_reappearing_file_restores_is_active_true(self, db, tmp_path):
        """Guarantee: rescan after file reappears sets is_active=True, preserving surrogate function_id."""
        py = write_py(tmp_path, "mod.py", """
            def fn(x: int) -> int:
                return x
        """)
        scan_functions(db, [str(tmp_path)])
        original_id = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'fn'"
        ).fetchone()[0]

        # Remove, rescan (now inactive)
        py.unlink()
        scan_functions(db, [str(tmp_path)])
        is_active = db.execute(
            "SELECT is_active FROM function_registry WHERE function_name = 'fn'"
        ).fetchone()[0]
        assert is_active is False

        # Restore file, rescan — should become active again
        write_py(tmp_path, "mod.py", """
            def fn(x: int) -> int:
                return x
        """)
        log = scan_functions(db, [str(tmp_path)])
        entry = next((e for e in log if e["function_name"] == "fn"), None)
        assert entry is not None
        assert entry["status"] == "re-registered"

        row = db.execute(
            "SELECT function_id, is_active FROM function_registry WHERE function_name = 'fn'"
        ).fetchone()
        assert row[1] is True, "is_active must be restored to True when file reappears"
        assert row[0] == original_id, "surrogate function_id must be preserved across is_active flips"

    @pytest.mark.integration
    def test_missing_file_does_not_delete_parameter_rows(self, db, tmp_path):
        """Guarantee: parameter rows survive is_active=False (surrogate intact, map rows preserved)."""
        py = write_py(tmp_path, "mod.py", """
            def fn(a: int, b: str) -> int:
                return a
        """)
        scan_functions(db, [str(tmp_path)])
        fn_id = db.execute(
            "SELECT function_id FROM function_registry WHERE function_name = 'fn'"
        ).fetchone()[0]
        count_before = db.execute(
            "SELECT count(*) FROM parameter WHERE function_id = ?", [fn_id]
        ).fetchone()[0]
        assert count_before == 2

        py.unlink()
        scan_functions(db, [str(tmp_path)])

        count_after = db.execute(
            "SELECT count(*) FROM parameter WHERE function_id = ?", [fn_id]
        ).fetchone()[0]
        assert count_after == 2, "parameter rows must not be removed when file goes missing"

    @pytest.mark.integration
    def test_only_functions_in_scanned_dirs_are_inactivated(self, db, tmp_path):
        """Guarantee: functions from un-scanned directories are not affected by a partial scan."""
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        write_py(dir_a, "a.py", "def fn_a(x: int) -> int: return x")
        write_py(dir_b, "b.py", "def fn_b(x: int) -> int: return x")

        # Register both
        scan_functions(db, [str(dir_a), str(dir_b)])

        # Remove fn_a's file, but only scan dir_a on next scan
        (dir_a / "a.py").unlink()
        scan_functions(db, [str(dir_a)])

        fn_a_active = db.execute(
            "SELECT is_active FROM function_registry WHERE function_name = 'fn_a'"
        ).fetchone()[0]
        fn_b_active = db.execute(
            "SELECT is_active FROM function_registry WHERE function_name = 'fn_b'"
        ).fetchone()[0]
        assert fn_a_active is False, "fn_a must be inactive — its file disappeared from scanned dir"
        assert fn_b_active is True, "fn_b must remain active — its directory was not scanned"

    @pytest.mark.integration
    def test_file_missing_entries_appear_in_scan_log(self, db, tmp_path):
        """Guarantee: scan log includes file_missing entries with correct file and function_name."""
        py1 = write_py(tmp_path, "mod.py", """
            def fn1(x: int) -> int: return x
            def fn2(x: int) -> int: return x
        """)
        scan_functions(db, [str(tmp_path)])

        py1.unlink()
        log = scan_functions(db, [str(tmp_path)])

        missing = [e for e in log if e["status"] == "file_missing"]
        assert len(missing) == 2
        names = {e["function_name"] for e in missing}
        assert names == {"fn1", "fn2"}
        for e in missing:
            assert "mod.py" in e["file"]
