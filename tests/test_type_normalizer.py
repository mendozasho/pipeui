"""Unit tests for the single DuckDB→canonical type normalizer (#52).

`normalize_column_type` is the one source of the "known DuckDB type, else VARCHAR"
rule that previously lived inline in inference.py, create.py, and ingestion._py_type.
"""
import pytest

from pipeui.backend.data.base.schema.constants import (
    DUCKDB_TO_PYTHON,
    normalize_column_type,
)
from pipeui.backend.domain.sources.ingestion import _py_type


class TestNormalizeColumnType:
    """The canonical normalizer: upper-case, strip parameterization, known-else-VARCHAR."""

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", sorted(DUCKDB_TO_PYTHON.keys()))
    def test_known_types_pass_through_unchanged(self, raw):
        assert normalize_column_type(raw) == raw

    @pytest.mark.unit
    @pytest.mark.parametrize("raw,expected", [
        ("bigint", "BIGINT"),
        ("varchar", "VARCHAR"),
        ("Double", "DOUBLE"),
        ("BoOl", "BOOL"),
    ])
    def test_case_insensitive(self, raw, expected):
        assert normalize_column_type(raw) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("raw,expected", [
        ("VARCHAR(100)", "VARCHAR"),
        ("DECIMAL(18,3)", "DECIMAL"),
        ("DECIMAL(10, 2)", "DECIMAL"),
        ("varchar(255)", "VARCHAR"),
    ])
    def test_strips_parameterization(self, raw, expected):
        assert normalize_column_type(raw) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", ["JSON", "BLOB", "UUID", "STRUCT(a INT)", "MAP(VARCHAR, INT)", "wibble"])
    def test_unknown_types_fall_back_to_varchar(self, raw):
        assert normalize_column_type(raw) == "VARCHAR"

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", ["", None])
    def test_empty_falls_back_to_varchar(self, raw):
        assert normalize_column_type(raw) == "VARCHAR"

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", [
        "BIGINT", "varchar", "DECIMAL(18,3)", "JSON", "", "TIMESTAMP", "hugeint",
    ])
    def test_result_is_always_a_known_key(self, raw):
        # The invariant that makes DUCKDB_TO_PYTHON[normalize_column_type(x)] always safe.
        assert normalize_column_type(raw) in DUCKDB_TO_PYTHON


class TestPyTypeRoutesThroughNormalizer:
    """ingestion._py_type now derives from the canonical normalizer — behavior guard
    that the conversion preserved its Python-type mapping (#52)."""

    @pytest.mark.unit
    @pytest.mark.parametrize("raw,expected", [
        ("BIGINT", int),
        ("integer", int),
        ("DOUBLE", float),
        ("DECIMAL(10,2)", float),
        ("VARCHAR(50)", str),
        ("DATE", str),
        ("BOOLEAN", bool),
        ("JSON", str),       # unknown → VARCHAR → str
        ("", str),           # empty → VARCHAR → str
    ])
    def test_py_type_maps_to_python_type(self, raw, expected):
        assert _py_type(raw) is expected

    @pytest.mark.unit
    @pytest.mark.parametrize("raw", ["BIGINT", "VARCHAR(100)", "DECIMAL(9,2)", "JSON", ""])
    def test_py_type_equals_normalizer_composition(self, raw):
        assert _py_type(raw) is DUCKDB_TO_PYTHON[normalize_column_type(raw)]
