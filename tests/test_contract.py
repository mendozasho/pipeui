"""Tests for the FunctionContract carrier, the AST guardrail, and extraction (#134).

Guarantees covered (CLAUDE.md rule 10 — every documented guarantee has a test):
  - A module with a blocked construct is rejected with a line-numbered reason and its
    top-level code provably never executes (side-effect sentinel).
  - Flag findings accept the module and surface in the scan log.
  - Extraction emits the same legacy registration payload as the pre-contract scanner
    (parity corpus, including multi-``pd.Series``-parameter and mixed-shape functions).
  - The contract round-trips ``to_dict``/``from_dict``; ``from_registry_rows`` is
    derivation-faithful.
  - ``parameter.position`` persists signature order, not alphabetical order.
  - ``execution_mode`` derives from signature shape (vectorized preferred; ``row``
    only when an all-scalar signature is column-backed).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pandas as pd
import pytest

from pipeui.backend.data.functions.contract import (
    ENGINE_PYTHON,
    ENGINE_SQL,
    FunctionContract,
    ParamContract,
)
from pipeui.backend.domain.functions.discovery import (
    _inspect_function,
    extract_contracts,
)
from pipeui.backend.domain.functions.guardrails import screen_module
from pipeui.backend.domain.functions.registration import scan_functions


def write_py(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return p


# ---------------------------------------------------------------------------
# Guardrail screen — unit
# ---------------------------------------------------------------------------

class TestScreenModule:
    """screen_module blocks/flags the documented constructs with line numbers."""

    @pytest.mark.unit
    @pytest.mark.parametrize("stmt,rule", [
        ("import os", "import-os"),
        ("import subprocess", "import-subprocess"),
        ("from socket import socket", "import-socket"),
        ("import os.path", "import-os"),
        ("import shutil", "import-shutil"),
        ("import ctypes", "import-ctypes"),
        ("import pickle", "import-pickle"),
        ("import urllib.request", "import-urllib"),
    ])
    def test_blocked_imports(self, stmt, rule):
        findings = screen_module(f"{stmt}\n\ndef f(x: int) -> int:\n    return x\n")
        blocks = [f for f in findings if f.severity == "block"]
        assert len(blocks) == 1
        assert blocks[0].rule == rule
        assert blocks[0].lineno == 1

    @pytest.mark.unit
    @pytest.mark.parametrize("call", ["eval", "exec", "compile", "__import__", "open"])
    def test_blocked_calls_inside_function_body(self, call):
        # Unsafe calls are blocked anywhere, not just at module level.
        src = f"def f(x: str) -> str:\n    return {call}(x)\n"
        blocks = [f for f in screen_module(src) if f.severity == "block"]
        assert len(blocks) == 1
        assert blocks[0].rule == f"call-{call}"
        assert blocks[0].lineno == 2

    @pytest.mark.unit
    def test_blocked_dunder_attribute_access(self):
        src = "def f(x: int) -> int:\n    return x.__class__.__mro__\n"
        blocks = [f for f in screen_module(src) if f.severity == "block"]
        assert blocks and all(b.rule == "dunder-access" for b in blocks)

    @pytest.mark.unit
    def test_blocked_getattr_with_dunder_literal(self):
        src = "def f(x: int) -> int:\n    return getattr(x, '__dict__')\n"
        blocks = [f for f in screen_module(src) if f.severity == "block"]
        assert len(blocks) == 1
        assert blocks[0].rule == "call-getattr-dunder"

    @pytest.mark.unit
    def test_syntax_error_blocks(self):
        blocks = [f for f in screen_module("def f(:\n") if f.severity == "block"]
        assert len(blocks) == 1
        assert blocks[0].rule == "syntax-error"

    @pytest.mark.unit
    def test_unlisted_import_flags_not_blocks(self):
        findings = screen_module("import scipy\n\ndef f(x: int) -> int:\n    return x\n")
        assert not [f for f in findings if f.severity == "block"]
        flags = [f for f in findings if f.severity == "flag"]
        assert any(f.rule == "import-unlisted-scipy" for f in flags)

    @pytest.mark.unit
    def test_toplevel_statement_flags(self):
        findings = screen_module("print('hello')\n\ndef f(x: int) -> int:\n    return x\n")
        # print() is a benign builtin (not in the block list) but the module-level
        # statement runs at scan time, so it is flagged.
        assert not [f for f in findings if f.severity == "block"]
        assert any(f.rule == "toplevel-statement" for f in findings)

    @pytest.mark.unit
    def test_clean_module_no_findings(self):
        src = '''\
            """Docstring is fine."""
            import pandas as pd
            import numpy as np

            THRESHOLD = 10.5

            def scale(col: pd.Series, factor: float = 2.0) -> pd.Series:
                return col * factor
        '''
        assert screen_module(textwrap.dedent(src)) == []


class TestGuardrailNeverExecutesBlockedModule:
    """Guarantee: a blocked module's top-level code never runs in the app process."""

    @pytest.mark.integration
    def test_blocked_module_sentinel_not_written(self, tmp_path):
        sentinel = tmp_path / "sentinel.txt"
        write_py(tmp_path, "evil.py", f"""
            import subprocess
            from pathlib import Path

            Path({str(sentinel)!r}).write_text("ran")

            def f(x: int) -> int:
                return x
        """)
        results = extract_contracts(tmp_path / "evil.py")
        assert len(results) == 1
        assert results[0].skip_reason is not None
        assert "blocked by static screen" in results[0].skip_reason
        assert "imports 'subprocess'" in results[0].skip_reason
        assert "(line 2)" in results[0].skip_reason
        assert not sentinel.exists(), "blocked module was exec'd — guardrail failed"

    @pytest.mark.integration
    def test_scan_log_carries_block_reason(self, db, tmp_path):
        write_py(tmp_path, "evil.py", """
            import os

            def f(x: int) -> int:
                return x
        """)
        log = scan_functions(db, [str(tmp_path)])
        entry = next(e for e in log if e["file"].endswith("evil.py"))
        assert entry["function_name"] == "<module>"
        assert "blocked by static screen: imports 'os'" in entry["status"]
        # Nothing from the module was registered.
        count = db.execute("SELECT COUNT(*) FROM function_registry").fetchone()[0]
        assert count == 0

    @pytest.mark.integration
    def test_flagged_module_still_registers(self, db, tmp_path):
        # uuid is importable but outside the expected data-function allowlist.
        write_py(tmp_path, "flagged.py", """
            import uuid

            def f(x: int) -> int:
                return x
        """)
        log = scan_functions(db, [str(tmp_path)])
        assert any(e["status"].startswith("flagged: imports 'uuid'") for e in log)
        assert any(e["function_name"] == "f" and e["status"] == "added" for e in log)


