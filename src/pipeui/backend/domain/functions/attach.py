"""Attach / detach workflows (functions domain) — the pipeline-wiring writes.

attach_function(conn, source_id, bindings, *, function_id, set_id)
    Wires a function or function set onto a source as a new step, writing
    source_function_map + alias_map (+ auto-set, output-config, output-target,
    scalar) rows atomically.

detach_function(conn, source_id, source_function_map_id)
    Removes a step atomically: alias_map rows + source_function_map row, and the
    auto-created function_set + function_set_map rows when no other references
    remain and the set is auto-created.

The read/suggest/edit seams that used to live here now have their own modules
(#46): ``pipeline_read.get_pipeline``, ``suggest.suggest_bindings``,
``step_edit.patch_pipeline_step``.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import duckdb

from pipeui.backend.data.base.ids import content_hash_id, new_id
from pipeui.backend.data.base.results import normalize_label
from pipeui.backend.data.runner.bundles import BundleLengthError, pair_bundles
from pipeui.backend.domain.functions.classification import binding_kind


# ---------------------------------------------------------------------------
# Attach data structures
# ---------------------------------------------------------------------------

@dataclass
class AttachBinding:
    param_id: uuid.UUID
    column_ids: list[uuid.UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Auto-set heuristic — the SINGLE owner shared by attach (reuse) and detach
# (cleanup). An "auto-set" is the one-member set the backend creates on the fly
# when a bare function is attached, named after that function. Both sides agree
# on this one definition so they cannot drift (#46).
# ---------------------------------------------------------------------------

def _is_auto_created_set(conn: duckdb.DuckDBPyConnection, set_id: uuid.UUID) -> bool:
    """True when ``set_id`` is an auto-created single-function set: it has exactly one
    member in function_set_map AND the set's name equals that member function's name.

    This is the one definition of "auto-set". attach reuses a set only when it passes
    this check (so a user's hand-named single-function set is never silently hijacked);
    detach cleans a set up only when it passes (so a user-built set is never deleted).
    When uncertain, both sides treat the set as user-owned — the safe default.
    """
    member_rows = conn.execute(
        """
        SELECT fr.function_name
        FROM function_set_map fsm
        JOIN function_registry fr ON fr.function_id = fsm.function_id
        WHERE fsm.set_id = ?
        """,
        [set_id],
    ).fetchall()
    if len(member_rows) != 1:
        return False
    member_function_name = member_rows[0][0]
    set_name_row = conn.execute(
        "SELECT set_name FROM function_set WHERE set_id = ?",
        [set_id],
    ).fetchone()
    return bool(set_name_row and set_name_row[0] == member_function_name)


def _resolve_or_create_auto_set(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID,
    fn_name: str,
) -> "tuple[uuid.UUID, tuple | None]":
    """Resolve the set a bare function attaches to: reuse a genuine auto-set for this
    function when one exists, else prepare a fresh one.

    Returns ``(resolved_set_id, auto_set)`` where ``auto_set`` is the
    ``(set_id, content_hash, set_name, set_description, set_map_id, function_id)`` tuple
    to INSERT inside the attach transaction — or None when an existing auto-set is
    reused. Reuse is gated on ``_is_auto_created_set`` (the single owner), so a user's
    custom-named single-function set is left untouched and a new auto-set is created
    instead.
    """
    candidate_rows = conn.execute(
        """
        SELECT fs.set_id FROM function_set fs
        JOIN function_set_map fsm ON fsm.set_id = fs.set_id
        WHERE fsm.function_id = ?
          AND (SELECT COUNT(*) FROM function_set_map WHERE set_id = fs.set_id) = 1
        """,
        [function_id],
    ).fetchall()
    for (cand_id,) in candidate_rows:
        if _is_auto_created_set(conn, cand_id):
            return cand_id, None

    resolved_set_id = new_id()
    set_ch = content_hash_id("function_set", str(resolved_set_id))
    set_map_id = new_id()
    auto_set = (resolved_set_id, set_ch, fn_name, None, set_map_id, function_id)
    return resolved_set_id, auto_set


# ---------------------------------------------------------------------------
# attach_function — resolve → validate → write
# ---------------------------------------------------------------------------

def _resolve_set_and_params(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID | None,
    set_id: uuid.UUID | None,
) -> "tuple[uuid.UUID | None, list[tuple] | None, tuple | None, dict | None]":
    """Resolve the target set + its param rows for an attach.

    Returns ``(resolved_set_id, param_rows, auto_set, error)``. When attaching a bare
    ``function_id`` a single-function auto-set is reused or prepared (``auto_set`` is the
    tuple to write, else None). On a not-found function/set ``error`` is a structured
    failure dict and the other fields are None.
    """
    if function_id is not None:
        fn_row = conn.execute(
            "SELECT function_name FROM function_registry WHERE function_id = ?",
            [function_id],
        ).fetchone()
        if fn_row is None:
            return None, None, None, {"ok": False, "missing_params": [], "detail": f"function_id {function_id!r} not found"}
        fn_name = fn_row[0]

        resolved_set_id, auto_set = _resolve_or_create_auto_set(conn, function_id, fn_name)

        param_rows = conn.execute(
            "SELECT param_id, param_name, param_type, has_default FROM parameter WHERE function_id = ?",
            [function_id],
        ).fetchall()
        return resolved_set_id, param_rows, auto_set, None

    # set_id path: verify the set exists, collect params for all its functions.
    if conn.execute("SELECT 1 FROM function_set WHERE set_id = ?", [set_id]).fetchone() is None:
        return None, None, None, {"ok": False, "missing_params": [], "detail": f"set_id {set_id!r} not found"}

    param_rows = conn.execute(
        """
        SELECT p.param_id, p.param_name, p.param_type, p.has_default
        FROM parameter p
        JOIN function_set_map fsm ON fsm.function_id = p.function_id
        WHERE fsm.set_id = ?
        """,
        [set_id],
    ).fetchall()
    return set_id, param_rows, None, None


def _find_missing_bindings(
    param_rows: list[tuple],
    binding_map: dict,
    scalar_values: dict,
) -> list[dict]:
    """Return the structured-failure entries for params with no way to receive an argument.

    Generalized optional-binding rule (param-binding-output-mode #99): a parameter is
    satisfied by ANY of — a column binding, a non-blank typed literal, or a declared
    Python default (``has_default``). Eligibility is derived from ``binding_kind``
    (classification.py), never a parallel type literal:
    - ``table`` (pd.DataFrame) — auto-filled, never requires anything.
    - ``column_only`` (pd.Series) — needs a column binding (no literal/default path).
    - ``value_or_column`` (int/float/bool/str) — needs a binding OR a literal OR a default;
      a numeric/str with none of the three is rejected like an unbound str (Bug #186), so
      the user is blocked at attach instead of crashing per-row at run time.
    """
    def _has_literal(p_id) -> bool:
        v = scalar_values.get(p_id)
        return v is not None and str(v).strip() != ""

    missing = []
    for p_id, p_name, p_type, has_default in param_rows:
        b_kind = binding_kind(p_type)
        if b_kind == "table":
            continue
        if binding_map.get(p_id):
            continue
        if b_kind == "value_or_column" and (_has_literal(p_id) or has_default):
            continue
        missing.append({"param_id": str(p_id), "param_name": p_name, "param_type": p_type})
    return missing


def _validate_bundles(
    param_rows: list[tuple],
    binding_map: dict,
) -> "tuple[list | None, str | None]":
    """Validate the argument-bundle shape before writing (slice 3 / §12, ADR-0001).

    Every varying parameter (>1 bound column) must share one length N; a single-column
    param broadcasts. Returns ``(bundles, None)`` on success, or ``(None, detail)`` when
    two distinct lengths among varying params are found — never a silent zip-shortest
    truncation and never a 500 at run time.
    """
    bundle_params = [
        {"param_id": str(p_id), "columns": [str(c) for c in binding_map.get(p_id, [])]}
        for p_id, _, _, _ in param_rows
        if binding_map.get(p_id)
    ]
    try:
        return pair_bundles(bundle_params), None
    except BundleLengthError as exc:
        return None, str(exc)


def _resolve_target_function_id(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID | None,
    resolved_set_id: uuid.UUID,
    output_targets: list,
) -> uuid.UUID | None:
    """Resolve the function whose output the replace-targets bind.

    For a bare ``function_id`` it is that function; for a set, the single transform
    function in it (output targets are a per-transform-function concept). Only queried
    when targets were provided for a set.
    """
    if function_id is not None:
        return function_id
    if not output_targets:
        return None
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
    return tf_row[0] if tf_row else None


def _persisted_append_name(output_mode: str, append_name: str | None) -> str | None:
    """The append_name to persist: the normalized user name for a non-blank append-mode
    name, else None (replace mode and blank names keep the runtime auto-label path)."""
    if output_mode == "append" and append_name is not None and append_name.strip():
        return normalize_label(append_name)
    return None


def _write_auto_set(conn: duckdb.DuckDBPyConnection, auto_set: tuple) -> None:
    """Insert the auto-created single-function set + its position-0 member row."""
    rs_id, rs_ch, rs_name, rs_desc, rs_map_id, rs_fn_id = auto_set
    conn.execute(
        "INSERT INTO function_set (set_id, content_hash_id, set_name, set_description) VALUES (?, ?, ?, ?)",
        [rs_id, rs_ch, rs_name, rs_desc],
    )
    conn.execute(
        "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) VALUES (?, ?, ?, ?)",
        [rs_map_id, rs_id, rs_fn_id, 0],
    )


def _next_position(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> int:
    """MAX(position) + 1 for this source's steps, or 0 when it has none yet."""
    pos_row = conn.execute(
        "SELECT COALESCE(MAX(position) + 1, 0) FROM source_function_map WHERE source_id = ?",
        [source_id],
    ).fetchone()
    return pos_row[0] if pos_row else 0


def _write_alias_rows(
    conn: duckdb.DuckDBPyConnection,
    param_rows: list[tuple],
    binding_map: dict,
    source_id: uuid.UUID,
) -> None:
    """Write one alias_map row per bound column, position = add-order index so argument
    bundles align by position at run time.

    Written for ANY param that was given columns (param-binding-output-mode #99) — a
    column-bound numeric (value_or_column) persists its alias_map rows exactly like a
    str/pd.Series param. A param with no columns (a text-mode literal, a pd.DataFrame,
    or an unbound default) writes nothing here.
    """
    for p_id, _p_name, _p_type, _has_default in param_rows:
        for pos, col_id in enumerate(binding_map.get(p_id, [])):
            am_id = content_hash_id("alias_map", str(p_id), str(col_id), str(source_id))
            conn.execute(
                "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id, position) VALUES (?, ?, ?, ?, ?)",
                [am_id, col_id, p_id, source_id, pos],
            )


def _write_output_targets(
    conn: duckdb.DuckDBPyConnection,
    sfm_id: uuid.UUID,
    target_function_id: uuid.UUID,
    output_targets: list,
) -> None:
    """Write the ordered output-target map rows for a replace step (bundle i → target i,
    slice 4 / #238)."""
    for pos, col_id in enumerate(output_targets):
        otm_id = content_hash_id("output_target_map", str(sfm_id), str(col_id), str(pos))
        conn.execute(
            "INSERT INTO output_target_map (output_target_map_id, source_function_map_id, function_id, column_id, position) VALUES (?, ?, ?, ?, ?)",
            [otm_id, sfm_id, target_function_id, col_id, pos],
        )


def _write_scalar_values(
    conn: duckdb.DuckDBPyConnection,
    param_rows: list[tuple],
    scalar_values: dict,
    source_id: uuid.UUID,
) -> None:
    """Persist provided scalar / plain-string literals (Bug #186). Only non-blank values
    for params of this attach are written; a blank means "use the Python default"."""
    param_id_set = {p_id for p_id, _, _, _ in param_rows}
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
    after the function (reusing any existing single-function auto-set for this
    function — see ``_is_auto_created_set``) and a function_set_map row at position 0.

    scalar_values: optional param_id -> literal value (VARCHAR). Used for scalar
    params (int/float/bool) and for str params in "plain string" mode. When a
    literal value is provided for a ``str`` param it is EXEMPT from the column
    binding requirement (Bug #186): the literal is persisted to source_scalar_map
    and no alias_map row is written for that param. Values are persisted inside the
    same transaction as the map rows.

    append_name: optional user-provided name for the column an append-mode transform
    adds. Normalized and persisted to source_function_map.append_name only when
    output_mode is "append" and the name is non-blank (NULL otherwise); the runner
    reads the already-clean value verbatim, falling back to the auto-label when NULL.

    Returns either:
      { "ok": True, "source_function_map_id": "<uuid>" }
    or a structured failure dict when validation fails (not an exception):
      { "ok": False, "missing_params": [...], "detail": "<msg>" }
    """
    scalar_values = scalar_values or {}
    if (function_id is None) == (set_id is None):
        return {"ok": False, "missing_params": [], "detail": "Provide exactly one of function_id or set_id"}

    # --- Resolve set_id + param rows (and prepare an auto-set when needed) ---
    resolved_set_id, param_rows, auto_set, error = _resolve_set_and_params(conn, function_id, set_id)
    if error is not None:
        return error

    binding_map: dict[uuid.UUID, list[uuid.UUID]] = {b.param_id: list(b.column_ids) for b in bindings}

    # --- Validate required bindings ---
    missing = _find_missing_bindings(param_rows, binding_map, scalar_values)
    if missing:
        return {"ok": False, "missing_params": missing, "detail": "Missing required column bindings"}

    # --- Equal-length-among-varying guard (slice 3 / §12, ADR-0001) ---
    bundles, bundle_error = _validate_bundles(param_rows, binding_map)
    if bundle_error is not None:
        return {"ok": False, "missing_params": [], "detail": bundle_error}

    # --- Output-target count guard (slice 4 / #240, ADR-0001 output-target map) ---
    # A `replace` step may carry an explicit ordered set of target columns (bundle i
    # overwrites target i). The target count MUST equal the bundle count, or the attach
    # is rejected here — never a 500 at run time and never a partial write. Empty/absent
    # targets are allowed (single-varying default resolves the input column at run time).
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

    target_function_id = _resolve_target_function_id(conn, function_id, resolved_set_id, output_targets)

    # --- Write atomically ---
    sfm_id = new_id()

    conn.execute("BEGIN")
    try:
        if auto_set is not None:
            _write_auto_set(conn, auto_set)

        position = _next_position(conn, source_id)
        persisted_append_name = _persisted_append_name(output_mode, append_name)

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

        _write_alias_rows(conn, param_rows, binding_map, source_id)

        # Output-target map rows (slice 4 / #238): only when targets were provided;
        # `append` steps and replace-with-default-target write none.
        if output_targets and target_function_id is not None:
            _write_output_targets(conn, sfm_id, target_function_id, output_targets)

        if scalar_values:
            _write_scalar_values(conn, param_rows, scalar_values, source_id)

        conn.execute("COMMIT")
        return {"ok": True, "source_function_map_id": str(sfm_id)}

    except Exception as exc:
        conn.execute("ROLLBACK")
        return {"ok": False, "missing_params": [], "detail": f"Transaction failed: {exc}"}


# ---------------------------------------------------------------------------
# detach_function
# ---------------------------------------------------------------------------

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
      4. If no other source_function_map row references this set_id AND the set
         is auto-created (``_is_auto_created_set`` — the single owner), delete the
         function_set + function_set_map rows. When uncertain, skip — an orphan is
         safer than deleting a user-built set.

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

        if remaining == 0 and _is_auto_created_set(conn, set_id):
            conn.execute("DELETE FROM function_set_map WHERE set_id = ?", [set_id])
            conn.execute("DELETE FROM function_set WHERE set_id = ?", [set_id])

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return True
