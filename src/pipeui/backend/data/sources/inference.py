"""CSV/xlsx column type-inference (data/sources) — DuckDB schema sniffing.

``infer_column_types`` DESCRIBE-sniffs a CSV/xlsx file's columns into ``(name, type)``
pairs normalized to the ``DUCKDB_TO_PYTHON`` vocabulary (``VARCHAR`` fallback);
``map_pandas_dtype`` backs the xlsx pandas-fallback path.

Extracted from ``data/base/db.py`` (#49): type-inference is a sources-data concern, not
a connection one. Consumed by ``domain/sources/create.py`` (down-import). Emits no
stdout — the xlsx fallback is a silent, designed degradation, not an error.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from pipeui.backend.data.base.schema.constants import PYTHON_TO_DUCKDB, normalize_column_type


def infer_column_types(
        conn: duckdb.DuckDBPyConnection,
        file_path: str
) -> list[tuple[str, str]]:
    """Infer column names and types from a CSV or xlsx file using DuckDB sniffing.

    Needs a duckdb connection to execute the DESCRIBE SELECT query, which is used to try and
    get the column types.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".xlsx":
        try:
            rows = conn.execute(
                f"DESCRIBE SELECT * FROM read_xlsx('{file_path}')"
            ).fetchall()
        except Exception:
            # Fall back to pandas for xlsx when read_xlsx is unavailable.
            import pandas as pd  # lazy: xlsx-fallback path only, when DuckDB read_xlsx is unavailable  # noqa: PLC0415

            df = pd.read_excel(file_path, nrows=0)
            return [
                (col, map_pandas_dtype(str(df[col].dtype)))
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
        # Known DuckDB type, else VARCHAR — strips parameterization (#52).
        col_type = normalize_column_type(row[1])
        result.append((col_name, col_type))
    return result


def map_pandas_dtype(dtype_str: str) -> str:
    """Map a pandas dtype string to a DUCKDB_TO_PYTHON key or 'varchar'."""
    return PYTHON_TO_DUCKDB.get(dtype_str, "VARCHAR")
