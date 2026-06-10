from __future__ import annotations

import uuid


def instance_table_name(source_id: uuid.UUID) -> str:
    """Return the DuckDB table name for a per-source instance table."""
    return f"src_{source_id.hex}"


def build_create_table_sql(
    table_name: str,
    columns: list[tuple[str, str]],
    primary_key: str,
) -> str:
    """Return a CREATE TABLE IF NOT EXISTS DDL string for a per-source instance table.

    Pure function — no DB connection, no registry knowledge.
    Table-level PRIMARY KEY constraint is used (safe to extend to composite PKs).
    Identifiers are double-quoted to handle names with spaces or reserved words.
    """
    col_defs = ",\n    ".join(f'"{name}" {col_type}' for name, col_type in columns)
    return (
        f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n'
        f"    {col_defs},\n"
        f'    PRIMARY KEY ("{primary_key}")\n'
        f")"
    )
