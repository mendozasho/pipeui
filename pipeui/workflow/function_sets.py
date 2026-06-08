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
    return {"set_id": set_id}


def get_function_set(conn: duckdb.DuckDBPyConnection, set_id: str) -> dict | None:
    """Return full detail for one function set including ordered members, or None if not found.

    Members are returned ORDER BY position ASC with function_name, function_type, is_active.
    """
    row = conn.execute(
        "SELECT set_id, set_name, set_description FROM function_set WHERE set_id = ?",
        [set_id],
    ).fetchone()
    if row is None:
        return None

    members = conn.execute("""
        SELECT
            fsm.function_id,
            fr.function_name,
            fr.function_type,
            fr.is_active,
            fsm.position
        FROM function_set_map fsm
        JOIN function_registry fr ON fsm.function_id = fr.function_id
        WHERE fsm.set_id = ?
        ORDER BY fsm.position ASC
    """, [set_id]).fetchall()

    return {
        "set_id": str(row[0]),
        "set_name": row[1],
        "set_description": row[2],
        "members": [
            {
                "function_id": str(m[0]),
                "function_name": m[1],
                "function_type": m[2],
                "is_active": bool(m[3]),
                "position": m[4],
            }
            for m in members
        ],
    }


def update_function_set(
    conn: duckdb.DuckDBPyConnection,
    set_id: str,
    set_name: str | None = None,
    set_description: str | None = None,
    members: list[str] | None = None,
    clear_description: bool = False,
) -> dict | FailedRegistryEntry:
    """Update an existing function set.

    Returns full detail dict on success, FailedRegistryEntry on collision or error.

    - set_name: if provided, updates name and recomputes content_hash_id; collision → reject
    - set_description: if provided, updates description; use clear_description=True to set NULL
    - members: if provided, replace-members (delete all existing map rows, reinsert in order)
      All changes commit in one transaction.
    """
    existing = conn.execute(
        "SELECT set_id, set_name, set_description FROM function_set WHERE set_id = ?",
        [set_id],
    ).fetchone()
    if existing is None:
        return None  # caller converts to 404

    current_name = existing[1]
    new_name = set_name if set_name is not None else current_name
    new_hash = _set_hash(new_name)

    failed = FailedRegistryEntry()

    # Collision check: new name already used by a different set
    if new_name != current_name:
        collision = conn.execute(
            "SELECT set_id FROM function_set WHERE content_hash_id = ? AND set_id != ?",
            [new_hash, set_id],
        ).fetchone()
        if collision:
            failed.add(set_name, f"A function set named '{new_name}' already exists.")
            return failed

    try:
        conn.execute("BEGIN")

        # Update registry fields
        conn.execute(
            "UPDATE function_set SET set_name = ?, content_hash_id = ?, set_description = ? WHERE set_id = ?",
            [
                new_name,
                new_hash,
                None if clear_description else (set_description if set_description is not None else existing[2]),
                set_id,
            ],
        )

        # Replace members if provided
        if members is not None:
            conn.execute("DELETE FROM function_set_map WHERE set_id = ?", [set_id])
            for position, function_id in enumerate(members):
                conn.execute(
                    "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) VALUES (?, ?, ?, ?)",
                    [_map_id(set_id, function_id), set_id, function_id, position],
                )

        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        failed.add(set_id, str(exc))
        return failed

    return get_function_set(conn, set_id)