# ---------------------------------------------------------------------------
# Extraction parity — the legacy registration payload, unchanged
# ---------------------------------------------------------------------------

class TestExtractionParity:
    """to_registry_dict() emits the pre-contract scanner's payload for a corpus that
    includes multi-parameter functions (multiple pd.Series params, mixed shapes,
    non-alphabetical order, None/str defaults)."""

    @pytest.mark.unit
    def test_multi_series_params_with_scalar_default(self):
        def combine(zeta: pd.Series, alpha: pd.Series, weight: float = 0.5) -> pd.Series: ...
        contract = _inspect_function("combine", combine)
        assert isinstance(contract, FunctionContract)
        d = contract.to_registry_dict()
        # Signature order, NOT alphabetical (zeta before alpha).
        assert d["param_names"] == ["zeta", "alpha", "weight"]
        assert d["param_types"] == ["pd.Series", "pd.Series", "float"]
        assert d["param_has_default"] == [False, False, True]
        assert d["param_default_values"] == [None, None, "0.5"]
        assert d["param_positions"] == [0, 1, 2]
        assert d["function_class"] == "pd.Series"
        assert d["function_return_type"] == "pd.Series"
        assert d["function_type"] == "transform"
        assert d["engine"] == ENGINE_PYTHON
        assert d["function_body"] is None

    @pytest.mark.unit
    def test_mixed_scalar_str_none_default(self):
        # str default None is stored as the string "None" — documented lossy wart,
        # kept for hash stability.
        def tag(value: str, label: str = None) -> str: ...  # noqa: RUF013
        contract = _inspect_function("tag", tag)
        assert isinstance(contract, FunctionContract)
        d = contract.to_registry_dict()
        assert d["param_default_values"] == [None, "None"]
        assert d["function_class"] == "scalar"

    @pytest.mark.unit
    def test_validation_series_bool(self, tmp_path):
        # Extracted from a real file with the supported unquoted spelling:
        # user modules inherit PEP 563 (stringified) annotations from the scanner's
        # compile() call, so `-> pd.Series[bool]` arrives as its source text.
        # (The quoted spelling `-> "pd.Series[bool]"` double-stringifies and has
        # never been supported — pre-existing scanner behavior, kept for parity.)
        write_py(tmp_path, "checks.py", """
            import pandas as pd

            def check(vals: pd.Series, threshold: float) -> pd.Series[bool]:
                return vals > threshold
        """)
        contract = extract_contracts(tmp_path / "checks.py")[0].contract
        assert contract is not None
        d = contract.to_registry_dict()
        assert d["function_return_type"] == "pd.Series[bool]"
        assert d["function_type"] == "validation"

    @pytest.mark.unit
    def test_sql_contract_matches_legacy_shape(self, tmp_path):
        f = tmp_path / "clean.sql"
        f.write_text(
            "-- name: clean_nulls\n"
            "-- description: Remove null rows\n"
            "-- type: transform\n"
            "SELECT * FROM {source_table} WHERE a IS NOT NULL\n"
        )
        results = extract_contracts(f)
        assert len(results) == 1
        contract = results[0].contract
        assert contract is not None
        assert contract.engine == ENGINE_SQL
        assert contract.body is not None and "{source_table}" in contract.body
        d = contract.to_registry_dict()
        assert d["param_names"] == []
        assert d["function_class"] == "pd.dataframe"
        assert d["function_return_type"] == "pd.DataFrame"
        assert d["function_type"] == "transform"
        assert d["function_signature"] == "{source_table}: pd.DataFrame -> pd.DataFrame"
        assert d["function_doc"] == "Remove null rows"

    @pytest.mark.unit
    def test_sql_unknown_type_preserved(self, tmp_path):
        f = tmp_path / "mystery.sql"
        f.write_text("-- name: mystery\nSELECT 1\n")
        contract = extract_contracts(f)[0].contract
        assert contract is not None
        assert contract.function_type == "unknown"
        assert contract.function_return_type == "unknown"


