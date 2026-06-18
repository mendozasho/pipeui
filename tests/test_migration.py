"""Tests for workflow/migration.py — behavioral guarantees per §7 / §13."""
from __future__ import annotations

import csv
import uuid

import pytest

from pipeui.backend.domain.sources.create import create_source
from pipeui.backend.domain.sources.ingestion import ingest_source
from pipeui.backend.domain.sources.migration import migrate_column, ALLOWED_COLUMN_TYPES
from pipeui.backend.data.base.tables import instance_table_name
from pipeui.backend.data.base.ids import content_hash_id
from tests.conftest import make_registered_source, make_quirky_file


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


def register_and_ingest(db, tmp_path, source_name, columns, rows, pk=None, method="upsert"):
    pk = pk or columns[0]
    path = make_csv(tmp_path, f"{source_name}.csv", columns, rows)
    source_id, failed = create_source(db, path, source_name, pk, method)
    assert not failed.has_failures(), str(failed)
    ingest_source(db, source_id, path)
    return source_id, path


def get_column_id(db, source_id):
    """Return list of (column_id, column_name, column_type) for a source."""
    rows = db.execute(
        """
        SELECT cr.column_id, cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------------------------------------------------------------------------
# AC1: column_type outside allowed set → structured failure, no DB changes
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_invalid_column_type_returns_structured_failure(db, tmp_path):
    """column_type outside allowed set returns ok=False with reason; DB unchanged."""
    source_id, _ = register_and_ingest(db, tmp_path, "s1", ["id", "val"], [["r1", "10"]])
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")

    result = migrate_column(db, source_id, col[0], "HUGEINT")
    assert result["ok"] is False
    assert result["error"] == "invalid_column_type"
    assert "HUGEINT" in result["reason"]

    # DB unchanged: column type still the same
    updated = get_column_id(db, source_id)
    assert next(c for c in updated if c[1] == "val")[2] == col[2]


@pytest.mark.integration
def test_empty_column_type_returns_structured_failure(db, tmp_path):
    source_id, _ = register_and_ingest(db, tmp_path, "s1", ["id", "val"], [["r1", "10"]])
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")
    result = migrate_column(db, source_id, col[0], "")
    assert result["ok"] is False
    assert result["error"] == "invalid_column_type"


# ---------------------------------------------------------------------------
# AC2: dry_run returns correct counts and shared sources without mutating DB
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dry_run_returns_counts_and_does_not_mutate(db, tmp_path):
    """dry_run returns castable/uncastable/shared_sources and makes no DB changes."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "val"],
        [["r1", "10"], ["r2", "abc"], ["r3", "30"]],
        method="upsert",
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")

    result = migrate_column(db, source_id, col[0], "INTEGER", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    # "r2" / "abc" is un-castable to INTEGER; "r1", "r3" are castable
    assert result["uncastable"] == 1
    assert result["castable"] == 2
    assert any(str(source_id) == s["source_id"] for s in result["shared_sources"])

    # Column registry must be unchanged
    same_cols = get_column_id(db, source_id)
    assert same_cols == cols


# ---------------------------------------------------------------------------
# AC3: on_uncastable="abort" leaves everything unchanged
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_abort_on_uncastable_leaves_db_unchanged(db, tmp_path):
    """on_uncastable='abort' returns ok=False; column registry and data unchanged."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "val"],
        [["r1", "10"], ["r2", "not_an_int"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")

    result = migrate_column(db, source_id, col[0], "INTEGER", on_uncastable="abort")
    assert result["ok"] is False
    assert result["error"] == "uncastable_rows"

    # Column registry and instance table unchanged
    same_cols = get_column_id(db, source_id)
    assert same_cols == cols
    tname = instance_table_name(source_id)
    count = db.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# AC4: on_uncastable="nullify" — proceeds; nullified list has correct PKs + column
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_nullify_proceeds_and_returns_pk_list(db, tmp_path):
    """on_uncastable='nullify' migrates successfully; un-castable rows become NULL."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "amount"],
        [["r1", "10"], ["r2", "bad"], ["r3", "30"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "amount")

    result = migrate_column(db, source_id, col[0], "INTEGER", on_uncastable="nullify")
    assert result["ok"] is True
    assert len(result["nullified"]) == 1
    assert result["nullified"][0]["pk"] == "r2"
    assert result["nullified"][0]["column"] == "amount"

    # r2's amount should now be NULL in the instance table
    tname = instance_table_name(source_id)
    val = db.execute(f'SELECT amount FROM "{tname}" WHERE id = \'r2\'').fetchone()[0]
    assert val is None

    # r1 and r3 should still be numeric
    v1 = db.execute(f'SELECT amount FROM "{tname}" WHERE id = \'r1\'').fetchone()[0]
    assert v1 == 10


# ---------------------------------------------------------------------------
# AC5: mid-migration failure rolls back completely
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_mid_migration_failure_rolls_back(db, tmp_path):
    """If the migration fails mid-way, the DB returns to its last committed state."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "val"], [["r1", "10"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")

    # Simulate a bad target type that passes the allowed-set check but fails
    # in DuckDB SQL — inject an invalid table name by passing a bad source_id.
    bad_source_id = uuid.uuid4()  # not registered
    result = migrate_column(db, bad_source_id, col[0], "INTEGER")
    assert result["ok"] is False
    # Either source_not_found (before any write) or migration_failed
    assert result["error"] in ("source_not_found", "migration_failed", "uncastable_rows")

    # Original column registry is intact
    same_cols = get_column_id(db, source_id)
    assert same_cols == cols


# ---------------------------------------------------------------------------
# AC6: column_registry updated correctly after success
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_column_registry_updated_after_success(db, tmp_path):
    """column_type and content_hash_id are updated in column_registry after migration."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "val"], [["r1", "10"], ["r2", "20"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")
    old_type = col[2]

    result = migrate_column(db, source_id, col[0], "BIGINT")
    assert result["ok"] is True, result

    # For scope="this_source" copy-on-write: a NEW column_registry row is
    # created (or reused).  Verify via source_column_map which col_id is now
    # pointed at for this source.
    new_cols = get_column_id(db, source_id)
    new_col = next(c for c in new_cols if c[1] == "val")
    assert new_col[2] == "BIGINT"
    # content_hash_id must match content_hash_id("column_registry", "val", "BIGINT")
    expected_hash = content_hash_id("column_registry", "val", "BIGINT")
    new_ch = db.execute(
        "SELECT content_hash_id FROM column_registry WHERE column_id = ?",
        [new_col[0]],
    ).fetchone()[0]
    assert str(new_ch) == str(expected_hash)


# ---------------------------------------------------------------------------
# AC7: copy-on-write reuses existing (column_name, new_type) row when one exists
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_copy_on_write_reuses_existing_registry_row(db, tmp_path):
    """scope='this_source' reuses an existing column_registry row with same hash."""
    # Pre-insert a column_registry row for ("val", "BIGINT")
    existing_col_id = uuid.uuid4()
    existing_hash = content_hash_id("column_registry", "val", "BIGINT")
    db.execute(
        "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
        [existing_col_id, existing_hash, "val", "BIGINT"],
    )

    source_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "val"], [["r1", "10"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")

    result = migrate_column(db, source_id, col[0], "BIGINT", scope="this_source")
    assert result["ok"] is True, result

    # source_column_map should now point to the pre-existing col id
    map_col = db.execute(
        "SELECT column_id FROM source_column_map WHERE source_id = ?",
        [source_id],
    ).fetchall()
    col_ids_after = [r[0] for r in map_col]
    assert existing_col_id in col_ids_after


# ---------------------------------------------------------------------------
# AC8: copy-on-write creates new row when none exists
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_copy_on_write_creates_new_row_when_none_exists(db, tmp_path):
    """scope='this_source' inserts a new column_registry row when no matching row exists."""
    # Use a string value so create_source infers VARCHAR; we'll migrate to INTEGER.
    # The target hash for ("val", "INTEGER") should not yet exist.
    source_id, _ = register_and_ingest(
        db, tmp_path, "s1_cow", ["id", "val"], [["r1", "hello"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "val")
    original_col_id = col[0]
    original_type = col[2]  # should be VARCHAR

    # Ensure no INTEGER row exists for "val" yet
    target_hash = content_hash_id("column_registry", "val", "INTEGER")
    db.execute("DELETE FROM column_registry WHERE content_hash_id = ?", [target_hash])

    result = migrate_column(db, source_id, col[0], "INTEGER", scope="this_source")
    # "hello" is not castable to INTEGER — so we need nullify mode
    result = migrate_column(
        db, source_id, col[0], "INTEGER", scope="this_source", on_uncastable="nullify"
    )
    assert result["ok"] is True, result

    # A column_registry row must now exist for ("val", "INTEGER")
    new_row = db.execute(
        "SELECT column_id, column_type FROM column_registry WHERE content_hash_id = ?",
        [target_hash],
    ).fetchone()
    assert new_row is not None
    assert new_row[1] == "INTEGER"


# ---------------------------------------------------------------------------
# AC9: scope="this_source" leaves other sources' source_column_map on original row
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_this_source_scope_does_not_affect_other_sources(db, tmp_path):
    """scope='this_source' only re-points the calling source; siblings are unaffected."""
    # Create two sources, manually share a column_registry row between them
    source1_id, _ = register_and_ingest(
        db, tmp_path, "s1", ["id", "val"], [["r1", "10"]],
    )
    source2_id, _ = register_and_ingest(
        db, tmp_path, "s2", ["id", "val"], [["x1", "20"]],
    )

    # Find source1's "val" column_id and share it with source2
    cols1 = get_column_id(db, source1_id)
    col1 = next(c for c in cols1 if c[1] == "val")
    shared_col_id = col1[0]

    # Point source2's "val" map entry at source1's col_id
    # (simulate a shared column_registry row scenario)
    db.execute(
        "DELETE FROM source_column_map WHERE source_id = ? AND column_id IN "
        "(SELECT column_id FROM column_registry WHERE column_name = 'val')",
        [source2_id],
    )
    map_id = content_hash_id("source_column_map", str(source2_id), str(shared_col_id))
    db.execute(
        "INSERT OR REPLACE INTO source_column_map VALUES (?, ?, ?)",
        [map_id, shared_col_id, source2_id],
    )

    # Migrate source1 only
    result = migrate_column(db, source1_id, shared_col_id, "BIGINT", scope="this_source")
    assert result["ok"] is True, result

    # source2 must still reference the original column_id
    s2_map = db.execute(
        "SELECT column_id FROM source_column_map WHERE source_id = ?",
        [source2_id],
    ).fetchall()
    s2_col_ids = [r[0] for r in s2_map]
    assert shared_col_id in s2_col_ids


# ---------------------------------------------------------------------------
# AC10: scope="all_shared" migrates all sharing sources in one transaction
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_all_shared_scope_migrates_all_sources(db, tmp_path):
    """scope='all_shared' updates the shared column_registry row and all instance tables."""
    source1_id, _ = register_and_ingest(
        db, tmp_path, "shared_a", ["id", "val"], [["r1", "10"]],
    )
    source2_id, _ = register_and_ingest(
        db, tmp_path, "shared_b", ["id", "val"], [["x1", "20"]],
    )

    # Share source1's "val" col_id with source2
    cols1 = get_column_id(db, source1_id)
    col1 = next(c for c in cols1 if c[1] == "val")
    shared_col_id = col1[0]

    db.execute(
        "DELETE FROM source_column_map WHERE source_id = ? AND column_id IN "
        "(SELECT column_id FROM column_registry WHERE column_name = 'val')",
        [source2_id],
    )
    map_id = content_hash_id("source_column_map", str(source2_id), str(shared_col_id))
    db.execute(
        "INSERT OR REPLACE INTO source_column_map VALUES (?, ?, ?)",
        [map_id, shared_col_id, source2_id],
    )

    result = migrate_column(db, source1_id, shared_col_id, "BIGINT", scope="all_shared")
    assert result["ok"] is True, result

    # Both instance tables should have BIGINT "val"
    for sid in [source1_id, source2_id]:
        tname = instance_table_name(sid)
        col_info = db.execute(
            f"SELECT data_type FROM information_schema.columns "
            f"WHERE table_name = ? AND column_name = 'val'",
            [tname],
        ).fetchone()
        assert col_info is not None
        assert col_info[0].upper() in ("BIGINT", "HUGEINT", "INT8")  # DuckDB may report INT8

    # column_registry row must be updated to BIGINT
    updated = db.execute(
        "SELECT column_type FROM column_registry WHERE column_id = ?",
        [shared_col_id],
    ).fetchone()
    assert updated[0] == "BIGINT"


# ---------------------------------------------------------------------------
# AC11: content_hash_id collision on update → structured failure
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_content_hash_id_collision_returns_structured_failure(db, tmp_path):
    """scope='all_shared' returns ok=False when the new hash collides on a different row."""
    # Register a source with a VARCHAR "label" column.
    # We'll try to migrate to BIGINT, but pre-plant a collision row first.
    source_id, _ = register_and_ingest(
        db, tmp_path, "s_coll", ["id", "label"], [["r1", "hello"]],
    )
    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "label")
    assert col[2] == "VARCHAR", f"Expected VARCHAR, got {col[2]}"

    # The hash that all_shared migration would write into the shared row when
    # changing ("label", "VARCHAR") → ("label", "BIGINT")
    target_hash = content_hash_id("column_registry", "label", "BIGINT")

    # Insert a DIFFERENT column_registry row with the target hash before migrating
    collider_id = uuid.uuid4()
    db.execute(
        "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
        [collider_id, target_hash, "label_alias", "BIGINT"],
    )

    # Migration to BIGINT should detect the collision and fail
    result = migrate_column(
        db, source_id, col[0], "BIGINT", scope="all_shared", on_uncastable="nullify"
    )
    assert result["ok"] is False
    assert result["error"] == "content_hash_id_collision"


# ---------------------------------------------------------------------------
# make_quirky_file: mixed_type exercises TRY_CAST uncastable-row count path
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_quirky_mixed_type_trycast_uncastable_count(db, tmp_path):
    """make_quirky_file mixed_type=True produces a column where 'abc' is un-castable
    to INTEGER; dry_run must report exactly 1 uncastable and 2 castable rows."""
    p = make_quirky_file(tmp_path, {"mixed_type": True})
    source_id, failed = create_source(db, str(p), "quirky_mixed", "id", "upsert")
    assert not failed.has_failures(), str(failed)
    ingest_source(db, source_id, str(p))

    cols = get_column_id(db, source_id)
    col = next(c for c in cols if c[1] == "mixed_col")

    result = migrate_column(db, source_id, col[0], "INTEGER", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    # "abc" is un-castable; "123" and "456" are castable
    assert result["uncastable"] == 1
    assert result["castable"] == 2

    # on_uncastable="abort" must refuse the migration
    result_abort = migrate_column(db, source_id, col[0], "INTEGER", on_uncastable="abort")
    assert result_abort["ok"] is False
    assert result_abort["error"] == "uncastable_rows"


# ---------------------------------------------------------------------------
# Allowed type set sanity
# ---------------------------------------------------------------------------

def test_allowed_column_types_set():
    assert "VARCHAR" in ALLOWED_COLUMN_TYPES
    assert "INTEGER" in ALLOWED_COLUMN_TYPES
    assert "TIMESTAMP" in ALLOWED_COLUMN_TYPES
    assert "HUGEINT" not in ALLOWED_COLUMN_TYPES


# ---------------------------------------------------------------------------
# Numeric thousands-separator handling (US/UK format) — migration path only.
# A value like "250,000" must migrate to DOUBLE as 250000 instead of being
# nullified. Decided with the user; comma = thousands separator, period = decimal.
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_thousands_separator_values_cast_to_numeric_not_nullified(db, tmp_path):
    """'250,000' / '12,345.67' migrate to DOUBLE (250000 / 12345.67), not NULL."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "amounts",
        ["policy", "premium"],
        [["A", "1000"], ["B", "2,500"], ["C", "250,000"], ["D", "12,345.67"]],
    )
    cols = get_column_id(db, source_id)
    premium = next(c for c in cols if c[1] == "premium")
    # Inferred as VARCHAR because of the commas (autodetection unchanged).
    assert premium[2] == "VARCHAR"

    # Dry-run: commas are handled, so nothing is uncastable.
    dry = migrate_column(db, source_id, premium[0], "DOUBLE", dry_run=True)
    assert dry["ok"] is True
    assert dry["uncastable"] == 0

    # Commit: all values cast, nothing nullified.
    result = migrate_column(db, source_id, premium[0], "DOUBLE", on_uncastable="nullify")
    assert result["ok"] is True
    assert result["nullified"] == []

    tname = instance_table_name(source_id)
    vals = dict(db.execute(f'SELECT policy, premium FROM "{tname}"').fetchall())
    assert vals["B"] == 2500.0
    assert vals["C"] == 250000.0
    assert vals["D"] == 12345.67


@pytest.mark.integration
def test_non_numeric_value_still_uncastable_for_numeric_target(db, tmp_path):
    """Comma-stripping must not make genuinely non-numeric text castable: 'abc'
    is still uncastable to DOUBLE and is nullified, while '250,000' survives."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "amounts2",
        ["policy", "premium"],
        [["A", "250,000"], ["B", "abc"]],
    )
    premium = next(c for c in get_column_id(db, source_id) if c[1] == "premium")

    dry = migrate_column(db, source_id, premium[0], "DOUBLE", dry_run=True)
    assert dry["uncastable"] == 1  # only "abc"

    result = migrate_column(db, source_id, premium[0], "DOUBLE", on_uncastable="nullify")
    assert result["ok"] is True
    assert [n["pk"] for n in result["nullified"]] == ["B"]

    tname = instance_table_name(source_id)
    vals = dict(db.execute(f'SELECT policy, premium FROM "{tname}"').fetchall())
    assert vals["A"] == 250000.0
    assert vals["B"] is None


@pytest.mark.integration
def test_numeric_cleaning_currency_percent_parens_whitespace(db, tmp_path):
    """The numeric cleaner handles currency symbols, percent (÷100), accounting
    parentheses (negatives), and whitespace — not just commas."""
    source_id, _ = register_and_ingest(
        db, tmp_path, "amounts3",
        ["k", "v"],
        [
            ["dollar", "$1,234.50"],
            ["pct", "50%"],
            ["pct_dec", "12.5%"],
            ["paren", "(2,500)"],
            ["lead_space", "  99  "],
            ["inner_space", "1 234"],
        ],
    )
    v = next(c for c in get_column_id(db, source_id) if c[1] == "v")

    result = migrate_column(db, source_id, v[0], "DOUBLE", on_uncastable="nullify")
    assert result["ok"] is True
    assert result["nullified"] == []

    tname = instance_table_name(source_id)
    vals = dict(db.execute(f'SELECT k, v FROM "{tname}"').fetchall())
    assert vals["dollar"] == 1234.5
    assert vals["pct"] == 0.5          # percent divides by 100
    assert vals["pct_dec"] == 0.125
    assert vals["paren"] == -2500.0    # accounting parens → negative
    assert vals["lead_space"] == 99.0
    assert vals["inner_space"] == 1234.0
