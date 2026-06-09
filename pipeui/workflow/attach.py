"""Pipeline read, attach, suggest, and detach workflows.

get_pipeline(conn, source_id)
    Returns the committed pipeline state for a source.

attach_function(conn, source_id, bindings, *, function_id, set_id)
    Writes source_function_map + alias_map atomically.

suggest_bindings(conn, source_id, *, function_id, set_id)
    Dry-run: returns per-parameter column suggestions without writing any rows.

detach_function(conn, source_id, source_function_map_id)
    Removes a step atomically: alias_map rows + source_function_map row, and
    optionally the auto-created function_set + function_set_map rows when no
    other references remain and the set is auto-created.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

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
    output_mode: str = "append",
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

        # Compute position = MAX(position) + 1 for this source, or 0
        pos_row = conn.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM source_function_map WHERE source_id = ?",
            [source_id],
        ).fetchone()
        position = pos_row[0] if pos_row else 0

        conn.execute(
            "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode) VALUES (?, ?, ?, ?, ?)",
            [sfm_id, source_id, resolved_set_id, position, output_mode],
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
            sfm.position,
            sfm.output_mode
        FROM source_function_map sfm
        JOIN function_set fs ON fs.set_id = sfm.set_id
        WHERE sfm.source_id = ?
        ORDER BY sfm.position ASC, fs.set_name
        """,
        [source_id],
    ).fetchall()

    steps = []
    for sfm_id, set_id, set_name, position, output_mode in set_rows:
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
            "output_mode": output_mode,
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


# ---------------------------------------------------------------------------
# suggest_bindings — dry-run column suggestion
# ---------------------------------------------------------------------------

def _params_for_set(
    conn: duckdb.DuckDBPyConnection,
    set_id: uuid.UUID,
) -> list[tuple]:
    """Return (param_id, param_name, param_type) rows for all functions in a set."""
    return conn.execute(
        """
        SELECT p.param_id, p.param_name, p.param_type
        FROM function_set_map fsmap
        JOIN parameter p ON p.function_id = fsmap.function_id
        WHERE fsmap.set_id = ?
        ORDER BY p.param_name
        """,
        [set_id],
    ).fetchall()


def _params_for_function(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID,
) -> list[tuple]:
    """Return (param_id, param_name, param_type) rows for a single function."""
    return conn.execute(
        """
        SELECT param_id, param_name, param_type
        FROM parameter
        WHERE function_id = ?
        ORDER BY param_name
        """,
        [function_id],
    ).fetchall()


# param_types eligible for column binding suggestions
_SUGGEST_TYPES = {"str", "pd.Series"}


def suggest_bindings(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    *,
    function_id: Optional[uuid.UUID] = None,
    set_id: Optional[uuid.UUID] = None,
) -> dict:
    """Return column binding suggestions for a function or set without writing rows.

    Exactly one of function_id / set_id must be provided.

    Response shape:
      {
        "params": [
          {
            "param_id": "...",
            "param_name": "...",
            "param_type": "...",
            "suggested_columns": [{ "column_id": "...", "column_name": "..." }]
          }
        ]
      }

    scalar and pd.DataFrame params are excluded.
    suggested_columns contains all target-source columns whose column_id appears
    in any prior alias_map binding for the same parameter_id on *other* sources.
    All matches are returned (no tiebreaker — alias_map has no reliable ordering).
    """
    if (function_id is None) == (set_id is None):
        raise ValueError("Exactly one of function_id or set_id must be provided")

    # 1. Collect params, filter to eligible types only
    if set_id is not None:
        raw_params = _params_for_set(conn, set_id)
    else:
        raw_params = _params_for_function(conn, function_id)

    eligible_params = [
        (p_id, p_name, p_type)
        for p_id, p_name, p_type in raw_params
        if p_type in _SUGGEST_TYPES
    ]

    if not eligible_params:
        return {"params": []}

    # 2. Collect column_ids on the target source
    target_col_rows = conn.execute(
        """
        SELECT cr.column_id, cr.column_name
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        """,
        [source_id],
    ).fetchall()
    target_col_map: dict[str, str] = {str(r[0]): r[1] for r in target_col_rows}

    # 3. For each eligible param, find prior bindings on *other* sources whose
    #    column_id exists in the target source.
    result_params = []
    for p_id, p_name, p_type in eligible_params:
        prior_rows = conn.execute(
            """
            SELECT DISTINCT am.column_id
            FROM alias_map am
            WHERE am.parameter_id = ?
              AND am.source_id <> ?
            """,
            [p_id, source_id],
        ).fetchall()

        suggested = []
        for (col_id,) in prior_rows:
            col_id_str = str(col_id)
            if col_id_str in target_col_map:
                suggested.append({
                    "column_id": col_id_str,
                    "column_name": target_col_map[col_id_str],
                })

        # Sort for deterministic output
        suggested.sort(key=lambda c: c["column_name"])

        result_params.append({
            "param_id": str(p_id),
            "param_name": p_name,
            "param_type": p_type,
            "suggested_columns": suggested,
        })

    return {"params": result_params}



