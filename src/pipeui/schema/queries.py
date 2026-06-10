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
    function_signature   VARCHAR NOT NULL,
    function_type        VARCHAR NOT NULL,
    module_path          VARCHAR NOT NULL,
    is_active            BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS column_registry (
    column_id       UUID PRIMARY KEY,
    content_hash_id UUID NOT NULL UNIQUE,
    column_name     VARCHAR NOT NULL,
    column_type     VARCHAR NOT NULL
);

-- DuckDB 1.5.x has a limitation where updating a UUID UNIQUE column on a row with
-- FK children fails even when the PK is unchanged. Referential integrity is enforced
-- at the application (workflow) layer instead.
CREATE TABLE IF NOT EXISTS parameter (
    param_id        UUID PRIMARY KEY,
    content_hash_id UUID NOT NULL UNIQUE,
    param_name      VARCHAR NOT NULL,
    param_type      VARCHAR NOT NULL,
    function_id     UUID NOT NULL  -- references function_registry(function_id)
);

CREATE TABLE IF NOT EXISTS source_column_map (
    source_column_map_id UUID PRIMARY KEY,
    column_id            UUID NOT NULL,  -- references column_registry(column_id)
    source_id            UUID NOT NULL   -- references source_registry(source_id)
);

CREATE TABLE IF NOT EXISTS source_function_map (
    source_function_map_id UUID PRIMARY KEY,
    source_id              UUID NOT NULL,  -- references source_registry(source_id)
    set_id                 UUID NOT NULL,  -- references function_set(set_id)
    position               INTEGER NOT NULL DEFAULT 0,
    output_mode            VARCHAR NOT NULL DEFAULT 'append'
);

CREATE TABLE IF NOT EXISTS alias_map (
    alias_map_id UUID PRIMARY KEY,
    column_id    UUID NOT NULL,  -- references column_registry(column_id)
    parameter_id UUID NOT NULL,  -- references parameter(param_id)
    source_id    UUID NOT NULL   -- references source_registry(source_id)
);

CREATE TABLE IF NOT EXISTS function_set (
    set_id          UUID PRIMARY KEY,
    content_hash_id UUID NOT NULL UNIQUE,
    set_name        VARCHAR NOT NULL,
    set_description VARCHAR
);

CREATE TABLE IF NOT EXISTS function_set_map (
    set_map_id  UUID PRIMARY KEY,
    set_id      UUID NOT NULL,  -- references function_set(set_id)
    function_id UUID NOT NULL,  -- references function_registry(function_id)
    position    INTEGER NOT NULL
);

-- Catalog of built-in pipeline step types (join, pivot, filter).
-- Seeded once at create_schema time; builtin_type is the stable identifier.
CREATE TABLE IF NOT EXISTS builtin_registry (
    builtin_id    UUID PRIMARY KEY,
    builtin_type  VARCHAR UNIQUE NOT NULL,  -- "join" | "pivot" | "filter"
    display_name  VARCHAR NOT NULL,
    description   TEXT,
    config_schema JSON
);

-- Built-in pipeline steps (join, pivot, filter) attached to a source.
-- Each row is a join, pivot, or filter configuration stored as JSON.
CREATE TABLE IF NOT EXISTS source_builtin_map (
    step_id       UUID PRIMARY KEY,
    source_id     UUID NOT NULL,   -- references source_registry(source_id)
    builtin_type  VARCHAR NOT NULL, -- "join" | "pivot" | "filter"
    builtin_config JSON    NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0
);
"""

SEED_BUILTINS = """
INSERT OR IGNORE INTO builtin_registry (builtin_id, builtin_type, display_name, description, config_schema) VALUES
  ('a1b2c3d4-0001-0001-0001-000000000001'::UUID, 'join',   'Join',
   'Merge two reports on matching column values. Produces a wider table combining columns from both sources.',
   '{"right_source_id": "string", "join_type": "string", "on": "array", "keep_columns": "string"}'),
  ('a1b2c3d4-0001-0001-0001-000000000002'::UUID, 'pivot',  'Pivot',
   'Reshape rows into columns. Groups by index columns, spreads a pivot column''s values into new columns, and aggregates values.',
   '{"index_columns": "array", "pivot_column": "string", "value_columns": "array"}'),
  ('a1b2c3d4-0001-0001-0001-000000000003'::UUID, 'filter', 'Filter',
   'Keep only rows matching a condition (column + operator + value). Supported operators: eq, neq, gt, gte, lt, lte, contains, not_contains, is_null, is_not_null.',
   '{"column": "string", "operator": "string", "value": "any"}');
"""
"""Creates the base application tables on initialization."""
