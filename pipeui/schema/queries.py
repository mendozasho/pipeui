"""Python to DuckDB type mapping."""

DDL = """
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
