"""Source read-path (sources domain) — registry reads + row preview, NO writes.

The read seam for the Data screen and pipeline binding. Pure reads — no transaction,
no ingest. Extracted from ``ingestion.py`` (#48): that module is the ingest write-path;
the read/preview helpers (``get_source_detail``/``get_source_rows``) plus the
listing/summary and existence/ownership guards live here, so the API layer can call a
workflow contract instead of running SQL in the HTTP seam (DIP fix — §14).

Functions:
- list_source_summaries — all sources + columns + row_count for GET /sources (no N+1)
- get_source_summary — one source's record (no row_count) for the register echo
- get_source_columns — a source's registered columns (join-modal picker)
- source_exists / check_column_ownership — existence/ownership guards; return a value
  the route maps to a 404 (domain stays HTTP-free)
- get_source_detail / get_source_rows — per-source detail + row preview
"""
from __future__ import annotations

import uuid

import duckdb

from pipeui.backend.data.base.tables import instance_table_name


# The source_registry projection shared by the listing + single-record builders.
# Order is load-bearing: _build_source_record zips it against the row tuple.
_SOURCE_FIELDS = [
    "source_id", "source_name", "date_ingested", "date_registered",
    "ingestion_method", "pattern", "primary_key", "table_url", "content_hash_id",
]
_SOURCE_SELECT = ", ".join(f"sr.{f}" for f in _SOURCE_FIELDS)


def _build_source_record(row: tuple, columns: list[dict]) -> dict:
    """Build the API source record (the 9 registry fields + columns) from a row tuple.

    Stringifies ids and ISO-formats dates exactly as the legacy ``_source_rows`` builder
    did. Does NOT add row_count — callers that need it append it (list view) and callers
    that must not (the register echo) leave it off.
    """
    record = dict(zip(_SOURCE_FIELDS, row))
    record["source_id"] = str(record["source_id"])
    record["content_hash_id"] = str(record["content_hash_id"])
    record["date_ingested"] = record["date_ingested"].isoformat() if record["date_ingested"] else None
    record["date_registered"] = record["date_registered"].isoformat() if record["date_registered"] else None
    record["columns"] = columns
    return record


def _row_count(conn: duckdb.DuckDBPyConnection, source_id) -> int:
    """Exact row count of a source's JIT instance table, or 0 when it doesn't exist yet."""
    tname = instance_table_name(source_id)
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    except Exception:
        return 0