# ---------------------------------------------------------------------------
# Contract round-trip + derivations
# ---------------------------------------------------------------------------

class TestContractRoundTrip:
    @pytest.mark.unit
    def test_to_dict_from_dict_round_trip(self):
        def norm(vals: pd.Series, lo: float = 0.0, hi: float = 1.0) -> pd.Series:
            """Normalize a column into [lo, hi]."""
        contract = _inspect_function("norm", norm, source_path="/tmp/mod.py")
        assert isinstance(contract, FunctionContract)
        assert FunctionContract.from_dict(contract.to_dict()) == contract

    @pytest.mark.unit
    def test_from_registry_rows_is_derivation_faithful(self):
        def flag(v: bool, note: str = "x") -> bool: ...
        contract = _inspect_function("flag", flag)
        assert isinstance(contract, FunctionContract)
        d = contract.to_registry_dict()
        fn_row = {
            "function_name": "flag",
            "function_return_type": d["function_return_type"],  # vocabulary form: "boolean"
            "function_signature": d["function_signature"],
            "function_doc": d["function_doc"],
            "module_path": None,
            "engine": d["engine"],
            "function_body": d["function_body"],
        }
        param_rows = [
            {"param_name": n, "param_type": t, "position": p, "has_default": h, "default_value": v}
            for n, t, p, h, v in zip(
                d["param_names"], d["param_types"], d["param_positions"],
                d["param_has_default"], d["param_default_values"],
            )
        ]
        rebuilt = FunctionContract.from_registry_rows(fn_row, param_rows)
        # Derived facts are stable across the round trip (raw annotation is not).
        assert rebuilt.function_class == contract.function_class
        assert rebuilt.function_return_type == contract.function_return_type
        assert rebuilt.function_type == contract.function_type
        assert [p.name for p in rebuilt.params] == [p.name for p in contract.params]
        assert [p.position for p in rebuilt.params] == [0, 1]

    @pytest.mark.unit
    def test_param_rows_reordered_by_position(self):
        # Rows arriving in alphabetical order rebuild in signature order.
        fn_row = {
            "function_name": "f",
            "function_return_type": "pd.Series",
            "function_signature": "(z: pd.Series, a: float) -> pd.Series",
        }
        rows = [
            {"param_name": "a", "param_type": "float", "position": 1},
            {"param_name": "z", "param_type": "pd.Series", "position": 0},
        ]
        rebuilt = FunctionContract.from_registry_rows(fn_row, rows)
        assert [p.name for p in rebuilt.params] == ["z", "a"]