# ---------------------------------------------------------------------------
# patch_pipeline_step — update position and/or output_mode
# ---------------------------------------------------------------------------

_VALID_OUTPUT_MODES = {"append", "replace"}


def patch_pipeline_step(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    source_function_map_id: uuid.UUID,
    *,
    position: int | None = None,
    output_mode: str | None = None,
) -> bool:
    """Update position and/or output_mode on a source_function_map row.

    Returns True on success, False when the row is not found or doesn't
    belong to source_id (caller surfaces a 404).

    Raises ValueError when output_mode is not a valid value.
    """
    if output_mode is not None and output_mode not in _VALID_OUTPUT_MODES:
        raise ValueError(f"output_mode must be one of {sorted(_VALID_OUTPUT_MODES)!r}; got {output_mode!r}")

    row = conn.execute(
        "SELECT source_function_map_id FROM source_function_map WHERE source_function_map_id = ? AND source_id = ?",
        [source_function_map_id, source_id],
    ).fetchone()
    if row is None:
        return False

    if position is not None:
        conn.execute(
            "UPDATE source_function_map SET position = ? WHERE source_function_map_id = ?",
            [position, source_function_map_id],
        )
    if output_mode is not None:
        conn.execute(
            "UPDATE source_function_map SET output_mode = ? WHERE source_function_map_id = ?",
            [output_mode, source_function_map_id],
        )
    return True

def detach_function(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    source_function_map_id: uuid.UUID,
) -> bool:
    """Remove a pipeline step atomically.

    One transaction:
      1. Verify the source_function_map row exists and belongs to source_id.
      2. Delete alias_map rows where source_id matches and parameter_id belongs
         to a function in the referenced set.
      3. Delete the source_function_map row.
      4. If no other source_function_map row references this set_id, AND the set
         is auto-created (exactly one member in function_set_map AND set_name
         equals that member function's function_name), delete function_set +
         function_set_map rows. When uncertain, skip — an orphan is safer.

    Returns True on success, False when the row is not found or doesn't belong
    to source_id (caller should surface a 404).
    """
    # Look up the map row and its set_id
    row = conn.execute(
        """
        SELECT sfm.set_id
        FROM source_function_map sfm
        WHERE sfm.source_function_map_id = ? AND sfm.source_id = ?
        """,
        [source_function_map_id, source_id],
    ).fetchone()
    if row is None:
        return False

    set_id = row[0]

    conn.execute("BEGIN")
    try:
        # 1. Delete alias_map rows for this source whose parameter belongs to a
        #    function in the set.
        conn.execute(
            """
            DELETE FROM alias_map
            WHERE source_id = ?
              AND parameter_id IN (
                SELECT p.param_id
                FROM parameter p
                JOIN function_set_map fsm ON fsm.function_id = p.function_id
                WHERE fsm.set_id = ?
              )
            """,
            [source_id, set_id],
        )

        # 2. Delete the source_function_map row itself.
        conn.execute(
            "DELETE FROM source_function_map WHERE source_function_map_id = ?",
            [source_function_map_id],
        )

        # 3. Conditionally clean up the set if auto-created and now unreferenced.
        remaining = conn.execute(
            "SELECT COUNT(*) FROM source_function_map WHERE set_id = ?",
            [set_id],
        ).fetchone()[0]

        if remaining == 0:
            # Check auto-created heuristic: exactly one function in set_map,
            # and set_name equals that function's function_name.
            member_rows = conn.execute(
                """
                SELECT fr.function_name
                FROM function_set_map fsm
                JOIN function_registry fr ON fr.function_id = fsm.function_id
                WHERE fsm.set_id = ?
                """,
                [set_id],
            ).fetchall()

            if len(member_rows) == 1:
                member_function_name = member_rows[0][0]
                set_name_row = conn.execute(
                    "SELECT set_name FROM function_set WHERE set_id = ?",
                    [set_id],
                ).fetchone()
                if set_name_row and set_name_row[0] == member_function_name:
                    # Auto-created set with no remaining references — clean up.
                    conn.execute(
                        "DELETE FROM function_set_map WHERE set_id = ?",
                        [set_id],
                    )
                    conn.execute(
                        "DELETE FROM function_set WHERE set_id = ?",
                        [set_id],
                    )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return True
