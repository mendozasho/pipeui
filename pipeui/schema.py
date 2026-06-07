import duckdb

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

_DDL = """
CREATE TABLE IF NOT EXISTS source_registry (
    source_id        UUID PRIMARY KEY,
    content_hash_id  UUID NOT NULL UNIQUE,
    source_name      VARCHAR NOT NULL,
    date_ingested    TIMESTAMP,
    date_registered  DATE NOT NULL,
    ingestion_method VARCHAR NOT NULL,
    pattern          VARCHAR,
    primary_key      VARCHAR NOT NULL,
    table_url        VARCHAR
);

CREATE TABLE IF NOT EXISTS function_registry (
    function_id          UUID PRIMARY KEY,
    content_hash_id      UUID NOT NULL UNIQUE,
    function_class       VARCHAR NOT NULL,
    function_name        VARCHAR NOT NULL,
    function_doc         VARCHAR,
    function_return_type VARCHAR NOT NULL,
    function_type        VARCHAR NOT NULL,
    module_path          VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS column_registry (
    column_id       UUID PRIMARY KEY,
    content_hash_id UUID NOT NULL UNIQUE,
    column_name     VARCHAR NOT NULL,
    column_type     VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter (
    param_id        UUID PRIMARY KEY,
    content_hash_id UUID NOT NULL UNIQUE,
    param_name      VARCHAR NOT NULL,
    param_type      VARCHAR NOT NULL,
    function_id     UUID NOT NULL REFERENCES function_registry(function_id)
);

CREATE TABLE IF NOT EXISTS source_column_map (
    source_column_map_id UUID PRIMARY KEY,
    column_id            UUID NOT NULL REFERENCES column_registry(column_id),
    source_id            UUID NOT NULL REFERENCES source_registry(source_id)
);

CREATE TABLE IF NOT EXISTS source_function_map (
    source_function_map_id UUID PRIMARY KEY,
    source_id              UUID NOT NULL REFERENCES source_registry(source_id),
    function_id            UUID NOT NULL REFERENCES function_registry(function_id)
);

CREATE TABLE IF NOT EXISTS alias_map (
    alias_map_id UUID PRIMARY KEY,
    column_id    UUID NOT NULL REFERENCES column_registry(column_id),
    parameter_id UUID NOT NULL REFERENCES parameter(param_id),
    source_id    UUID NOT NULL REFERENCES source_registry(source_id)
);
"""
"""Creates the base application tables on initialization."""


############################
# DuckDB Related Functions
############################
# Leaving it here in case in the future, we need to get away from DuckDB

def get_connection(db_path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Establishes and returns a connection to a DuckDB database.

    This function creates a connection to a DuckDB database using the provided
    database file path. If no path is provided, it defaults to an in-memory
    database.

    :param db_path: The file path to the DuckDB database. Defaults to ":memory:"
                    which creates an in-memory database.
    :type db_path: str
    :return: A DuckDBPyConnection object representing the connection to the database.
    :rtype: duckdb.DuckDBPyConnection
    """
    return duckdb.connect(db_path)


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Creates the necessary schema in the provided DuckDB connection.

    This function executes a predefined SQL Data Definition Language (DDL) statement
    to create database schema elements such as tables or other objects within the
    given DuckDB connection.

    :param conn: The DuckDB connection object to execute the schema creation
        DDL statement on.
    :type conn: duckdb.DuckDBPyConnection

    :return: None
    """
    conn.execute(_DDL)