def list_source_summaries(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return every source's record (registry fields + columns + row_count) for GET /sources.

    Two base queries (all sources; all columns grouped by source) replace the old
    per-source column query and the per-source ``get_source_detail`` row_count loop —
    the N+1 the route used to incur. row_count stays the EXACT instance-table count (one
    cheap COUNT(*) per source, 0 when the table doesn't exist yet), so the payload is
    byte-identical to the legacy ``_source_rows`` + row_count output.
    """
    src_rows = conn.execute(
        f"""
        SELECT {_SOURCE_SELECT}
        FROM source_registry sr
        ORDER BY sr.date_registered DESC, sr.source_name
        """
    ).fetchall()

    col_rows = conn.execute(
        """
        SELECT scm.source_id, cr.column_id, cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        ORDER BY cr.column_name
        """
    ).fetchall()
    cols_by_source: dict[str, list[dict]] = {}
    for sid, cid, cname, ctype in col_rows:
        cols_by_source.setdefault(str(sid), []).append(
            {"column_id": str(cid), "column_name": cname, "column_type": ctype}
        )

    results = []
    for row in src_rows:
        record = _build_source_record(row, cols_by_source.get(str(row[0]), []))
        record["row_count"] = _row_count(conn, row[0])
        results.append(record)
    return results


def get_source_summary(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> dict | None:
    """Return one source's record (registry fields + columns, NO row_count) or None.

    The shape the register/ingest-match responses echo back — identical to a single
    legacy ``_source_rows`` entry (which never carried row_count).
    """
    row = conn.execute(
        f"SELECT {_SOURCE_SELECT} FROM source_registry sr WHERE sr.source_id = ?",
        [source_id],
    ).fetchone()
    if row is None:
        return None

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
        {"column_id": str(c[0]), "column_name": c[1], "column_type": c[2]}
        for c in col_rows
    ]
    return _build_source_record(row, columns)


def get_source_columns(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> list[dict]:
    """Return a source's registered columns as ``[{column_name, column_type}]``.

    The raw column set the join-modal picker binds against (no column_id — matches the
    legacy GET /sources/{id}/join-columns non-transformed payload).
    """
    cols = conn.execute(
        """
        SELECT cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()
    return [{"column_name": c[0], "column_type": c[1]} for c in cols]


def source_exists(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> bool:
    """True when ``source_id`` is registered. The existence guard the routes map to a 404."""
    return conn.execute(
        "SELECT 1 FROM source_registry WHERE source_id = ?", [source_id]
    ).fetchone() is not None


def check_column_ownership(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    column_id: uuid.UUID,
) -> str:
    """Structured existence/ownership guard for a (source, column) pair.

    Returns one of ``"source_missing"`` | ``"column_missing"`` | ``"not_owned"`` | ``"ok"``,
    checked in that order so the route can raise the same three 404 messages it used to
    inline. Domain stays HTTP-free — the route translates the status to an HTTPException.
    """
    if not source_exists(conn, source_id):
        return "source_missing"
    if conn.execute(
        "SELECT 1 FROM column_registry WHERE column_id = ?", [column_id]
    ).fetchone() is None:
        return "column_missing"
    if conn.execute(
        "SELECT 1 FROM source_column_map WHERE source_id = ? AND column_id = ?",
        [source_id, column_id],
    ).fetchone() is None:
        return "not_owned"
    return "ok"


def get_source_rows(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    limit: int = 200,
) -> list[dict]:
    """Return up to `limit` rows from the JIT instance table as plain dicts.

    Returns an empty list when the instance table does not yet exist (source
    registered but not ingested) or when the source has no rows. No transaction
    needed — read-only. (§9 Row preview note.)
    """
    tname = instance_table_name(source_id)
    try:
        rows = conn.execute(
            f'SELECT * FROM "{tname}" LIMIT ?', [limit]
        ).fetchall()
    except Exception:
        # Table does not exist yet (not ingested) or any other read error → empty.
        return []

    if not rows:
        return []

    col_names = [desc[0] for desc in conn.description]
    return [dict(zip(col_names, row)) for row in rows]


def get_source_detail(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    include_functions: bool = False,
) -> dict | None:
    """Return per-source detail including row_count and columns for GET /sources/{id}.

    Shaped for both the Data screen drawer and Phase E pipeline binding.
    row_count is 0 when no data has been ingested yet (instance table not created).

    When include_functions=True, adds a 'functions' field with each attached function's
    function_name, function_type, and set_name, ordered by source_function_map.position
    then function_set_map.position. Pass include_functions=False on list endpoints to
    avoid an N+1 query per source.
    """
    row = conn.execute(
        """
        SELECT source_id, source_name, date_ingested, date_registered,
               ingestion_method, primary_key
        FROM source_registry WHERE source_id = ?
        """,
        [source_id],
    ).fetchone()
    if row is None:
        return None

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

    primary_key = row[5]
    tname = instance_table_name(source_id)
    try:
        row_count: int = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    except Exception:
        row_count = 0

    try:
        distinct_pk_count: int | None = conn.execute(
            f'SELECT COUNT(DISTINCT "{primary_key}") FROM "{tname}"'
        ).fetchone()[0]
    except Exception:
        distinct_pk_count = None

    detail: dict = {
        "source_id": str(row[0]),
        "source_name": row[1],
        "date_ingested": row[2].isoformat() if row[2] else None,
        "date_registered": row[3].isoformat() if row[3] else None,
        "ingestion_method": row[4],
        "primary_key": primary_key,
        "row_count": row_count,
        "distinct_pk_count": distinct_pk_count,
        "columns": [
            {"column_id": str(c[0]), "column_name": c[1], "column_type": c[2]}
            for c in col_rows
        ],
    }

    if include_functions:
        fn_rows = conn.execute(
            """
            SELECT fr.function_name, fr.function_type, fs.set_name
            FROM source_function_map sfm
            JOIN function_set fs ON fs.set_id = sfm.set_id
            JOIN function_set_map fsm ON fsm.set_id = fs.set_id
            JOIN function_registry fr ON fr.function_id = fsm.function_id
            WHERE sfm.source_id = ?
            ORDER BY sfm.position ASC, fsm.position ASC
            """,
            [source_id],
        ).fetchall()
        detail["functions"] = [
            {"function_name": r[0], "function_type": r[1], "set_name": r[2]}
            for r in fn_rows
        ]

    return detail
