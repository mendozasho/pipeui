import duckdb


class CreateFlowCache:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        conn.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _stage_create_flow (
                column_name    VARCHAR NOT NULL,
                column_type    VARCHAR NOT NULL,
                is_primary_key BOOLEAN NOT NULL DEFAULT false
            )
        """)

    def stage_columns(self, columns: list[tuple[str, str]]) -> None:
        self._conn.execute("DELETE FROM _stage_create_flow")
        self._conn.executemany(
            "INSERT INTO _stage_create_flow (column_name, column_type) VALUES (?, ?)",
            columns,
        )

    def set_primary_key(self, column_name: str) -> None:
        self._conn.execute(
            "UPDATE _stage_create_flow SET is_primary_key = false"
        )
        self._conn.execute(
            "UPDATE _stage_create_flow SET is_primary_key = true WHERE column_name = ?",
            [column_name],
        )

    def get_staged(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT column_name, column_type, is_primary_key FROM _stage_create_flow"
        ).fetchall()
        return [
            {"column_name": r[0], "column_type": r[1], "is_primary_key": r[2]}
            for r in rows
        ]

    def get_primary_key(self) -> str | None:
        row = self._conn.execute(
            "SELECT column_name FROM _stage_create_flow WHERE is_primary_key = true LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def clear(self) -> None:
        self._conn.execute("DELETE FROM _stage_create_flow")

    def drop(self) -> None:
        self._conn.execute("DROP TABLE IF EXISTS _stage_create_flow")
