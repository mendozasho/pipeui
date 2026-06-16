"""Column-type migration workflow (§7).

Entry point: migrate_column(conn, source_id, column_id, new_type, ...)

All mutations happen inside a single DuckDB transaction. The module owns the
connection and transaction; it calls sql_user_table helpers and
ColumnRegistryUpdate from validation but never crosses into schema/ or
validation/ boundaries that would require a DB handle.
"""
from __future__ import annotations

import uuid
from typing import Literal

import duckdb

from pipeui.ids import content_hash_id as _content_hash_id
from pipeui.sql_user_table import build_create_table_sql, instance_table_name
from pipeui.validation.column import ColumnRegistryUpdate, ColumnRegistryEntry

# ---------------------------------------------------------------------------
# Allowed column types (resolved from Active Deferred Work in CLAUDE.md)
# ---------------------------------------------------------------------------
ALLOWED_COLUMN_TYPES: frozenset[str] = frozenset(
    ["INTEGER", "BIGINT", "DOUBLE", "BOOLEAN", "VARCHAR", "DATE", "TIMESTAMP"]
)

# Numeric targets get a formatting-aware cast so US/UK-formatted data survives the
# migration instead of being nullified. Migration-path only — autodetection is
# unchanged (a formatted column is still inferred as VARCHAR; the user converts when
# ready). See CONTEXT.md "numeric formatting cleanup".
NUMERIC_COLUMN_TYPES: frozenset[str] = frozenset(["INTEGER", "BIGINT", "DOUBLE"])

# Characters stripped from a value before a numeric cast: whitespace, thousands-
# separator commas, currency symbols, percent signs, and accounting parentheses.
_NUMERIC_STRIP_CLASS = r"[\s,$%€£¥()]"
# A value fully wrapped in parentheses is accounting notation for a negative.
_PAREN_NEGATIVE_RE = r"^\(.*\)$"


def numeric_cast_expr(column: str, target_type_upper: str) -> str:
    """Return the SQL that migrates ``column`` to ``target_type_upper``.

    For a numeric target the raw value is cleaned of common formatting noise before
    casting (US/UK number format — comma = thousands separator, period = decimal):

    * whitespace, thousands-separator commas, currency symbols ($ € £ ¥) are stripped
      ("$1,234.50" -> 1234.5, "1 234" -> 1234);
    * a percent sign divides the value by 100 ("50%" -> 0.5, "12.5%" -> 0.125);
    * accounting parentheses become a negative ("(1,234)" -> -1234).

    Genuinely non-numeric text ("abc", a lone "$") still yields NULL and follows the
    existing ``on_uncastable`` path. For every non-numeric target this is a plain
    ``TRY_CAST``. Used at all three cast sites (pre-check, nullify collection,
    recreate-and-copy) so they agree on what is castable.
    """
    if target_type_upper not in NUMERIC_COLUMN_TYPES:
        return f'TRY_CAST("{column}" AS {target_type_upper})'

    raw = f'CAST("{column}" AS VARCHAR)'
    magnitude = (
        f"TRY_CAST(regexp_replace({raw}, '{_NUMERIC_STRIP_CLASS}', '', 'g') AS DOUBLE)"
    )
    cleaned = (
        f"({magnitude}"
        f" * CASE WHEN regexp_matches(trim({raw}), '{_PAREN_NEGATIVE_RE}') THEN -1 ELSE 1 END"
        f" * CASE WHEN contains({raw}, '%') THEN 0.01 ELSE 1 END)"
    )
    return f"TRY_CAST({cleaned} AS {target_type_upper})"


