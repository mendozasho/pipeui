import csv
import datetime
import itertools
import uuid
from pathlib import Path

import pytest

from pipeui.ids import content_hash_id
from pipeui.duckdb import create_schema, get_connection


@pytest.fixture
def db():
    # fresh in-memory sandbox; function-scoped so each test gets a clean slate
    conn = get_connection(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def db_file(tmp_path):
    # file-backed only when table_url / file-path resolution is under test
    path = str(tmp_path / "test.db")
    conn = get_connection(path)
    create_schema(conn)
    yield conn, path
    conn.close()


@pytest.fixture
def patch_new_id(monkeypatch):
    counter = itertools.count(1)

    def _fixed() -> uuid.UUID:
        return uuid.UUID(int=next(counter))

    monkeypatch.setattr("pipeui.ids.new_id", _fixed)
    return _fixed


def make_registered_source(conn, n_columns: int = 2):
    """
    Create a test source with a specified number of columns.

    Returns:
        tuple: source_id, list of column_ids
    """
    source_id = uuid.uuid4()
    ch = content_hash_id("source_registry", f"test_source_{source_id}", "id", "upsert")
    conn.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_id, ch, f"test_source_{source_id}", datetime.date.today(), "upsert", "id"],
    )

    column_ids = []
    for i in range(n_columns):
        col_id = uuid.uuid4()
        col_name = f"col_{i}"
        col_ch = content_hash_id("column_registry", col_name, "INTEGER")
        conn.execute(
            "INSERT INTO column_registry VALUES (?, ?, ?, ?)",
            [col_id, col_ch, col_name, "INTEGER"],
        )
        map_id = content_hash_id("source_column_map", str(source_id), str(col_id))
        conn.execute(
            "INSERT INTO source_column_map VALUES (?, ?, ?)",
            [map_id, col_id, source_id],
        )
        column_ids.append(col_id)

    return source_id, column_ids


def make_quirky_file(tmp_path, spec: dict, fmt: str = "csv") -> Path:
    """
    Generate a test file in tmp_path with columns controlled by spec flags.

    spec keys (all optional booleans):
      mixed_type       — a "quirky" column with mixed int/str values; tests
                         TRY_CAST pre-check failure when migrating to INTEGER.
      ambiguous_type   — a column whose values are all numeric strings; could
                         be inferred as INTEGER or VARCHAR depending on context.
      varchar_fallback — a column with genuinely mixed content (str + int + bool)
                         that forces VARCHAR inference.

    fmt: "csv" (default) or "xlsx".
    Returns a Path to the generated file.
    """
    columns = ["id"]
    rows_by_col: dict[str, list] = {"id": ["r1", "r2", "r3"]}

    if spec.get("mixed_type"):
        columns.append("mixed_col")
        rows_by_col["mixed_col"] = ["123", "abc", "456"]

    if spec.get("ambiguous_type"):
        columns.append("ambiguous_col")
        rows_by_col["ambiguous_col"] = ["10", "20", "30"]

    if spec.get("varchar_fallback"):
        columns.append("varchar_col")
        rows_by_col["varchar_col"] = ["hello", 123, True]

    rows = [
        [rows_by_col[col][i] for col in columns]
        for i in range(3)
    ]

    if fmt == "xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise ImportError("openpyxl is required for xlsx output") from exc
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(columns)
        for row in rows:
            ws.append(row)
        p = tmp_path / "quirky.xlsx"
        wb.save(p)
    else:
        p = tmp_path / "quirky.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(columns)
            w.writerows(rows)

    return p
