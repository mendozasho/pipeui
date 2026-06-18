"""Tests for the source read-path module (#48) — backend/domain/sources/read.py.

Covers the new workflow contract the API seam now delegates to (DIP fix): the
listing/summary builders, the existence/ownership guards, and a structural guard that
no raw SQL SELECT remains in the middleware seam.
"""
from __future__ import annotations

import uuid

import pytest

from pipeui.backend.data.base.ids import content_hash_id
from pipeui.backend.domain.sources.read import (
    check_column_ownership,
    get_source_columns,
    get_source_summary,
    list_source_summaries,
    source_exists,
)
from tests.conftest import make_registered_source


@pytest.mark.integration
def test_list_source_summaries_shape_and_exact_row_count(db):
    """list_source_summaries returns the registry fields + columns + EXACT row_count.

    row_count is 0 before ingestion (instance table absent) — the COUNT(*) is wrapped,
    not an error. Columns come back ordered by name. This is the payload GET /sources
    serves; it replaces the old per-source get_source_detail N+1.
    """
    source_id, col_ids = make_registered_source(db, n_columns=2)

    summaries = list_source_summaries(db)
    rec = next(r for r in summaries if r["source_id"] == str(source_id))

    # Base registry fields + columns + row_count present.
    for key in ("source_id", "source_name", "date_registered", "ingestion_method",
                "primary_key", "pattern", "table_url", "content_hash_id", "columns", "row_count"):
        assert key in rec, f"missing {key}"
    assert rec["row_count"] == 0  # not ingested → 0, never an error
    assert [c["column_name"] for c in rec["columns"]] == ["col_0", "col_1"]
    assert all({"column_id", "column_name", "column_type"} <= c.keys() for c in rec["columns"])


@pytest.mark.integration
def test_get_source_summary_excludes_row_count(db):
    """get_source_summary (the register/ingest-match echo) returns the same record shape
    as a list entry but WITHOUT row_count — matching the legacy register payload."""
    source_id, _ = make_registered_source(db, n_columns=1)

    rec = get_source_summary(db, source_id)
    assert rec is not None
    assert rec["source_id"] == str(source_id)
    assert "row_count" not in rec  # register echo never carried row_count
    assert [c["column_name"] for c in rec["columns"]] == ["col_0"]


@pytest.mark.integration
def test_get_source_summary_none_for_unknown(db):
    """get_source_summary returns None for an unregistered source_id (register's
    next(..., None) default behavior is preserved)."""
    assert get_source_summary(db, uuid.uuid4()) is None


@pytest.mark.integration
def test_get_source_columns_shape(db):
    """get_source_columns returns [{column_name, column_type}] ordered by name — the
    join-modal picker payload (no column_id)."""
    source_id, _ = make_registered_source(db, n_columns=2)
    cols = get_source_columns(db, source_id)
    assert cols == [
        {"column_name": "col_0", "column_type": "INTEGER"},
        {"column_name": "col_1", "column_type": "INTEGER"},
    ]


@pytest.mark.integration
def test_source_exists_true_and_false(db):
    """source_exists reflects registry membership (the existence guard the routes 404 on)."""
    source_id, _ = make_registered_source(db, n_columns=1)
    assert source_exists(db, source_id) is True
    assert source_exists(db, uuid.uuid4()) is False


@pytest.mark.integration
def test_check_column_ownership_all_statuses(db):
    """check_column_ownership returns the four ordered statuses the migrate route maps to
    its three distinct 404s (source → column → membership) plus the ok pass-through."""
    source_id, col_ids = make_registered_source(db, n_columns=1)

    # ok — the column belongs to the source
    assert check_column_ownership(db, source_id, col_ids[0]) == "ok"

    # source_missing — unknown source (checked first, before the column)
    assert check_column_ownership(db, uuid.uuid4(), col_ids[0]) == "source_missing"

    # column_missing — source exists, column_id not in column_registry
    assert check_column_ownership(db, source_id, uuid.uuid4()) == "column_missing"

    # not_owned — a real column that exists but is NOT mapped to this source
    orphan_id = uuid.uuid4()
    db.execute(
        "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
        [orphan_id, content_hash_id("column_registry", "orphan_col", "INTEGER"), "orphan_col", "INTEGER"],
    )
    assert check_column_ownership(db, source_id, orphan_id) == "not_owned"


def test_middleware_seam_has_no_raw_sql():
    """#48 acceptance / DIP guard: the source/pipeline/builtin API seam must not run SQL
    — no ``conn.execute`` lives in these route modules; they delegate to the workflow."""
    import pipeui.middleware.sources as m_sources
    import pipeui.middleware.pipelines as m_pipelines
    import pipeui.middleware.builtins as m_builtins

    from pathlib import Path
    for mod in (m_sources, m_pipelines, m_builtins):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert ".execute(" not in src, f"raw SQL leaked back into the seam: {mod.__name__}"