def migrate_column(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    column_id: uuid.UUID,
    new_type: str,
    scope: Literal["this_source", "all_shared"] = "this_source",
    on_uncastable: Literal["abort", "nullify"] = "abort",
    dry_run: bool = False,
) -> dict:
    """Migrate a column to a new type.

    Steps (§7):
    1. Validate new_type is in the allowed set.
    2. TRY_CAST pre-check — count rows that cannot be cast.
    3. Shared-row detection — find all sources sharing the column_registry row.
    4. Dry-run: rollback, return counts + shared sources.
    5. on_uncastable="abort": return structured failure if any un-castable rows.
    6. on_uncastable="nullify": collect un-castable PKs before migration.
    7. scope="this_source": copy-on-write for column_registry row.
    8. scope="all_shared": update shared row in place; migrate all sharing sources.
    9. Recreate-and-copy for each affected instance table (never ALTER COLUMN TYPE).
    10. Update column_registry, recompute content_hash_id, enforce collision check.

    Returns:
        On dry_run: {"ok": True, "dry_run": True, "castable": N, "uncastable": M,
                     "shared_sources": [...]}
        On success: {"ok": True, "rows_migrated": N,
                     "nullified": [{"pk": "...", "column": "col_name"}]}
        On failure: {"ok": False, "error": "...", "reason": "..."}
    """
    # Step 1: validate new_type
    new_type_upper = new_type.upper() if new_type else ""
    if new_type_upper not in ALLOWED_COLUMN_TYPES:
        return {
            "ok": False,
            "error": "invalid_column_type",
            "reason": (
                f"column_type {new_type!r} is not in the allowed set: "
                + ", ".join(sorted(ALLOWED_COLUMN_TYPES))
            ),
        }

    # Fetch the column_registry row
    col_row = conn.execute(
        "SELECT column_id, content_hash_id, column_name, column_type "
        "FROM column_registry WHERE column_id = ?",
        [column_id],
    ).fetchone()
    if col_row is None:
        return {
            "ok": False,
            "error": "column_not_found",
            "reason": f"column_id {column_id!r} not found in column_registry",
        }

    _col_id, _col_hash, column_name, current_type = col_row

    # No-op: already the right type
    if current_type.upper() == new_type_upper:
        return {"ok": True, "rows_migrated": 0, "nullified": []}

    # Fetch source's primary_key for the given source_id
    source_row = conn.execute(
        "SELECT primary_key FROM source_registry WHERE source_id = ?",
        [source_id],
    ).fetchone()
    if source_row is None:
        return {
            "ok": False,
            "error": "source_not_found",
            "reason": f"source_id {source_id!r} not found in source_registry",
        }
    primary_key = source_row[0]

    # Step 3: Shared-row detection — all sources referencing this column_id
    shared_rows = conn.execute(
        """
        SELECT scm.source_id, sr.source_name
        FROM source_column_map scm
        JOIN source_registry sr ON sr.source_id = scm.source_id
        WHERE scm.column_id = ?
        """,
        [column_id],
    ).fetchall()
    shared_sources = [
        {"source_id": str(r[0]), "source_name": r[1]} for r in shared_rows
    ]

    # Step 2: TRY_CAST pre-check on the calling source's instance table
    tname = instance_table_name(source_id)
    # Check if the instance table exists before querying it
    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    if tname in tables:
        uncastable_count: int = conn.execute(
            f'SELECT COUNT(*) FROM "{tname}" '
            f'WHERE {numeric_cast_expr(column_name, new_type_upper)} IS NULL '
            f'AND "{column_name}" IS NOT NULL',
        ).fetchone()[0]
        castable_count: int = conn.execute(
            f'SELECT COUNT(*) FROM "{tname}"',
        ).fetchone()[0] - uncastable_count
    else:
        uncastable_count = 0
        castable_count = 0

    # Step 4: dry-run — return counts without mutating
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "castable": castable_count,
            "uncastable": uncastable_count,
            "shared_sources": shared_sources,
        }

    # Step 5: abort if un-castable rows exist
    if on_uncastable == "abort" and uncastable_count > 0:
        return {
            "ok": False,
            "error": "uncastable_rows",
            "reason": (
                f"{uncastable_count} row(s) in {tname!r} cannot be cast to {new_type_upper}"
            ),
            "uncastable": uncastable_count,
        }

    # ---------------------------------------------------------------------------
    # Determine which sources to migrate and build the work list
    # ---------------------------------------------------------------------------
    if scope == "all_shared":
        sources_to_migrate = [r[0] for r in shared_rows]  # all sharing source UUIDs
    else:
        # scope="this_source" — only the calling source
        sources_to_migrate = [source_id]

    # Step 10 pre-check: compute the new content_hash_id and check for collision
    # BEFORE opening the transaction so we can bail early.
    # Build a ColumnRegistryEntry from the existing row to feed into ColumnRegistryUpdate,
    # then read update_obj.content_hash_id — same pattern as update_function_set() (§3).
    existing_col_entry = ColumnRegistryEntry(
        column_id=_col_id,
        column_name=column_name,
        column_type=current_type,
    )
    update_obj = ColumnRegistryUpdate.from_existing(
        existing_col_entry, column_type=new_type_upper
    )
    new_hash = update_obj.content_hash_id

    # Check for collision: same hash on a DIFFERENT column_registry row
    collision_row = conn.execute(
        "SELECT column_id FROM column_registry WHERE content_hash_id = ? AND column_id != ?",
        [new_hash, column_id],
    ).fetchone()

    # For scope="this_source" copy-on-write: a pre-existing matching row is a
    # reuse target, not a collision. Only raise collision when scope="all_shared"
    # (in-place update) and the hash would land on a different row.
    if scope == "all_shared" and collision_row is not None:
        return {
            "ok": False,
            "error": "content_hash_id_collision",
            "reason": (
                f"content_hash_id {new_hash} already exists on a different "
                f"column_registry row: {collision_row[0]}"
            ),
        }

    # For scope="this_source": determine whether a reuse target exists
    reuse_col_id: uuid.UUID | None = None
    if scope == "this_source":
        reuse_row = conn.execute(
            "SELECT column_id FROM column_registry WHERE content_hash_id = ?",
            [new_hash],
        ).fetchone()
        if reuse_row is not None:
            # The target type/name combo already exists in the registry; reuse it
            reuse_col_id = reuse_row[0]

    # ---------------------------------------------------------------------------
    # Begin transaction — all mutations below are atomic
    # ---------------------------------------------------------------------------
    conn.execute("BEGIN")
    try:
        nullified: list[dict] = []
        total_rows_migrated = 0

        # Step 6: collect un-castable PKs for nullify mode (per source being migrated)
        if on_uncastable == "nullify":
            for sid in sources_to_migrate:
                stname = instance_table_name(sid)
                if stname not in tables:
                    continue
                src_pk_row = conn.execute(
                    "SELECT primary_key FROM source_registry WHERE source_id = ?",
                    [sid],
                ).fetchone()
                if src_pk_row is None:
                    continue
                src_pk = src_pk_row[0]
                uncastable_pks = conn.execute(
                    f'SELECT "{src_pk}" FROM "{stname}" '
                    f'WHERE {numeric_cast_expr(column_name, new_type_upper)} IS NULL '
                    f'AND "{column_name}" IS NOT NULL',
                ).fetchall()
                for pk_row in uncastable_pks:
                    nullified.append({"pk": str(pk_row[0]), "column": column_name})

        # Steps 7/8: update column_registry
        if scope == "this_source":
            if reuse_col_id is not None:
                # Reuse existing row — just re-point source_column_map
                effective_col_id = reuse_col_id
            else:
                # Copy-on-write: insert a new column_registry row
                new_col_id = uuid.uuid4()
                conn.execute(
                    "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
                    [new_col_id, new_hash, column_name, new_type_upper],
                )
                effective_col_id = new_col_id

            # Re-point source_column_map for this source only
            map_id = _content_hash_id("source_column_map", str(source_id), str(effective_col_id))
            conn.execute(
                "DELETE FROM source_column_map WHERE source_id = ? AND column_id = ?",
                [source_id, column_id],
            )
            conn.execute(
                "INSERT INTO source_column_map VALUES (?, ?, ?)",
                [map_id, effective_col_id, source_id],
            )

        else:  # scope="all_shared"
            # Update the shared column_registry row in place
            conn.execute(
                "UPDATE column_registry SET column_type = ?, content_hash_id = ? WHERE column_id = ?",
                [new_type_upper, new_hash, column_id],
            )
            effective_col_id = column_id

        # Step 9: Recreate-and-copy each affected instance table
        for sid in sources_to_migrate:
            stname = instance_table_name(sid)
            if stname not in tables:
                continue

            # Fetch this source's PK and full column list
            src_pk_row = conn.execute(
                "SELECT primary_key FROM source_registry WHERE source_id = ?",
                [sid],
            ).fetchone()
            if src_pk_row is None:
                continue
            src_pk = src_pk_row[0]

            # Fetch updated column list for this source from registry
            # For scope="this_source" the map has already been re-pointed above
            col_rows = conn.execute(
                """
                SELECT cr.column_name, cr.column_type
                FROM column_registry cr
                JOIN source_column_map scm ON scm.column_id = cr.column_id
                WHERE scm.source_id = ?
                ORDER BY cr.column_name
                """,
                [sid],
            ).fetchall()
            new_columns = [(r[0], r[1]) for r in col_rows]

            # Build new table DDL with updated schema
            tmp_tname = f"{stname}__mig_tmp"
            new_ddl = build_create_table_sql(tmp_tname, new_columns, src_pk)
            # Use CREATE TABLE (not IF NOT EXISTS) so collision is an error
            new_ddl_strict = new_ddl.replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
            conn.execute(new_ddl_strict)

            # INSERT ... SELECT with TRY_CAST on the changed column
            # Build SELECT list: cast the migrating column, pass-through others
            select_parts = []
            for col_n, col_t in new_columns:
                if col_n == column_name:
                    select_parts.append(
                        f'{numeric_cast_expr(col_n, new_type_upper)} AS "{col_n}"'
                    )
                else:
                    select_parts.append(f'"{col_n}"')

            col_list = ", ".join(f'"{c[0]}"' for c in new_columns)
            select_list = ", ".join(select_parts)
            conn.execute(
                f'INSERT INTO "{tmp_tname}" ({col_list}) '
                f'SELECT {select_list} FROM "{stname}"'
            )

            migrated: int = conn.execute(
                f'SELECT COUNT(*) FROM "{tmp_tname}"'
            ).fetchone()[0]
            total_rows_migrated += migrated

            # Atomic swap: drop old, rename new
            conn.execute(f'DROP TABLE "{stname}"')
            conn.execute(f'ALTER TABLE "{tmp_tname}" RENAME TO "{stname}"')

        conn.execute("COMMIT")
        return {
            "ok": True,
            "rows_migrated": total_rows_migrated,
            "nullified": nullified,
        }

    except Exception as exc:
        conn.execute("ROLLBACK")
        return {
            "ok": False,
            "error": "migration_failed",
            "reason": str(exc),
        }
