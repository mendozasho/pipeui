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

import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

import duckdb

from pipeui.backend.data.base.ids import content_hash_id, new_id
from pipeui.backend.data.base.results import normalize_label
from pipeui.backend.data.runner.bundles import BundleLengthError, pair_bundles
from pipeui.backend.data.runner.step_loader import get_builtin_steps


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
    scalar_values: dict[uuid.UUID, str] | None = None,
    output_targets: list[uuid.UUID] | None = None,
    append_name: str | None = None,
) -> dict:
    """Attach a function or function set to a source, writing map rows atomically.

    Exactly one of function_id or set_id must be provided.

    When function_id is given the backend auto-creates a function_set row named
    after the function (reusing any existing single-function set for this function)
    and a function_set_map row at position 0.

    scalar_values: optional param_id -> literal value (VARCHAR). Used for scalar
    params (int/float/bool) and for str params in "plain string" mode. When a
    literal value is provided for a ``str`` param it is EXEMPT from the column
    binding requirement (Bug #186): the literal is persisted to
    source_scalar_map and no alias_map row is written for that param. Values are
    persisted inside the same transaction as the map rows.

    append_name: optional user-provided name for the column an append-mode transform
    adds. Normalized (normalize_label) and persisted to
    source_function_map.append_name only when output_mode is "append" and the name
    is non-blank (NULL otherwise); the runner reads the already-clean value verbatim
    to name the appended column, falling back to the cleaned auto-label when NULL.

    Returns either:
      { "ok": True, "source_function_map_id": "<uuid>" }
    or a structured failure dict when validation fails (not an exception):
      { "ok": False, "missing_params": [...], "detail": "<msg>" }
    """
    scalar_values = scalar_values or {}
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
    # A `str` param is exempt from the binding requirement when a literal value
    # is provided for it (plain-string mode, Bug #186): the literal goes to
    # source_scalar_map instead of alias_map. Other binding types (column_backed,
    # pd.Series) still require a column binding.
    def _has_literal(p_id) -> bool:
        v = scalar_values.get(p_id)
        return v is not None and str(v).strip() != ""

    missing = [
        {"param_id": str(p_id), "param_name": p_name, "param_type": p_type}
        for p_id, p_name, p_type in param_rows
        if p_type in _REQUIRES_BINDING
        and not binding_map.get(p_id)
        and not (p_type == "str" and _has_literal(p_id))
    ]
    if missing:
        return {"ok": False, "missing_params": missing, "detail": "Missing required column bindings"}

    # --- Equal-length-among-varying guard (slice 3 / §12, ADR-0001) ---
    # Validate the argument-bundle shape *before* writing: every varying parameter
    # (>1 bound column) must share one length N; a single-column param broadcasts.
    # Two distinct lengths among varying params is rejected as a structured failure
    # here — never a silent zip-shortest truncation and never a 500 at run time.
    bundle_params = [
        {"param_id": str(p_id), "columns": [str(c) for c in binding_map.get(p_id, [])]}
        for p_id, _, _ in param_rows
        if binding_map.get(p_id)
    ]
    try:
        bundles = pair_bundles(bundle_params)
    except BundleLengthError as exc:
        return {"ok": False, "missing_params": [], "detail": str(exc)}

    # --- Output-target count guard (slice 4 / #240, ADR-0001 output-target map) ---
    # A `replace` step may carry an explicit ordered set of target columns (bundle i
    # overwrites target i). The target count MUST equal the bundle count, or the
    # attach is rejected as a structured failure here — never a 500 at run time and
    # never a partial write. Empty/absent targets are allowed (single-varying default
    # resolves the input column at run time).
    output_targets = output_targets or []
    bundle_count = len(bundles)
    if output_targets and len(output_targets) != bundle_count:
        return {
            "ok": False,
            "missing_params": [],
            "detail": (
                f"Replace target count ({len(output_targets)}) must equal the bundle "
                f"count ({bundle_count}); one target column per argument bundle."
            ),
        }

    # Resolve the function whose output these targets bind. When attaching a bare
    # function_id it is that function; for a set, the single transform function in it
    # (output targets are a per-transform-function concept).
    target_function_id = function_id
    if target_function_id is None and output_targets:
        tf_row = conn.execute(
            """
            SELECT fr.function_id
            FROM function_set_map fsm
            JOIN function_registry fr ON fr.function_id = fsm.function_id
            WHERE fsm.set_id = ? AND fr.function_type = 'transform'
            ORDER BY fsm.position
            LIMIT 1
            """,
            [resolved_set_id],
        ).fetchone()
        target_function_id = tf_row[0] if tf_row else None

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

        # append_name is meaningful only for append mode (the runtime reads it to
        # name the new column); store NULL for replace so the auto-label path stays.
        # Normalize the user-provided name at persistence time so the stored value
        # is already a clean column name — the runtime consumes it verbatim.
        persisted_append_name = (
            normalize_label(append_name)
            if output_mode == "append" and append_name is not None and append_name.strip()
            else None
        )
        conn.execute(
            "INSERT INTO source_function_map (source_function_map_id, source_id, set_id, position, output_mode, append_name) VALUES (?, ?, ?, ?, ?, ?)",
            [sfm_id, source_id, resolved_set_id, position, output_mode, persisted_append_name],
        )

        # #264: per-function output config — output_mode + append_name belong to the
        # attached function, not the whole set. (source_function_map still carries the
        # step-level value as a legacy fallback for rows written before this existed.)
        if target_function_id is not None:
            conn.execute(
                "INSERT INTO function_output_config (source_function_map_id, function_id, output_mode, append_name) VALUES (?, ?, ?, ?)",
                [sfm_id, target_function_id, output_mode, persisted_append_name],
            )

        for p_id, p_name, p_type in param_rows:
            if p_type in _REQUIRES_BINDING:
                # position = add-order: the index of the column in the provided
                # binding list, so argument bundles align by position at run time.
                for pos, col_id in enumerate(binding_map.get(p_id, [])):
                    am_id = content_hash_id("alias_map", str(p_id), str(col_id), str(source_id))
                    conn.execute(
                        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
                        [am_id, col_id, p_id, source_id, pos],
                    )

        # Output-target map rows (slice 4 / #238): ordered target columns for a
        # `replace` step, bundle i -> target i. Written only when targets were
        # provided; `append` steps and replace-with-default-target write none.
        if output_targets and target_function_id is not None:
            for pos, col_id in enumerate(output_targets):
                otm_id = content_hash_id(
                    "output_target_map", str(sfm_id), str(col_id), str(pos)
                )
                conn.execute(
                    "INSERT INTO output_target_map (output_target_map_id, source_function_map_id, function_id, column_id, position) VALUES (?, ?, ?, ?, ?)",
                    [otm_id, sfm_id, target_function_id, col_id, pos],
                )

        # Persist any provided scalar / plain-string literals (Bug #186). Only
        # write non-blank values; a blank means "use the Python default".
        if scalar_values:
            param_id_set = {p_id for p_id, _, _ in param_rows}
            for p_id, value in scalar_values.items():
                if p_id not in param_id_set:
                    continue
                if value is None or str(value).strip() == "":
                    continue
                scalar_id = new_id()
                conn.execute(
                    """
                    INSERT INTO source_scalar_map (scalar_map_id, source_id, param_id, value)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (source_id, param_id) DO UPDATE SET value = excluded.value
                    """,
                    [scalar_id, source_id, p_id, str(value)],
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

            functions.append({
                "function_id": str(fn_id),
                "function_name": fn_name,
                "function_doc": fn_doc,
                "function_type": fn_type,
                "params": params,
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

    # 6. Append placed built-in steps (join/pivot/filter) — #209. Each carries
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
    # determinism (mirrors get_unified_pipeline's sort key).
    steps.sort(key=lambda s: (s["position"], s.get("set_name") or s.get("builtin_type") or ""))

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
    """Return (param_id, param_name, param_type, function_name, function_doc) rows for all functions in a set."""
    return conn.execute(
        """
        SELECT p.param_id, p.param_name, p.param_type, fr.function_name, fr.function_doc
        FROM function_set_map fsmap
        JOIN function_registry fr ON fr.function_id = fsmap.function_id
        JOIN parameter p ON p.function_id = fsmap.function_id
        WHERE fsmap.set_id = ?
        ORDER BY fsmap.position, p.param_name
        """,
        [set_id],
    ).fetchall()


def _params_for_function(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID,
) -> list[tuple]:
    """Return (param_id, param_name, param_type, function_name, function_doc) rows for a single function."""
    return conn.execute(
        """
        SELECT p.param_id, p.param_name, p.param_type, fr.function_name, fr.function_doc
        FROM parameter p
        JOIN function_registry fr ON fr.function_id = p.function_id
        WHERE p.function_id = ?
        ORDER BY p.param_name
        """,
        [function_id],
    ).fetchall()


# param_types eligible for column binding suggestions
# governs column-backed suggestion only — scalar params are included separately below
_SUGGEST_TYPES = {"str", "pd.Series"}

# param_types treated as scalar — rendered as free-text inputs, persisted in source_scalar_map
_SCALAR_TYPES = {"int", "float", "bool"}


def suggest_bindings(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    *,
    function_id: Optional[uuid.UUID] = None,
    set_id: Optional[uuid.UUID] = None,
    step_position: Optional[int] = None,
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
            "param_kind": "column" | "scalar",
            "function_name": "...",
            "function_doc": "...",          # owning function's description ("" if none)
            "suggested_columns": [...],     # empty for scalar params
            "current_bindings": [{ "column_id": "...", "column_name": "..." }],
            "current_scalar_value": "..." | null
          }
        ],
        "available_columns": [
          { "column_id": "...", "column_name": "...", "column_type": "..." }
        ]
      }

    Column params (str, pd.Series) carry param_kind="column". Scalar params
    (int, float, bool) carry param_kind="scalar" with empty suggested_columns.
    pd.DataFrame params are excluded (auto-filled with the full table).

    current_bindings is the param's existing alias_map rows on THIS source — it
    drives edit-modal pre-selection of column checkboxes. current_scalar_value is
    the persisted source_scalar_map value for (source, param) — set for scalar
    params and for a str param used in plain-string mode — or None. Both let a
    re-opened step restore its saved state (#191).

    available_columns is computed from the source's column_registry rows plus
    columns added by join steps already in the pipeline at positions before
    step_position (when provided).

    suggested_columns contains all target-source columns whose column_id appears
    in any prior alias_map binding for the same parameter_id on *other* sources.
    All matches are returned (no tiebreaker — alias_map has no reliable ordering).
    """
    if (function_id is None) == (set_id is None):
        raise ValueError("Exactly one of function_id or set_id must be provided")

    # 1. Collect all params
    if set_id is not None:
        raw_params = _params_for_set(conn, set_id)
    else:
        raw_params = _params_for_function(conn, function_id)

    # 2. Collect column_ids on the target source
    target_col_rows = conn.execute(
        """
        SELECT cr.column_id, cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()
    target_col_map: dict[str, tuple[str, str]] = {str(r[0]): (r[1], r[2]) for r in target_col_rows}

    # 3. Build available_columns: source columns + columns from prior join steps
    available_columns = [
        {"column_id": str(r[0]), "column_name": r[1], "column_type": r[2]}
        for r in target_col_rows
    ]

    # Add columns contributed by join steps at positions before step_position
    if step_position is not None:
        join_step_rows = conn.execute(
            """
            SELECT sbm.builtin_config
            FROM source_builtin_map sbm
            WHERE sbm.source_id = ? AND sbm.builtin_type = 'join' AND sbm.position < ?
            ORDER BY sbm.position
            """,
            [source_id, step_position],
        ).fetchall()
        seen_col_ids = {c["column_id"] for c in available_columns}
        for (config,) in join_step_rows:
            cfg = json.loads(config) if isinstance(config, str) else config
            right_source_id = cfg.get("right_source_id")
            keep = cfg.get("keep_columns", "all")
            if right_source_id and keep != "none":
                right_cols = conn.execute(
                    """
                    SELECT cr.column_id, cr.column_name, cr.column_type
                    FROM column_registry cr
                    JOIN source_column_map scm ON scm.column_id = cr.column_id
                    WHERE scm.source_id = ?
                    ORDER BY cr.column_name
                    """,
                    [right_source_id],
                ).fetchall()
                for rc in right_cols:
                    rc_id = str(rc[0])
                    if rc_id not in seen_col_ids:
                        available_columns.append(
                            {"column_id": rc_id, "column_name": rc[1], "column_type": rc[2]}
                        )
                        seen_col_ids.add(rc_id)

    # 4. Build result params — unified loop over column + scalar params (pd.DataFrame
    #    excluded). Each param carries: suggested_columns (prior bindings on OTHER
    #    sources, column kinds only), current_bindings (this param's alias_map rows on
    #    THIS source — drives edit pre-selection, #191), and current_scalar_value (the
    #    persisted source_scalar_map value for scalars AND str in plain-string mode, #191).
    result_params = []
    for p_id, p_name, p_type, fn_name, fn_doc in raw_params:
        if p_type not in _SUGGEST_TYPES and p_type not in _SCALAR_TYPES:
            continue  # pd.DataFrame — auto-filled with the full table, never user-bound

        kind = "scalar" if p_type in _SCALAR_TYPES else "column"

        # Suggested columns: prior bindings for this param on OTHER sources that
        # also exist on the target source. Column kinds only.
        suggested = []
        if kind == "column":
            prior_rows = conn.execute(
                """
                SELECT DISTINCT am.column_id
                FROM alias_map am
                WHERE am.parameter_id = ?
                  AND am.source_id <> ?
                """,
                [p_id, source_id],
            ).fetchall()
            for (col_id,) in prior_rows:
                col_id_str = str(col_id)
                if col_id_str in target_col_map:
                    col_name, _ = target_col_map[col_id_str]
                    suggested.append({"column_id": col_id_str, "column_name": col_name})
            suggested.sort(key=lambda c: c["column_name"])

        # Current bindings: this param's alias_map rows on THIS source. Restores
        # column selections when an attached step is re-opened for edit (#191).
        current_binding_rows = conn.execute(
            """
            SELECT cr.column_id, cr.column_name
            FROM alias_map am
            JOIN column_registry cr ON cr.column_id = am.column_id
            WHERE am.parameter_id = ? AND am.source_id = ?
            ORDER BY am.position
            """,
            [p_id, source_id],
        ).fetchall()
        current_bindings = [
            {"column_id": str(r[0]), "column_name": r[1]}
            for r in current_binding_rows
        ]

        # Current persisted scalar value (or None) — for scalar params and for a
        # str param used in plain-string mode. Restores the typed value on edit (#191).
        scalar_row = conn.execute(
            "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
            [source_id, p_id],
        ).fetchone()
        current_scalar_value = scalar_row[0] if scalar_row is not None else None

        result_params.append({
            "param_id": str(p_id),
            "param_name": p_name,
            "param_type": p_type,
            "param_kind": kind,
            "function_name": fn_name,
            "function_doc": fn_doc or "",
            "suggested_columns": suggested,
            "current_bindings": current_bindings,
            "current_scalar_value": current_scalar_value,
        })

    return {"params": result_params, "available_columns": available_columns}



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
    bindings: dict[uuid.UUID, list[uuid.UUID]] | None = None,
    scalar_values: dict[uuid.UUID, str] | None = None,
) -> bool:
    """Update position, output_mode, bindings, and/or scalar_values on a pipeline step.

    bindings: param_id -> [column_id, ...]; when present, replaces all alias_map rows
      for this source_function_map in a single transaction.
    scalar_values: param_id -> value string; when present, upserts into source_scalar_map.
      A blank/None value clears the row instead (the param reverts to its Python default).

    Returns True on success, False when the row is not found or doesn't
    belong to source_id (caller surfaces a 404).

    Raises ValueError when output_mode is not a valid value.
    """
    if output_mode is not None and output_mode not in _VALID_OUTPUT_MODES:
        raise ValueError(f"output_mode must be one of {sorted(_VALID_OUTPUT_MODES)!r}; got {output_mode!r}")

    row = conn.execute(
        """
        SELECT sfm.source_function_map_id, sfm.set_id
        FROM source_function_map sfm
        WHERE sfm.source_function_map_id = ? AND sfm.source_id = ?
        """,
        [source_function_map_id, source_id],
    ).fetchone()
    if row is None:
        return False

    set_id = row[1]

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
        # #264: keep the per-function output config in sync — the runner reads it
        # first, so a step-level output_mode edit must propagate or it won't take effect.
        conn.execute(
            "UPDATE function_output_config SET output_mode = ? WHERE source_function_map_id = ?",
            [output_mode, source_function_map_id],
        )

    if bindings is not None:
        # Equal-length-among-varying guard (slice 3): a binding edit must also keep
        # a valid argument-bundle shape. Reject a mismatched edit before the rewrite.
        patch_bundle_params = [
            {"param_id": str(p_id), "columns": [str(c) for c in col_ids]}
            for p_id, col_ids in bindings.items()
            if col_ids
        ]
        try:
            pair_bundles(patch_bundle_params)
        except BundleLengthError as exc:
            raise ValueError(str(exc)) from exc

        # Replace all alias_map rows for params in this set + source atomically
        conn.execute("BEGIN")
        try:
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
            for p_id, col_ids in bindings.items():
                # position = the index of each column in the provided list, so a
                # reorder via PATCH rewrites the argument-bundle column order.
                for pos, col_id in enumerate(col_ids):
                    am_id = content_hash_id("alias_map", str(p_id), str(col_id), str(source_id))
                    conn.execute(
                        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
                        [am_id, col_id, p_id, source_id, pos],
                    )
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            raise RuntimeError(f"Failed to replace bindings: {exc}") from exc

    if scalar_values is not None:
        for p_id, value in scalar_values.items():
            if value is None or str(value).strip() == "":
                # Blank clears the override — the param falls back to its Python default.
                conn.execute(
                    "DELETE FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
                    [source_id, p_id],
                )
                continue
            scalar_id = new_id()
            conn.execute(
                """
                INSERT INTO source_scalar_map (scalar_map_id, source_id, param_id, value)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (source_id, param_id) DO UPDATE SET value = excluded.value
                """,
                [scalar_id, source_id, p_id, value],
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
