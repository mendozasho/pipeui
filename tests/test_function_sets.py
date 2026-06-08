"""Behavioral guarantee tests for workflow/function_sets.py — Phase D2 (§13)."""
from __future__ import annotations

import textwrap
import uuid

import pytest

from pipeui.workflow.function_sets import create_function_set, list_function_sets
from pipeui.workflow.functions import scan_functions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_functions(db, tmp_path, src: str) -> list[str]:
    """Write a .py file, scan it, return list of function_ids in name order."""
    p = tmp_path / f"fn_{uuid.uuid4().hex[:6]}.py"
    p.write_text(textwrap.dedent(src))
    scan_functions(db, [str(tmp_path)])
    rows = db.execute(
        "SELECT function_id FROM function_registry ORDER BY function_name"
    ).fetchall()
    return [str(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# create_function_set
# ---------------------------------------------------------------------------


class TestCreateFunctionSet:
    @pytest.mark.integration
    def test_creates_set_and_map_rows(self, db, tmp_path):
        """Guarantee: create writes function_set + function_set_map rows atomically."""
        fn_ids = _register_functions(db, tmp_path, """
            def fn_a(x: int) -> int: return x
            def fn_b(x: int) -> int: return x
        """)
        result = create_function_set(db, "my_set", "desc", fn_ids)
        assert "set_id" in result
        assert result["set_name"] == "my_set"
        assert result["member_count"] == 2

        set_id = result["set_id"]
        map_rows = db.execute(
            "SELECT function_id, position FROM function_set_map WHERE set_id = ? ORDER BY position",
            [set_id],
        ).fetchall()
        assert len(map_rows) == 2
        assert [str(r[0]) for r in map_rows] == fn_ids
        assert [r[1] for r in map_rows] == [0, 1]

    @pytest.mark.integration
    def test_empty_members_allowed(self, db, tmp_path):
        """Guarantee: a set with no members can be created."""
        result = create_function_set(db, "empty_set", None, [])
        assert result["member_count"] == 0
        assert result["set_description"] is None

    @pytest.mark.integration
    def test_duplicate_name_returns_failure(self, db, tmp_path):
        """Guarantee: duplicate set_name returns FailedRegistryEntry, no partial write."""
        create_function_set(db, "dup_set", None, [])
        result = create_function_set(db, "dup_set", "second", [])
        assert hasattr(result, "has_failures")
        assert result.has_failures()
        # Only one set should exist
        count = db.execute("SELECT COUNT(*) FROM function_set WHERE set_name = 'dup_set'").fetchone()[0]
        assert count == 1

    @pytest.mark.integration
    def test_atomicity_on_bad_function_id(self, db, tmp_path):
        """Guarantee: if any map row fails, the whole transaction rolls back."""
        bad_ids = ["not-a-uuid"]
        result = create_function_set(db, "bad_set", None, bad_ids)
        assert hasattr(result, "has_failures")
        assert result.has_failures()
        count = db.execute("SELECT COUNT(*) FROM function_set WHERE set_name = 'bad_set'").fetchone()[0]
        assert count == 0

    @pytest.mark.integration
    def test_set_map_id_prevents_duplicate_members(self, db, tmp_path):
        """Guarantee: set_map_id = uuid5(set_id, function_id) — same function twice is rejected."""
        fn_ids = _register_functions(db, tmp_path, """
            def fn_a(x: int) -> int: return x
        """)
        # Try to insert the same function twice in the members list
        result = create_function_set(db, "dup_member_set", None, [fn_ids[0], fn_ids[0]])
        assert hasattr(result, "has_failures")
        assert result.has_failures()
        count = db.execute("SELECT COUNT(*) FROM function_set WHERE set_name = 'dup_member_set'").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# list_function_sets
# ---------------------------------------------------------------------------


class TestListFunctionSets:
    @pytest.mark.integration
    def test_returns_empty_when_no_sets(self, db, tmp_path):
        """Guarantee: list returns [] when no sets exist."""
        assert list_function_sets(db) == []

    @pytest.mark.integration
    def test_returns_all_sets_ordered_by_name(self, db, tmp_path):
        """Guarantee: sets are returned in ascending set_name order."""
        create_function_set(db, "zebra", None, [])
        create_function_set(db, "alpha", None, [])
        result = list_function_sets(db)
        names = [r["set_name"] for r in result]
        assert names == sorted(names)

    @pytest.mark.integration
    def test_member_count_is_correct(self, db, tmp_path):
        """Guarantee: member_count reflects the number of function_set_map rows."""
        fn_ids = _register_functions(db, tmp_path, """
            def fn_a(x: int) -> int: return x
            def fn_b(x: int) -> int: return x
            def fn_c(x: int) -> int: return x
        """)
        create_function_set(db, "three_fns", None, fn_ids)
        result = list_function_sets(db)
        assert result[0]["member_count"] == 3

    @pytest.mark.integration
    def test_has_inactive_is_false_when_all_active(self, db, tmp_path):
        """Guarantee: has_inactive is false when all member functions are active."""
        fn_ids = _register_functions(db, tmp_path, """
            def fn_a(x: int) -> int: return x
        """)
        create_function_set(db, "active_set", None, fn_ids)
        result = list_function_sets(db)
        assert result[0]["has_inactive"] is False

    @pytest.mark.integration
    def test_has_inactive_is_true_when_member_inactive(self, db, tmp_path):
        """Guarantee: has_inactive is true when any member function has is_active = false."""
        fn_ids = _register_functions(db, tmp_path, """
            def fn_a(x: int) -> int: return x
        """)
        create_function_set(db, "inactive_set", None, fn_ids)
        # Manually flip is_active
        db.execute(
            "UPDATE function_registry SET is_active = false WHERE function_id = ?",
            [fn_ids[0]],
        )
        result = list_function_sets(db)
        assert result[0]["has_inactive"] is True

    @pytest.mark.integration
    def test_has_inactive_false_for_empty_set(self, db, tmp_path):
        """Guarantee: has_inactive is false for a set with no members."""
        create_function_set(db, "no_members", None, [])
        result = list_function_sets(db)
        assert result[0]["has_inactive"] is False
