from __future__ import annotations

import uuid
from pathlib import Path

import duckdb

from pipeui.schema.constants import IngestionMethod
from pipeui.sql_user_table import build_create_table_sql, instance_table_name
from pipeui.validation.fails import FailedRegistryEntry


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
            import pandas as pd  # noqa: PLC0415
            df = pd.read_excel(file_path)
            conn.register(f"_df_{temp_name}", df)
            conn.execute(f"CREATE TEMP TABLE {temp_name} AS SELECT * FROM _df_{temp_name}")
            conn.unregister(f"_df_{temp_name}")
    else:
        raise ValueError(f"Unsupported file extension: {ext!r}")


def ingest_source(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    file_path: str,
    ingestion_method: str | None = None,
) -> tuple[int, list, FailedRegistryEntry]:
    """Stage a file and write rows into the per-source instance table.

    Returns (rows_ingested, skipped_pk_values, failed).
    ingestion_method overrides the source's stored value when provided.
    Instance table is created JIT if it does not yet exist (§8, §9).
    """
    failed = FailedRegistryEntry()

    source_row = conn.execute(
        "SELECT source_name, primary_key, ingestion_method FROM source_registry WHERE source_id = ?",
        [source_id],
    ).fetchone()
    if source_row is None:
        failed.add(None, f"source_id {source_id!r} not found")
        return 0, [], failed

    _source_name, primary_key, stored_method = source_row
    method = ingestion_method if ingestion_method is not None else stored_method

    if not IngestionMethod.accepted(method):
        failed.add(None, f"Invalid ingestion_method: {method!r}")
        return 0, [], failed

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
        return 0, [], failed

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

        conn.execute("COMMIT")
        return rows_ingested, skipped_pks, failed

    except Exception as exc:
        conn.execute("ROLLBACK")
        failed.add(None, str(exc))
        return 0, [], failed
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {temp_name}")


def get_source_detail(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> dict | None:
    """Return per-source detail including row_count and columns for GET /sources/{id}.

    Shaped for both the Data screen drawer and Phase E pipeline binding.
    row_count is 0 when no data has been ingested yet (instance table not created).
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

    tname = instance_table_name(source_id)
    try:
        row_count: int = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    except Exception:
        row_count = 0

    return {
        "source_id": str(row[0]),
        "source_name": row[1],
        "date_ingested": row[2].isoformat() if row[2] else None,
        "date_registered": row[3].isoformat() if row[3] else None,
        "ingestion_method": row[4],
        "primary_key": row[5],
        "row_count": row_count,
        "columns": [
            {"column_id": str(c[0]), "column_name": c[1], "column_type": c[2]}
            for c in col_rows
        ],
    }
