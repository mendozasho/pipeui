"""Pipeline read and attach workflow — get_pipeline / attach_function.

get_pipeline returns the committed pipeline state for a source.
attach_function writes source_function_map + alias_map atomically.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import duckdb

from pipeui.ids import content_hash_id, new_id


# ---------------------------------------------------------------------------
# Attach data structures
# ---------------------------------------------------------------------------

@dataclass
class AttachBinding:
    param_id: uuid.UUID
    column_ids: list[uuid.UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# attach_function
# ---------------------------------------------------------------------------

# param_types that require ≥1 alias_map binding
_REQUIRES_BINDING = {"str", "column_backed", "pd.Series"}


def attach_function(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    bindings: list[AttachBinding],
    *,
    function_id: uuid.UUID | None = None,
    set_id: uuid.UUID | None = None,
) -> dict:
    """Attach a function or function set to a source, writing map rows atomically.

    Exactly one of function_id or set_id must be provided.

    When function_id is given the backend auto-creates a function_set row named
    after the function (reusing any existing single-function set for this function)
    and a function_set_map row at position 0.

    Returns either:
      { "ok": True, "source_function_map_id": "<uuid>" }
    or a structured failure dict when validation fails (not an exception):
      { "ok": False, "missing_params": [...], "detail": "<msg>" }
    """
    if (function_id is None) == (set_id is None):
        return {"ok": False, "missing_params": [], "detail": "Provide exactly one of function_id or set_id"}

    # --- Resolve set_id and collect param rows ---
    _auto_set = None  # tuple written inside transaction when we create a new set

    if function_id is not None:
        fn_row = conn.execute(
            "SELECT function_name FROM function_registry WHERE function_id = ?",
            [function_id],
        ).fetchone()
        if fn_row is None:
            return {"ok": False, "missing_params": [], "detail": f"function_id {function_id!r} not found"}
        fn_name = fn_row[0]

        # Reuse existing single-function set for this function if one exists
        existing_set = conn.execute(
            """
            SELECT fs.set_id FROM function_set fs
            JOIN function_set_map fsm ON fsm.set_id = fs.set_id
            WHERE fsm.function_id = ?
              AND (SELECT COUNT(*) FROM function_set_map WHERE set_id = fs.set_id) = 1
            LIMIT 1
            """,
            [function_id],
        ).fetchone()

        if existing_set:
            resolved_set_id = existing_set[0]
        else:
            resolved_set_id = new_id()
            set_ch = content_hash_id("function_set", str(resolved_set_id))
            set_map_id = new_id()
            _auto_set = (resolved_set_id, set_ch, fn_name, None, set_map_id, function_id)

        # Params for this single function
        param_rows = conn.execute(
            "SELECT param_id, param_name, param_type FROM parameter WHERE function_id = ?",
            [function_id],
        ).fetchall()

    else:
        # Verify set exists
        if conn.execute("SELECT 1 FROM function_set WHERE set_id = ?", [set_id]).fetchone() is None:
            return {"ok": False, "missing_params": [], "detail": f"set_id {set_id!r} not found"}
        resolved_set_id = set_id

        # Params for all functions in the set
        param_rows = conn.execute(
            """
            SELECT p.param_id, p.param_name, p.param_type
            FROM parameter p
            JOIN function_set_map fsm ON fsm.function_id = p.function_id
            WHERE fsm.set_id = ?
            """,
            [set_id],
        ).fetchall()

    # --- Build binding lookup: param_id -> [column_id, ...] ---
    binding_map: dict[uuid.UUID, list[uuid.UUID]] = {b.param_id: list(b.column_ids) for b in bindings}

    # --- Validate required bindings ---
    missing = [
        {"param_id": str(p_id), "param_name": p_name, "param_type": p_type}
        for p_id, p_name, p_type in param_rows
        if p_type in _REQUIRES_BINDING and not binding_map.get(p_id)
    ]
    if missing:
        return {"ok": False, "missing_params": missing, "detail": "Missing required column bindings"}

    # --- Write atomically ---
    sfm_id = new_id()

    conn.execute("BEGIN")
    try:
        if _auto_set is not None:
            rs_id, rs_ch, rs_name, rs_desc, rs_map_id, rs_fn_id = _auto_set
            conn.execute(
                "INSERT INTO function_set (set_id, content_hash_id, set_name, set_description) VALUES (?, ?, ?, ?)",
                [rs_id, rs_ch, rs_name, rs_desc],
            )
            conn.execute(
                "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) VALUES (?, ?, ?, ?)",
                [rs_map_id, rs_id, rs_fn_id, 0],
            )

        conn.execute(
            "INSERT INTO source_function_map (source_function_map_id, source_id, set_id) VALUES (?, ?, ?)",
            [sfm_id, source_id, resolved_set_id],
        )

        for p_id, p_name, p_type in param_rows:
            if p_type in _REQUIRES_BINDING:
                for col_id in binding_map.get(p_id, []):
                    am_id = content_hash_id("alias_map", str(p_id), str(col_id), str(source_id))
                    conn.execute(
                        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
                        [am_id, col_id, p_id, source_id],
                    )

        conn.execute("COMMIT")
        return {"ok": True, "source_function_map_id": str(sfm_id)}

    except Exception as exc:
        conn.execute("ROLLBACK")
        return {"ok": False, "missing_params": [], "detail": f"Transaction failed: {exc}"}


# ---------------------------------------------------------------------------
# get_pipeline
# ---------------------------------------------------------------------------

def get_pipeline(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> dict | None:
    """Return the full pipeline state for a source.

    Returns None when the source_id is not in source_registry.

    Response shape:
      {
        "source": { source_id, source_name, columns: [...] },
        "steps": [
          {
            source_function_map_id,
            set_id,
            set_name,
            position,
            functions: [
              {
                function_id, function_name, function_doc, function_type,
                params: [
                  { param_id, param_name, param_type, bindings: [...] }
                ]
              }
            ]
          }
        ]
      }

    Steps are ordered by the minimum position of their functions in
    function_set_map.  pd.DataFrame params carry an empty bindings list
    (implicitly bound to the full source table).
    """
    # 1. Verify source exists
    src_row = conn.execute(
        "SELECT source_id, source_name FROM source_registry WHERE source_id = ?",
        [source_id],
    ).fetchone()
    if src_row is None:
        return None

    # 2. Fetch source columns
    col_rows = conn.execute(
        """
        SELECT cr.column_id, cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()

    columns = [
        {"column_id": str(r[0]), "column_name": r[1], "column_type": r[2]}
        for r in col_rows
    ]

    # 3. Fetch attached sets (via source_function_map → function_set)
    #    Order by the minimum position of any function in the set.
    set_rows = conn.execute(
        """
        SELECT
            sfm.source_function_map_id,
            fs.set_id,
            fs.set_name,
            MIN(fsmap.position) AS position
        FROM source_function_map sfm
        JOIN function_set fs ON fs.set_id = sfm.set_id
        LEFT JOIN function_set_map fsmap ON fsmap.set_id = fs.set_id
        WHERE sfm.source_id = ?
        GROUP BY sfm.source_function_map_id, fs.set_id, fs.set_name
        ORDER BY position NULLS LAST, fs.set_name
        """,
        [source_id],
    ).fetchall()

    steps = []
    for sfm_id, set_id, set_name, position in set_rows:
        # 4. Fetch functions in this set, ordered by position
        fn_rows = conn.execute(
            """
            SELECT
                fr.function_id,
                fr.function_name,
                fr.function_doc,
                fr.function_type,
                fsmap.position
            FROM function_set_map fsmap
            JOIN function_registry fr ON fr.function_id = fsmap.function_id
            WHERE fsmap.set_id = ?
            ORDER BY fsmap.position
            """,
            [set_id],
        ).fetchall()

        functions = []
        for fn_id, fn_name, fn_doc, fn_type, fn_pos in fn_rows:
            # 5. Fetch params for this function
            param_rows = conn.execute(
                """
                SELECT param_id, param_name, param_type
                FROM parameter
                WHERE function_id = ?
                ORDER BY param_name
                """,
                [fn_id],
            ).fetchall()

            params = []
            for p_id, p_name, p_type in param_rows:
                # pd.DataFrame params are implicitly bound to the full source table
                if p_type == "pd.DataFrame":
                    bindings = []
                else:
                    # Look up alias_map bindings for this param + source
                    binding_rows = conn.execute(
                        """
                        SELECT cr.column_id, cr.column_name
                        FROM alias_map am
                        JOIN column_registry cr ON cr.column_id = am.column_id
                        WHERE am.parameter_id = ? AND am.source_id = ?
                        ORDER BY cr.column_name
                        """,
                        [p_id, source_id],
                    ).fetchall()
                    bindings = [
                        {"column_id": str(b[0]), "column_name": b[1]}
                        for b in binding_rows
                    ]

                params.append({
                    "param_id": str(p_id),
                    "param_name": p_name,
                    "param_type": p_type,
                    "bindings": bindings,
                })

            functions.append({
                "function_id": str(fn_id),
                "function_name": fn_name,
                "function_doc": fn_doc,
                "function_type": fn_type,
                "params": params,
            })

        steps.append({
            "source_function_map_id": str(sfm_id),
            "set_id": str(set_id),
            "set_name": set_name,
            "position": position,
            "functions": functions,
        })

    return {
        "source": {
            "source_id": str(src_row[0]),
            "source_name": src_row[1],
            "columns": columns,
        },
        "steps": steps,
    }
