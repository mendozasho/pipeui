from __future__ import annotations

import datetime
import os
import uuid
from pathlib import Path

import duckdb

from pipeui.validation.ids import content_hash_id
from pipeui.schema.constants import IngestionMethod
from pipeui.duckdb import infer_column_types, get_db_path
from pipeui.helpers import infer_pattern
from pipeui.workflow.staging import CreateFlowCache
from pipeui.validation import FailedRegistryEntry, SourceRegistryEntry, ColumnRegistryEntry


def create_source(
    conn: duckdb.DuckDBPyConnection,
    file_path: str,
    source_name: str,
    primary_key: str,
    ingestion_method: str = "upsert",
) -> tuple[uuid.UUID | None, FailedRegistryEntry]:
    """Execute the source-create flow as one atomic transaction."""
    failed = FailedRegistryEntry()

    # Check if ingestion method is valid
    if ingestion_method not in IngestionMethod.__members__.values():
        raise ValueError(f"Invalid ingestion method: {ingestion_method}")

    # Try to infer the pattern of the filename for future searches
    pattern = infer_pattern(Path(file_path).name)

    # Try to infer column types from the file. Will later add the ones not in the column registry
    columns = infer_column_types(conn, file_path)

    # [1] [stage in create-flow cache]
    # We stage so that any errors can be rolled back to previous working state.
    cache = CreateFlowCache(conn)
    cache.stage_columns(columns)
    cache.set_primary_key(primary_key)  # [DEFERRED] PK uniqueness not validated (§6, CLAUDE.md M4)

    # The registry table requires the date we ingested the file, so we can keep track.
    # Future builds may use this as a way of rolling back to a previous version.
    date_ingested = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))

    # [2] [build and validate SourceRegistryEntry]
    # We build out the entry with a pydantic-based class to ensure the data is validated and correct.
    try:
        entry = SourceRegistryEntry(
            source_name=source_name,
            date_ingested=date_ingested,
            ingestion_method=ingestion_method,
            pattern=pattern,
            primary_key=primary_key,
        )
    except Exception as exc:
        failed.add(None, str(exc))  # should be an error from the pydantic validation
        return None, failed

    # TODO: This should likely be added as part of the original step.
    #  We aren't creating the path based off the variables
    db_path = get_db_path(conn)
    entry.generate_table_url(db_path)

    staged = cache.get_staged()  # used for the column table

    # one transaction covers source_registry + column_registry + source_column_map
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
