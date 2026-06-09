"""Pipeline read workflow — get_pipeline(conn, source_id).

Returns the committed pipeline state for a source: source metadata with its
columns, and an ordered list of steps (function sets) with full param + binding
detail.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import uuid

import duckdb


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
