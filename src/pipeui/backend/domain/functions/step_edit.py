"""Placed-step edit (functions domain).

patch_pipeline_step(conn, source_id, source_function_map_id, *, position, output_mode,
                    bindings, scalar_values)
    Updates a step already on the canvas: reorder (position), switch output_mode,
    rewrite its column bindings, and/or upsert its scalar values — each guarded and
    transactional where it rewrites rows.

Split out of ``attach.py`` (#46): the step-edit seam.

Principle 7 (#191/#260): a binding rewrite persists the column ORDER the user
provides (alias_map.position = list index), so a re-opened step round-trips.

§12: alias_map binding; §14: API layer calls workflow only.
"""
from __future__ import annotations

import uuid

import duckdb

from pipeui.backend.data.base.ids import content_hash_id, new_id
from pipeui.backend.data.runner.bundles import BundleLengthError, pair_bundles


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
