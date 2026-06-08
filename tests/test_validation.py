"""
Behavioral-guarantee tests for pipeui.validation (§3, §4).
Marker: unit — pure logic, no DB, no subprocess.
"""

import pytest
from pydantic import ValidationError

from pipeui.ids import content_hash_id
from pipeui.validation import (
    ColumnRegistryEntry,
    ColumnRegistryUpdate,
    FailedRegistryEntry,
    SourceRegistryEntry,
    SourceRegistryUpdate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_source(**kwargs) -> SourceRegistryEntry:
    defaults = dict(
        source_name="sales",
        primary_key="id",
        ingestion_method="upsert",
    )
    defaults.update(kwargs)
    return SourceRegistryEntry(**defaults)


def _make_column(**kwargs) -> ColumnRegistryEntry:
    defaults = dict(column_name="amount", column_type="INTEGER")
    defaults.update(kwargs)
    return ColumnRegistryEntry(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_source_entry_content_hash_id_computed_at_construction():
    # §2 contributing fields; §3 recompute at construction
    entry = _make_source()
    expected = content_hash_id("source_registry", "sales", "id", "upsert")
    assert entry.content_hash_id == expected


@pytest.mark.unit
def test_source_entry_content_hash_id_changes_on_source_name_change():
    # §2 recompute sensitivity — different source_name → different hash
    a = _make_source(source_name="alpha")
    b = _make_source(source_name="beta")
    assert a.content_hash_id != b.content_hash_id


@pytest.mark.unit
def test_source_entry_content_hash_id_stable_on_non_contributing_field_change():
    # §3: non-contributing field (pattern) must not affect content_hash_id
    a = _make_source(pattern=".*_sales\\.csv")
    b = _make_source(pattern="monthly_.*\\.xlsx")
    assert a.content_hash_id == b.content_hash_id


@pytest.mark.unit
def test_source_entry_surrogate_id_never_changed_by_update():
    # §3: surrogate source_id must be preserved across updates
    entry = _make_source()
    update = SourceRegistryUpdate.from_existing(entry, source_name="new_name")
    assert update.source_id == entry.source_id


@pytest.mark.unit
def test_source_update_recomputes_hash_on_contributing_field():
    # §3 recompute-on-edit: touching source_name must change content_hash_id
    entry = _make_source()
    update = SourceRegistryUpdate.from_existing(entry, source_name="new_name")
    assert update.content_hash_id != entry.content_hash_id


@pytest.mark.unit
def test_source_update_does_not_recompute_hash_on_non_contributing_field():
    # §3: touching only pattern (non-contributing) must leave content_hash_id unchanged
    entry = _make_source()
    update = SourceRegistryUpdate.from_existing(entry, pattern="new_pattern")
    assert update.content_hash_id == entry.content_hash_id


@pytest.mark.unit
def test_column_entry_content_hash_id_computed():
    # §2, §3: ColumnRegistryEntry computes content_hash_id at construction
    entry = _make_column()
    expected = content_hash_id("column_registry", "amount", "INTEGER")
    assert entry.content_hash_id == expected


@pytest.mark.unit
def test_column_update_recomputes_hash_on_column_type_change():
    # §3 recompute-on-edit for column: column_type is a contributing field
    entry = _make_column()
    update = ColumnRegistryUpdate.from_existing(entry, column_type="DOUBLE")
    assert update.content_hash_id != entry.content_hash_id


@pytest.mark.unit
def test_invalid_ingestion_method_routes_to_failed_registry_entry():
    # §4: invalid ingestion_method raises ValidationError; caller routes to FailedRegistryEntry
    stack = FailedRegistryEntry()
    try:
        bad = SourceRegistryEntry(
            source_name="sales", primary_key="id", ingestion_method="bad"
        )
    except ValidationError as exc:
        stack.add(
            SourceRegistryEntry(
                source_name="sales", primary_key="id", ingestion_method="upsert"
            ),
            str(exc),
        )
    assert stack.has_failures()


@pytest.mark.unit
def test_failed_registry_entry_accumulates_multiple_failures():
    # §4 stack semantics: multiple add() calls accumulate
    stack = FailedRegistryEntry()
    e1 = _make_column(column_name="a")
    e2 = _make_column(column_name="b")
    stack.add(e1, "reason one")
    stack.add(e2, "reason two")
    assert len(stack.failures) == 2


@pytest.mark.unit
def test_update_produces_matching_hash_on_colliding_rename():
    # §1 model guarantee: from_existing recomputes hash so a rename onto an existing name
    # produces the same hash — the workflow layer then detects and rejects the collision.
    entry_a = _make_source(source_name="alpha")
    entry_b = _make_source(source_name="beta")

    update = SourceRegistryUpdate.from_existing(entry_b, source_name="alpha")
    assert update.content_hash_id == entry_a.content_hash_id