class TestExecutionMode:
    """execution_mode derives from signature shape; vectorization is preferred."""

    def _c(self, *types: str) -> FunctionContract:
        params = tuple(
            ParamContract(name=f"p{i}", type_str=t, position=i) for i, t in enumerate(types)
        )
        return FunctionContract(
            name="f", engine=ENGINE_PYTHON, params=params,
            return_type="pd.Series", signature="(…)",
        )

    @pytest.mark.unit
    def test_dataframe_param_is_table(self):
        assert self._c("pd.DataFrame", "float").execution_mode() == "table"

    @pytest.mark.unit
    def test_no_params_is_table(self):
        assert self._c().execution_mode() == "table"

    @pytest.mark.unit
    def test_series_param_is_column_vectorized(self):
        assert self._c("pd.Series", "pd.Series", "float").execution_mode() == "column"
        assert self._c("pd.Series[bool]").execution_mode() == "column"

    @pytest.mark.unit
    def test_all_scalar_column_backed_is_row(self):
        c = self._c("str", "float")
        assert c.execution_mode(column_backed=frozenset({"p0"})) == "row"

    @pytest.mark.unit
    def test_all_scalar_unbound_is_value(self):
        assert self._c("str", "float").execution_mode() == "value"

    @pytest.mark.unit
    def test_series_beats_row_even_when_column_backed(self):
        # Vectorized preferred: a Series signature stays "column" regardless of binding.
        c = self._c("pd.Series", "str")
        assert c.execution_mode(column_backed=frozenset({"p0", "p1"})) == "column"


# ---------------------------------------------------------------------------
# Registration integration — position persisted, new columns written
# ---------------------------------------------------------------------------

class TestPositionPersisted:
    @pytest.mark.integration
    def test_parameter_position_is_signature_order(self, db, tmp_path):
        """Guarantee: parameter.position stores signature order, not alphabetical."""
        write_py(tmp_path, "mod.py", """
            import pandas as pd

            def blend(zeta: pd.Series, alpha: pd.Series, mid: float = 0.5) -> pd.Series:
                return zeta * mid + alpha * (1 - mid)
        """)
        log = scan_functions(db, [str(tmp_path)])
        assert any(e["status"] == "added" for e in log), log
        rows = db.execute(
            "SELECT p.param_name, p.position FROM parameter p "
            "JOIN function_registry fr ON fr.function_id = p.function_id "
            "WHERE fr.function_name = 'blend' ORDER BY p.position"
        ).fetchall()
        assert rows == [("zeta", 0), ("alpha", 1), ("mid", 2)]

    @pytest.mark.integration
    def test_engine_and_body_columns_written(self, db, tmp_path):
        write_py(tmp_path, "mod.py", """
            def double(x: int) -> int:
                return x * 2
        """)
        (tmp_path / "clean.sql").write_text(
            "-- name: clean\n-- type: transform\nSELECT * FROM {source_table}\n"
        )
        scan_functions(db, [str(tmp_path)])
        rows = dict(db.execute(
            "SELECT function_name, engine FROM function_registry"
        ).fetchall())
        assert rows == {"double": "python", "clean": "sql"}
        body = db.execute(
            "SELECT function_body FROM function_registry WHERE function_name = 'clean'"
        ).fetchone()[0]
        assert "{source_table}" in body
