from __future__ import annotations

import datetime
import uuid
from pathlib import Path

import duckdb

import re

from pipeui.backend.data.base.schema.constants import DUCKDB_TO_PYTHON, IngestionMethod
from pipeui.backend.data.base.tables import build_create_table_sql, instance_table_name
from pipeui.backend.data.base.fails import FailedRegistryEntry


def _load_to_temp(conn: duckdb.DuckDBPyConnection, file_path: str, temp_name: str) -> None:
    """Load a CSV or xlsx file into a DuckDB TEMP TABLE using native readers (§9)."""
    ext = Path(file_path).suffix.lower()
    if ext == ".csv":
        conn.execute(
            f"CREATE TEMP TABLE {temp_name} AS SELECT * FROM read_csv_auto(?)",
            [file_path],
        )
    elif ext == ".xlsx":
        try:
            conn.execute(
                f"CREATE TEMP TABLE {temp_name} AS SELECT * FROM read_xlsx('{file_path}')"
            )
        except Exception:
            import pandas as pd  # noqa: PLC0415  # lazy: xlsx fallback — pandas only when DuckDB read_xlsx fails
            df = pd.read_excel(file_path)
            conn.register(f"_df_{temp_name}", df)
            conn.execute(f"CREATE TEMP TABLE {temp_name} AS SELECT * FROM _df_{temp_name}")
            conn.unregister(f"_df_{temp_name}")
    else:
        raise ValueError(f"Unsupported file extension: {ext!r}")


def _py_type(raw: str) -> type:
    """Map a raw DuckDB type string to its Python equivalent via DUCKDB_TO_PYTHON.

    Strips parameterization first (e.g. VARCHAR(100) → VARCHAR).
    Unmapped types fall back to str (same as VARCHAR).
    """
    base = re.split(r"[\s(]", raw.upper())[0]
    return DUCKDB_TO_PYTHON.get(base, str)


def _diff_schema(
    registered_columns: list[tuple[str, str]],
    incoming_columns: list[tuple[str, str]],
) -> dict:
    """Compute schema diff between registered and incoming columns.

    Returns a dict with keys 'added', 'removed', 'type_changes'.
    All lists are empty when there is no mismatch.
    Both sides are compared as Python types via DUCKDB_TO_PYTHON so aliases
    (e.g. INTEGER vs BIGINT, TEXT vs VARCHAR) do not produce false positives.
    """
    registered_raw = {name: ctype for name, ctype in registered_columns}
    incoming_raw = {name: ctype for name, ctype in incoming_columns}
    registered_py = {name: _py_type(ctype) for name, ctype in registered_columns}
    incoming_py = {name: _py_type(ctype) for name, ctype in incoming_columns}

    added = [name for name in incoming_raw if name not in registered_raw]
    removed = [name for name in registered_raw if name not in incoming_raw]
    type_changes = [
        {"column": name, "from": registered_raw[name], "to": incoming_raw[name]}
        for name in incoming_raw
        if name in registered_raw and registered_py[name] != incoming_py[name]
    ]

    return {"added": added, "removed": removed, "type_changes": type_changes}


def _has_schema_diff(diff: dict) -> bool:
    return bool(diff["added"] or diff["removed"] or diff["type_changes"])


