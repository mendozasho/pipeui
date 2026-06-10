import enum

DUCKDB_TO_PYTHON: dict[str, type] = {
    "DOUBLE": float,
    "FLOAT": float,
    "REAL": float,
    "DECIMAL": float,
    "NUMERIC": float,
    "BIGINT": int,
    "INTEGER": int,
    "INT": int,
    "SMALLINT": int,
    "TINYINT": int,
    "HUGEINT": int,
    "VARCHAR": str,
    "TEXT": str,
    "DATE": str,
    "TIMESTAMP": str,
    "TIMESTAMPTZ": str,
    "BOOLEAN": bool,
    "BOOL": bool,
}
"""DuckDB to Python type mapping."""


PYTHON_TO_DUCKDB = mapping = {
    "int64": "BIGINT",
    "int32": "INTEGER",
    "int16": "SMALLINT",
    "int8": "TINYINT",
    "float64": "DOUBLE",
    "float32": "FLOAT",
    "bool": "BOOLEAN",
    "object": "VARCHAR",
    "datetime64[ns]": "TIMESTAMP",
}
"""Python to DuckDB type mapping."""


class IngestionMethod(enum.Enum):
    """
    Defines the ingestion method enumeration.

    This class is used to specify the method of ingestion for data, allowing
    users to define whether data should be merged with existing data or simply
    added. It helps in managing data input strategies for various applications.

    :ivar UPSERT: Specifies that the ingestion method involves updating existing
        records if they exist or inserting new records otherwise.
    :type UPSERT: str
    :ivar APPEND: Specifies that the ingestion method involves adding new records
        without checking for existing records.
    :type APPEND: str
    :ivar SKIP: Specifies that the ingestion method involves skipping ingestion
        of data without any action.
    :type SKIP: str
    """
    UPSERT = "upsert"
    APPEND = "append"
    SKIP = "skip"

    @staticmethod
    def accepted(val: str) -> bool:
        members = [member.value for member in IngestionMethod.__members__.values()]
        return val.lower() in members
