from __future__ import annotations

import datetime
import os
import re
import uuid
from pathlib import Path

import duckdb

from pipeui.ids import content_hash_id
from pipeui.schema import DUCKDB_TO_PYTHON, PYTHON_TO_DUCKDB
from pipeui.staging import CreateFlowCache
from pipeui.validation import (
    ColumnRegistryEntry,
    FailedRegistryEntry,
    SourceRegistryEntry,
)


def infer_pattern(filename: str) -> str | None:
    """Return a generalized regex pattern for a filename, or None if no digits exist."""
    stem = Path(filename).stem
    if not re.search(r"\d", stem):
        return None
    return re.sub(r"\d+", r"\\d+", stem)


def infer_column_types(
    conn: duckdb.DuckDBPyConnection, file_path: str
) -> list[tuple[str, str]]:
    """Infer column names and types from a CSV or xlsx file using DuckDB sniffing."""
    ext = Path(file_path).suffix.lower()

    if ext == ".xlsx":
        try:
            rows = conn.execute(
                f"DESCRIBE SELECT * FROM read_xlsx('{file_path}')"
            ).fetchall()
        except Exception as exc:
            # Fall back to pandas for xlsx when read_xlsx is unavailable.
            import pandas as pd  # noqa: PLC0415

            df = pd.read_excel(file_path, nrows=0)
            print(exc)
            return [
                (col, _map_pandas_dtype(str(df[col].dtype)))
                for col in df.columns
            ]
    elif ext == ".csv":
        rows = conn.execute(
            "DESCRIBE SELECT * FROM read_csv_auto(?)", [file_path]
        ).fetchall()
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    result = []
    for row in rows:
        col_name = row[0]
        raw_type = row[1].upper() if row[1] else ""
        # Normalize parameterized types like VARCHAR(100) or DECIMAL(18,3) → base name
        base_type = re.split(r"[\s(]", raw_type)[0]
        col_type = base_type if base_type in DUCKDB_TO_PYTHON else "VARCHAR"
        result.append((col_name, col_type))
    return result


def _map_pandas_dtype(dtype_str: str) -> str:
    """Map a pandas dtype string to a DUCKDB_TO_PYTHON key or 'varchar'."""
    return PYTHON_TO_DUCKDB.get(dtype_str, "VARCHAR")


def _get_db_path(conn: duckdb.DuckDBPyConnection) -> str:
    """Return the DB file path for this connection, or ':memory:' for in-memory."""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for row in rows:
            if row[1] == "main" and row[2]:
                return row[2]
    except Exception as exc:
        print(f"Error retrieving database path: {exc}")
    return ":memory:"


def create_source(
    conn: duckdb.DuckDBPyConnection,
    file_path: str,
    source_name: str,
    primary_key: str,
    ingestion_method: str = "upsert",
) -> tuple[uuid.UUID | None, FailedRegistryEntry]:
    """Execute §6 source-create flow as one atomic transaction."""
    failed = FailedRegistryEntry()

    # §6 step 1.1
    pattern = infer_pattern(Path(file_path).name)

    # §6 step 1.2–1.3
    columns = infer_column_types(conn, file_path)

    # §6 step 1.4 — stage in create-flow cache
    cache = CreateFlowCache(conn)
    cache.stage_columns(columns)
    cache.set_primary_key(primary_key)  # [DEFERRED] PK uniqueness not validated (§6, CLAUDE.md M4)

    # §6 step 1.5
    date_ingested = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))

    # §6 step 2 — build and validate SourceRegistryEntry
    try:
        entry = SourceRegistryEntry(
            source_name=source_name,
            date_ingested=date_ingested,
            ingestion_method=ingestion_method,
            pattern=pattern,
            primary_key=primary_key,
        )
    except Exception as exc:
        failed.add(None, str(exc))
        return None, failed

    db_path = _get_db_path(conn)
    entry.generate_table_url(db_path)

    staged = cache.get_staged()

    # §6 steps 3–5 — one transaction covers source_registry + column_registry + source_column_map
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO source_registry
                (source_id, content_hash_id, source_name, date_ingested,
                 date_registered, ingestion_method, pattern, primary_key, table_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry.source_id,
                entry.content_hash_id,
                entry.source_name,
                entry.date_ingested,
                entry.date_registered,
                entry.ingestion_method,
                entry.pattern,
                entry.primary_key,
                entry.table_url,
            ],
        )

        for col in staged:
            col_entry = ColumnRegistryEntry(
                column_name=col["column_name"],
                column_type=col["column_type"],
            )
            conn.execute(
                """
                INSERT INTO column_registry
                    (column_id, content_hash_id, column_name, column_type)
                VALUES (?, ?, ?, ?)
                """,
                [
                    col_entry.column_id,
                    col_entry.content_hash_id,
                    col_entry.column_name,
                    col_entry.column_type,
                ],
            )

            # Map row written directly — no pydantic object (CLAUDE.md Architecture)
            map_id = content_hash_id(
                "source_column_map", str(entry.source_id), str(col_entry.column_id)
            )
            conn.execute(
                "INSERT INTO source_column_map (source_column_map_id, column_id, source_id) VALUES (?, ?, ?)",
                [map_id, col_entry.column_id, entry.source_id],
            )

        conn.execute("COMMIT")
        return entry.source_id, failed

    except Exception as exc:
        conn.execute("ROLLBACK")
        failed.add(entry, str(exc))
        return None, failed
