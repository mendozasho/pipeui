"""Function read-API (functions domain) — registry read/serialize.

get_function(conn, function_id)
    Full detail for one function: its function_registry fields, parameter list, and
    attached_sources. None when unknown.

list_functions(conn)
    All function_registry rows + their parameters, ordered by function_name.

Split out of ``registration.py`` (#47): the read seam (mirrors ``pipeline_read`` in the
attach split). Pure read — no transaction, no discovery, no classification.
"""
from __future__ import annotations

import duckdb


def get_function(conn: duckdb.DuckDBPyConnection, function_id: str) -> dict | None:
    """Return full detail for one function, or None if not found.

    Includes all function_registry fields, parameter list, and attached_sources
    (joined from source_function_map → source_registry).
    """
    row = conn.execute(
        """
        SELECT function_id, content_hash_id, function_class, function_name,
               function_doc, function_return_type, function_signature,
               function_type, module_path, is_active
        FROM function_registry
        WHERE function_id = ?
        """,
        [function_id],
    ).fetchone()

    if row is None:
        return None

    col_names = [
        "function_id", "content_hash_id", "function_class", "function_name",
        "function_doc", "function_return_type", "function_signature",
        "function_type", "module_path", "is_active",
    ]
    record = dict(zip(col_names, row))
    record["function_id"] = str(record["function_id"])
    record["content_hash_id"] = str(record["content_hash_id"])

    params = conn.execute(
        """
        SELECT param_id, param_name, param_type
        FROM parameter
        WHERE function_id = ?
        ORDER BY param_name
        """,
        [record["function_id"]],
    ).fetchall()
    record["parameters"] = [
        {"param_id": str(p[0]), "param_name": p[1], "param_type": p[2]}
        for p in params
    ]

    sources = conn.execute(
        """
        SELECT DISTINCT sr.source_id, sr.source_name
        FROM source_function_map sfm
        JOIN function_set_map fsm ON fsm.set_id = sfm.set_id
        JOIN source_registry sr ON sr.source_id = sfm.source_id
        WHERE fsm.function_id = ?
        ORDER BY sr.source_name
        """,
        [record["function_id"]],
    ).fetchall()
    record["attached_sources"] = [
        {"source_id": str(s[0]), "source_name": s[1]}
        for s in sources
    ]

    return record


def list_functions(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return all function_registry rows with their parameter rows, ordered by function_name."""
    rows = conn.execute(
        """
        SELECT function_id, content_hash_id, function_class, function_name,
               function_doc, function_return_type, function_signature,
               function_type, module_path, is_active
        FROM function_registry
        ORDER BY function_name
        """
    ).fetchall()

    col_names = [
        "function_id", "content_hash_id", "function_class", "function_name",
        "function_doc", "function_return_type", "function_signature",
        "function_type", "module_path", "is_active",
    ]

    results = []
    for row in rows:
        record = dict(zip(col_names, row))
        record["function_id"] = str(record["function_id"])
        record["content_hash_id"] = str(record["content_hash_id"])

        params = conn.execute(
            """
            SELECT param_id, param_name, param_type
            FROM parameter
            WHERE function_id = ?
            ORDER BY param_name
            """,
            [record["function_id"]],
        ).fetchall()
        record["parameters"] = [
            {"param_id": str(p[0]), "param_name": p[1], "param_type": p[2]}
            for p in params
        ]
        results.append(record)

    return results
