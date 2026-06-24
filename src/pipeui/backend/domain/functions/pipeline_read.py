"""Pipeline read/serialize (functions domain).

get_pipeline(conn, source_id)
    Returns the committed pipeline state for a source as the API wire dict — the
    source's columns plus its ordered steps (function sets + their params/bindings,
    interleaved with placed built-in steps), ordered by position.

Split out of ``attach.py`` (#46): the read/serialize seam. Pure read — no writes,
no transaction. The API layer (`middleware/pipelines.py`) calls this to draw the
Report Builder canvas.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import uuid

import duckdb

from pipeui.backend.data.runner.step_loader import get_builtin_steps


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
                  { param_id, param_name, param_type, bindings: [...],
                    scalar_value: "<literal>" | None }
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
                        ORDER BY am.position
                        """,
                        [p_id, source_id],
                    ).fetchall()
                    bindings = [
                        {"column_id": str(b[0]), "column_name": b[1]}
                        for b in binding_rows
                    ]

                # Persisted scalar / plain-string literal for this param, if any
                # (Bug #186). Lets a placed-step card render "= <value>" instead
                # of "unbound" for scalar params.
                scalar_row = conn.execute(
                    "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
                    [source_id, p_id],
                ).fetchone()
                scalar_value = scalar_row[0] if scalar_row else None

                params.append({
                    "param_id": str(p_id),
                    "param_name": p_name,
                    "param_type": p_type,
                    "bindings": bindings,
                    "scalar_value": scalar_value,
                })

            # Per-function output config (#264 / param-binding-output-mode #104): each
            # member's output_mode / append_name / ordered replace targets, so the
            # Builder can render a per-function Append/Replace control that round-trips
            # the persisted form (Principle 7). Legacy steps without a config row fall
            # back to the set-level output_mode.
            cfg_row = conn.execute(
                "SELECT output_mode, append_name FROM function_output_config "
                "WHERE source_function_map_id = ? AND function_id = ?",
                [sfm_id, fn_id],
            ).fetchone()
            fn_output_mode = cfg_row[0] if cfg_row else output_mode
            fn_append_name = cfg_row[1] if cfg_row else None
            target_rows = conn.execute(
                """
                SELECT cr.column_id, cr.column_name
                FROM output_target_map otm
                JOIN column_registry cr ON cr.column_id = otm.column_id
                WHERE otm.source_function_map_id = ? AND otm.function_id = ?
                ORDER BY otm.position
                """,
                [sfm_id, fn_id],
            ).fetchall()
            output_targets = [
                {"column_id": str(r[0]), "column_name": r[1]} for r in target_rows
            ]

            functions.append({
                "function_id": str(fn_id),
                "function_name": fn_name,
                "function_doc": fn_doc,
                "function_type": fn_type,
                "params": params,
                "output_mode": fn_output_mode,
                "append_name": fn_append_name,
                "output_targets": output_targets,
            })

        steps.append({
            "step_type": "function",
            "source_function_map_id": str(sfm_id),
            "set_id": str(set_id),
            "set_name": set_name,
            "position": position,
            "output_mode": output_mode,
            "functions": functions,
        })

    # 6. Append placed built-in steps — #209. Each carries
    #    step_type="builtin", builtin_type, builtin_config, position. The canvas
    #    dispatches on step_type; built-in steps interleave by position.
    #    get_builtin_steps now produces the typed BuiltinStepContext carrier; this
    #    API-response builder serializes each back to the wire dict shape.
    for bstep in get_builtin_steps(conn, source_id):
        steps.append({
            "step_id": bstep.step_id,
            "step_type": "builtin",
            "builtin_type": bstep.builtin_type,
            "builtin_config": bstep.builtin_config,
            "position": bstep.position,
        })

    # Order the unified list by position; tie-break on set_name/builtin_type for
    # determinism (mirrors get_unified_pipeline's sort key). #40: a rename built-in is
    # pinned last (it runs on the final output), so it sorts after everything else on
    # the canvas regardless of position — matching the execution order in run.py.
    steps.sort(key=lambda s: (
        1 if s.get("builtin_type") == "rename" else 0,
        s["position"],
        s.get("set_name") or s.get("builtin_type") or "",
    ))

    return {
        "source": {
            "source_id": str(src_row[0]),
            "source_name": src_row[1],
            "columns": columns,
        },
        "steps": steps,
    }
