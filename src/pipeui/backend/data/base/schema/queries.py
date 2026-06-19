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
    function_id     UUID NOT NULL,  -- references function_registry(function_id)
    has_default     BOOLEAN NOT NULL DEFAULT FALSE,  -- #258: param has a Python default
    default_value   VARCHAR  -- #258: str() of the Python default; NULL when has_default is FALSE
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
    output_mode            VARCHAR NOT NULL DEFAULT 'append',
    append_name            VARCHAR  -- user-provided append column name; NULL -> auto-label
);

CREATE TABLE IF NOT EXISTS alias_map (
    alias_map_id UUID PRIMARY KEY,
    column_id    UUID NOT NULL,  -- references column_registry(column_id)
    parameter_id UUID NOT NULL,  -- references parameter(param_id)
    source_id    UUID NOT NULL,  -- references source_registry(source_id)
    position     INTEGER NOT NULL DEFAULT 0  -- add-order of the bound column within its parameter
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

-- Persisted scalar argument overrides per (source, param) pair.
-- value is stored as VARCHAR; the execution layer casts to param_type at run time.
-- If no row exists for a (source_id, param_id) pair, the Python default is used.
CREATE TABLE IF NOT EXISTS source_scalar_map (
    scalar_map_id UUID PRIMARY KEY,
    source_id     UUID NOT NULL,  -- references source_registry(source_id)
    param_id      UUID NOT NULL,  -- references parameter(param_id)
    value         VARCHAR NOT NULL,
    UNIQUE (source_id, param_id)
);

-- Output-target map: ties a `replace` transform step's OUTPUT to an ordered set
-- of target columns (bundle i -> target i). The first table that maps a function's
-- *output* to columns — alias_map is param-keyed INPUT, source_function_map is
-- source->set, source_scalar_map is scalar values (ADR-0001). Keyed by
-- (source_function_map_id, function_id) -> ordered (column_id, position). Only
-- `replace` steps write rows here; `append` steps write none.
CREATE TABLE IF NOT EXISTS output_target_map (
    output_target_map_id   UUID PRIMARY KEY,
    source_function_map_id UUID NOT NULL,  -- references source_function_map(source_function_map_id)
    function_id            UUID NOT NULL,  -- references function_registry(function_id)
    column_id              UUID NOT NULL,  -- references column_registry(column_id)
    position               INTEGER NOT NULL DEFAULT 0  -- bundle index this target overwrites
);

-- Per-function transform output config (#264). output_mode + append_name are a
-- property of each FUNCTION within a step, not of the whole set — a multi-function
-- set can mix append/replace and per-function append names. Keyed (sfm_id, function_id),
-- mirroring output_target_map's per-function granularity. Legacy steps without a row
-- fall back to source_function_map.output_mode / append_name.
CREATE TABLE IF NOT EXISTS function_output_config (
    source_function_map_id UUID NOT NULL,  -- references source_function_map(source_function_map_id)
    function_id            UUID NOT NULL,  -- references function_registry(function_id)
    output_mode            VARCHAR NOT NULL DEFAULT 'append',
    append_name            VARCHAR,        -- user-provided append column name; NULL -> auto-label
    PRIMARY KEY (source_function_map_id, function_id)
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
   '{"column": "string", "operator": "string", "value": "any"}'),
  ('a1b2c3d4-0001-0001-0001-000000000004'::UUID, 'rename', 'Rename',
   'Rename selected columns in the report output. Runs last in the pipeline; one per report. Output-only — does not change the source schema.',
   '{"renames": "object"}');
"""
"""Creates the base application tables on initialization."""