def ingest_source(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    file_path: str,
    ingestion_method: str | None = None,
    confirm_schema_diff: bool = False,
) -> tuple[int, list, FailedRegistryEntry, dict | None]:
    """Stage a file and write rows into the per-source instance table.

    Returns (rows_ingested, skipped_pk_values, failed, schema_diff).
    ingestion_method overrides the source's stored value when provided.
    Instance table is created JIT if it does not yet exist (§8, §9).

    When the incoming file's columns differ from the registered schema and
    confirm_schema_diff is False, returns early with schema_diff populated
    and rows_ingested=0 (caller inspects requires_confirmation).
    """
    failed = FailedRegistryEntry()

    source_row = conn.execute(
        "SELECT source_name, primary_key, ingestion_method FROM source_registry WHERE source_id = ?",
        [source_id],
    ).fetchone()
    if source_row is None:
        failed.add(None, f"source_id {source_id!r} not found")
        return 0, [], failed, None

    _source_name, primary_key, stored_method = source_row
    method = ingestion_method if ingestion_method is not None else stored_method

    if not IngestionMethod.accepted(method):
        failed.add(None, f"Invalid ingestion_method: {method!r}")
        return 0, [], failed, None

    col_rows = conn.execute(
        """
        SELECT cr.column_name, cr.column_type
        FROM column_registry cr
        JOIN source_column_map scm ON scm.column_id = cr.column_id
        WHERE scm.source_id = ?
        ORDER BY cr.column_name
        """,
        [source_id],
    ).fetchall()
    columns = [(r[0], r[1]) for r in col_rows]

    tname = instance_table_name(source_id)

    # JIT: build the instance table from registry metadata if it doesn't exist yet (§8)
    ddl = build_create_table_sql(tname, columns, primary_key)
    conn.execute(ddl)

    # Explicit column list guards against column-order differences between the file
    # and the registry (both use the same names; order in SELECT * may vary).
    col_list = ", ".join(f'"{name}"' for name, _ in columns)

    # Stage into a TEMP TABLE — written to the real table only on success (§9)
    temp_name = f"_ingest_{source_id.hex}"
    try:
        _load_to_temp(conn, file_path, temp_name)
    except Exception as exc:
        failed.add(None, f"Failed to load file: {exc}")
        return 0, [], failed, None

    # Schema diff check: compare incoming file columns against registered columns.
    # If a mismatch is detected and the caller has not confirmed, return early.
    if not confirm_schema_diff:
        incoming_desc = conn.execute(f"DESCRIBE {temp_name}").fetchall()
        # DESCRIBE returns (column_name, column_type, ...) per row
        incoming_columns = [(r[0], r[1]) for r in incoming_desc]
        diff = _diff_schema(columns, incoming_columns)
        if _has_schema_diff(diff):
            conn.execute(f"DROP TABLE IF EXISTS {temp_name}")
            return 0, [], failed, diff

    total_rows: int = conn.execute(f"SELECT COUNT(*) FROM {temp_name}").fetchone()[0]
    skipped_pks: list = []

    conn.execute("BEGIN")
    try:
        if method == "upsert":
            conn.execute(
                f'INSERT OR REPLACE INTO "{tname}" ({col_list}) '
                f"SELECT {col_list} FROM {temp_name}"
            )
            rows_ingested = total_rows

        elif method == "append":
            conn.execute(
                f'INSERT INTO "{tname}" ({col_list}) SELECT {col_list} FROM {temp_name}'
            )
            rows_ingested = total_rows

        else:  # skip
            # Collect the PK values of rows that would collide before inserting
            skipped = conn.execute(
                f'SELECT "{primary_key}" FROM {temp_name} '
                f'WHERE "{primary_key}" IN (SELECT "{primary_key}" FROM "{tname}")'
            ).fetchall()
            skipped_pks = [r[0] for r in skipped]
            conn.execute(
                f'INSERT INTO "{tname}" ({col_list}) '
                f"SELECT {col_list} FROM {temp_name} ON CONFLICT DO NOTHING"
            )
            rows_ingested = total_rows - len(skipped_pks)

        # Record the time of this ingest in the registry (no per-ingest history — §9)
        conn.execute(
            "UPDATE source_registry SET date_ingested = ? WHERE source_id = ?",
            [datetime.datetime.now(), source_id],
        )
        conn.execute("COMMIT")
        return rows_ingested, skipped_pks, failed, None

    except Exception as exc:
        conn.execute("ROLLBACK")
        failed.add(None, str(exc))
        return 0, [], failed, None
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {temp_name}")
