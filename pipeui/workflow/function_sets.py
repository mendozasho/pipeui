"""Workflow functions for function set create and list — Phase D2.

§1: function_set uses dual-id identity (set_id uuid4 surrogate + content_hash_id uuid5).
§2: content_hash_id = uuid5(function_set namespace, set_name).
§3: Principle 1 collision rule — duplicate set_name → reject as FailedRegistryEntry.
Map rows (function_set_map) are written directly; set_map_id = uuid5(namespace, set_id|function_id).
"""
from __future__ import annotations

import duckdb

from pipeui.ids import content_hash_id, new_id
from pipeui.validation.fails import FailedRegistryEntry

_SET_TABLE = "function_set"
_MAP_TABLE = "function_set_map"


def _set_hash(set_name: str) -> str:
    return str(content_hash_id(_SET_TABLE, set_name))


def _map_id(set_id: str, function_id: str) -> str:
    return str(content_hash_id(_MAP_TABLE, set_id, function_id))


def create_function_set(
    conn: duckdb.DuckDBPyConnection,
    set_name: str,
    set_description: str | None,
    members: list[str],
) -> dict | FailedRegistryEntry:
    """Create a new function set with ordered member functions.

    Returns the created set summary dict on success, or FailedRegistryEntry on
    duplicate set_name or any other write failure.

    members is an ordered list of function_id strings; positions are 0-based.
    set_map_id is structurally unique per (set_id, function_id) pair.
    """
    set_id = str(new_id())
    hash_id = _set_hash(set_name)

    failed = FailedRegistryEntry()

    # Collision check: set_name already exists on a different set_id
    existing = conn.execute(
        "SELECT set_id FROM function_set WHERE content_hash_id = ?", [hash_id]
    ).fetchone()
    if existing:
        failed.add(set_name, f"A function set named '{set_name}' already exists.")
        return failed

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO function_set (set_id, content_hash_id, set_name, set_description) "
            "VALUES (?, ?, ?, ?)",
            [set_id, hash_id, set_name, set_description],
        )
        for position, function_id in enumerate(members):
            conn.execute(
                "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) "
                "VALUES (?, ?, ?, ?)",
                [_map_id(set_id, function_id), set_id, function_id, position],
            )
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        failed.add(set_name, str(exc))
        return failed

    return _set_summary(conn, set_id)


def list_function_sets(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return all function sets as summary dicts, ordered by set_name.

    Each summary includes: set_id, set_name, set_description, member_count,
    has_inactive (true when any member function has is_active = false).
    """
    rows = conn.execute("""
        SELECT
            fs.set_id,
            fs.set_name,
            fs.set_description,
            COUNT(fsm.function_id)                                          AS member_count,
            COALESCE(BOOL_OR(fr.is_active = false), false)                  AS has_inactive
        FROM function_set fs
        LEFT JOIN function_set_map fsm ON fs.set_id = fsm.set_id
        LEFT JOIN function_registry fr ON fsm.function_id = fr.function_id
        GROUP BY fs.set_id, fs.set_name, fs.set_description
        ORDER BY fs.set_name
    """).fetchall()

    return [
        {
            "set_id": str(r[0]),
            "set_name": r[1],
            "set_description": r[2],
            "member_count": r[3],
            "has_inactive": bool(r[4]),
        }
        for r in rows
    ]


def _set_summary(conn: duckdb.DuckDBPyConnection, set_id: str) -> dict:
    """Return a single set summary by set_id (used after create)."""
    rows = list_function_sets(conn)
    for r in rows:
        if r["set_id"] == set_id:
            return r
    # Fallback: return minimal dict if somehow not found (shouldn't happen)
    return {"set_id": set_id}
