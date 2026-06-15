"""Pipeline run workflow — Phase E2.

run_pipeline(conn, source_id, run_type, set_id=None)
    Executes the pipeline for a source and returns per-step results.

Run types (controlled by run_type param):
  "transforms"  — execute only steps whose set has ≥1 transform function
  "validations" — execute only steps whose set has ≥1 validation function
  "set"         — execute only the single specified set (requires set_id kwarg)

Transform chaining:
  Steps execute in source_function_map.position order.  Each transform step
  receives the current working table (starts as a full copy of the source's
  instance table) and produces a new working table.

Validation steps:
  Run against the **original** instance table (never the working table).
  They produce rows_passed / rows_failed counts and do not modify the working
  table.

Failure handling:
  A failed worker call marks the step "failed" with the error message; the
  chain continues with the last good working table.

Staging tables:
  After each successful transform step the working table is written to DuckDB as
      staging_{source_id_short}_{unix_timestamp}
  where source_id_short is the first 8 hex chars of the source UUID.
  Before each run all prior staging_{source_id_short}_* tables for that source
  are dropped.  Validation-only runs do not write a staging table.

§10: worker boundary; §12: alias_map binding.
"""
from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

from pipeui.results import RunResult, ValidationRunResult, normalize_label
from pipeui.sql_user_table import instance_table_name
from pipeui.workflow.bundles import pair_bundles
from pipeui.validation.fails import FailedFunctionEntry
from pipeui.workflow.worker import call_function


# ---------------------------------------------------------------------------
# Scalar parameter resolution (#258)
# ---------------------------------------------------------------------------

class RequiredParamError(Exception):
    """A scalar param has no persisted value and no Python default — the function
    cannot run. Surfaced as a failed RunResult the frontend can pick up."""

    def __init__(self, param_name: str):
        self.param_name = param_name
        super().__init__(
            f"parameter '{param_name}' is required but no value or default was provided"
        )


def _coerce_scalar(value: str, param_type: str):
    """Coerce a source_scalar_map / default_value VARCHAR to the param's Python type."""
    if param_type == "int":
        return int(value)
    if param_type == "float":
        return float(value)
    if param_type == "bool":
        return str(value).strip().lower() in ("true", "1", "yes")
    return value  # str


def resolve_scalar_kwargs(params: list[dict]) -> dict:
    """Resolve non-column scalar params to ``{param_name: value}`` for broadcast into
    every argument bundle (#258).

    A scalar param is one whose type is int/float/str/bool and which has NO column
    bindings (a column-bound param is the bundle column, passed separately; pd.Series /
    pd.DataFrame are handled elsewhere). Its value is the persisted source_scalar_map
    value, else the captured Python default. A param with neither raises
    RequiredParamError — the function genuinely cannot run.
    """
    extra: dict = {}
    for p in params:
        if p.get("bindings"):
            continue  # column-bound — passed as the bundle column, not a scalar
        if p["param_type"] not in ("int", "float", "str", "bool"):
            continue  # pd.Series / pd.DataFrame
        raw = p.get("scalar_value")
        if raw is None and p.get("has_default"):
            raw = p.get("default_value")
        if raw is None:
            raise RequiredParamError(p["param_name"])
        extra[p["param_name"]] = _coerce_scalar(raw, p["param_type"])
    return extra


# ---------------------------------------------------------------------------
# SQL function execution
# ---------------------------------------------------------------------------

def _execute_sql_function(
    conn: duckdb.DuckDBPyConnection,
    module_path: str,
    source_id: uuid.UUID,
) -> "pd.DataFrame | FailedFunctionEntry":
    """Execute a SQL function by substituting {source_table} and running on DuckDB.

    Returns a DataFrame on success or a FailedFunctionEntry on error.
    """
    try:
        sql_source = Path(module_path).read_text(encoding="utf-8")
    except OSError as exc:
        entry = FailedFunctionEntry()
        entry.add("sql_read", f"cannot read SQL file: {exc}")
        return entry

    # Strip leading comment header lines to get the actual SQL body
    body_lines = [ln for ln in sql_source.splitlines() if not ln.strip().startswith("--")]
    sql_body = "\n".join(body_lines).strip()

    if not sql_body:
        entry = FailedFunctionEntry()
        entry.add("sql_empty", "SQL file contains no query after header comments")
        return entry

    tname = instance_table_name(source_id)
    sql = sql_body.replace("{source_table}", f'"{tname}"')

    try:
        return conn.execute(sql).df()
    except Exception as exc:
        entry = FailedFunctionEntry()
        entry.add("sql_exec", str(exc))
        return entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _staging_prefix(source_id: uuid.UUID) -> str:
    return f"staging_{source_id.hex[:8]}_"


def _drop_prior_staging_tables(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> None:
    """Drop all prior staging tables for this source."""
    prefix = _staging_prefix(source_id)
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchall()
    for (tname,) in rows:
        if tname.startswith(prefix):
            conn.execute(f'DROP TABLE IF EXISTS "{tname}"')


def _write_staging_table(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    df: pd.DataFrame,
    timestamp: int,
) -> str:
    """Write df to a new staging table; return the table name."""
    tname = f"{_staging_prefix(source_id)}{timestamp}"
    conn.execute(f'DROP TABLE IF EXISTS "{tname}"')
    conn.execute(f'CREATE TABLE "{tname}" AS SELECT * FROM df')
    return tname


def _load_instance_table(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> pd.DataFrame:
    """Load the full source instance table as a DataFrame."""
    tname = instance_table_name(source_id)
    return conn.execute(f'SELECT * FROM "{tname}"').df()


def _fetch_steps(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> list[dict]:
    """Return pipeline steps for a source, ordered by position.

    Each step dict has:
      source_function_map_id, set_id, set_name, position, output_mode, append_name,
      function_type (dominant type for the step),
      functions: [{ function_id, function_name, function_type,
                    function_class, function_return_type, module_path,
                    params: [{ param_id, param_name, param_type,
                               bindings: [column_name, ...] }] }]
    """
    set_rows = conn.execute(
        """
        SELECT
            sfm.source_function_map_id,
            fs.set_id,
            fs.set_name,
            sfm.position,
            sfm.output_mode,
            sfm.append_name
        FROM source_function_map sfm
        JOIN function_set fs ON fs.set_id = sfm.set_id
        WHERE sfm.source_id = ?
        ORDER BY sfm.position ASC
        """,
        [source_id],
    ).fetchall()

    steps = []
    for sfm_id, set_id, set_name, position, output_mode, append_name in set_rows:
        fn_rows = conn.execute(
            """
            SELECT
                fr.function_id,
                fr.function_name,
                fr.function_type,
                fr.function_class,
                fr.function_return_type,
                fr.module_path
            FROM function_set_map fsm
            JOIN function_registry fr ON fr.function_id = fsm.function_id
            WHERE fsm.set_id = ?
            ORDER BY fsm.position
            """,
            [set_id],
        ).fetchall()

        functions = []
        for fn_id, fn_name, fn_type, fn_class, fn_ret, module_path in fn_rows:
            param_rows = conn.execute(
                """
                SELECT p.param_id, p.param_name, p.param_type,
                       p.has_default, p.default_value,
                       cr.column_name, ssm.value AS scalar_value
                FROM parameter p
                LEFT JOIN alias_map am ON am.parameter_id = p.param_id
                    AND am.source_id = ?
                LEFT JOIN column_registry cr ON cr.column_id = am.column_id
                LEFT JOIN source_scalar_map ssm ON ssm.param_id = p.param_id
                    AND ssm.source_id = ?
                WHERE p.function_id = ?
                ORDER BY p.param_name, am.position
                """,
                [source_id, source_id, fn_id],
            ).fetchall()

            # Collapse multiple alias_map rows per param into a list of column names.
            # #258: also carry the persisted scalar value + Python default so the
            # executor can resolve and broadcast scalar params into every bundle.
            params_map: dict[str, dict] = {}
            for p_id, p_name, p_type, p_has_default, p_default, col_name, scalar_value in param_rows:
                key = str(p_id)
                if key not in params_map:
                    params_map[key] = {
                        "param_id": key,
                        "param_name": p_name,
                        "param_type": p_type,
                        "bindings": [],
                        "has_default": bool(p_has_default),
                        "default_value": p_default,
                        "scalar_value": scalar_value,
                    }
                if col_name is not None:
                    params_map[key]["bindings"].append(col_name)

            # Per-function output config (#264): output_mode / append_name / output_targets
            # belong to each function, not the whole set. Fall back to the step-level
            # source_function_map values for legacy rows with no function_output_config.
            cfg_row = conn.execute(
                "SELECT output_mode, append_name FROM function_output_config "
                "WHERE source_function_map_id = ? AND function_id = ?",
                [sfm_id, fn_id],
            ).fetchone()
            fn_output_mode = cfg_row[0] if cfg_row else output_mode
            fn_append_name = cfg_row[1] if cfg_row else append_name
            fn_target_rows = conn.execute(
                """
                SELECT cr.column_name
                FROM output_target_map otm
                JOIN column_registry cr ON cr.column_id = otm.column_id
                WHERE otm.source_function_map_id = ? AND otm.function_id = ?
                ORDER BY otm.position
                """,
                [sfm_id, fn_id],
            ).fetchall()

            functions.append({
                "function_id": str(fn_id),
                "function_name": fn_name,
                "function_type": fn_type,
                "function_class": fn_class,
                "function_return_type": fn_ret,
                "module_path": module_path,
                "params": list(params_map.values()),
                "output_mode": fn_output_mode,
                "append_name": fn_append_name,
                "output_targets": [r[0] for r in fn_target_rows],
            })

        # Derive the step's dominant function_type
        fn_types = {f["function_type"] for f in functions}
        if "transform" in fn_types:
            step_function_type = "transform"
        elif "validation" in fn_types:
            step_function_type = "validation"
        else:
            step_function_type = "unknown"

        # Output-target columns for a `replace` transform step, in position order
        # (bundle i -> target i). Empty for append steps and replace-with-default.
        target_rows = conn.execute(
            """
            SELECT cr.column_name
            FROM output_target_map otm
            JOIN column_registry cr ON cr.column_id = otm.column_id
            WHERE otm.source_function_map_id = ?
            ORDER BY otm.position
            """,
            [sfm_id],
        ).fetchall()
        output_targets = [r[0] for r in target_rows]

        steps.append({
            "source_function_map_id": str(sfm_id),
            "set_id": str(set_id),
            "set_name": set_name,
            "position": position,
            "output_mode": output_mode,
            "append_name": append_name,
            "function_type": step_function_type,
            "output_targets": output_targets,
            "functions": functions,
        })

    return steps


def _unique_column_name(name: str, existing: set[str]) -> str:
    """Return a column name that does not collide with `existing`, suffixing _2, _3…

    Append mode must never clobber an existing column (the no-collision guarantee).
    """
    if name not in existing:
        return name
    i = 2
    while f"{name}_{i}" in existing:
        i += 1
    return f"{name}_{i}"


def _normalize_to_series(result, n_rows: int) -> pd.Series:
    """Normalize a worker result (pd.Series or scalar) to a row-aligned Series.

    A scalar is broadcast across all rows; a Series is index-reset for alignment.
    """
    if isinstance(result, pd.Series):
        return result.reset_index(drop=True)
    return pd.Series([result] * n_rows)


def _execute_transform_step(
    working: pd.DataFrame,
    step: dict,
    conn: duckdb.DuckDBPyConnection | None = None,
    source_id: uuid.UUID | None = None,
) -> tuple[pd.DataFrame, str | None, list[dict]]:
    """Execute all functions in a transform step against the working table.

    Returns (new_working_table, error_message_or_None, run_results).

    A column-bound transform expands into argument bundles (§12 / ADR-0001): the
    function runs once per bundle's column, and `output_mode` decides write-back:
      - **append** → each bundle adds a NEW column, named by the user-provided append
        name (collision-suffixed) or a cleaned auto-label of the varying column.
      - **replace** → bundle i overwrites the i-th output-target column
        (`step["output_targets"]`, in position order); with no explicit targets a
        single-varying step defaults to its input column.
    A `pd.DataFrame` transform runs once over the whole table regardless of
    output_mode (no bundle expansion). Each run yields one RunResult dict.
    On error the original working table is returned unchanged.
    conn and source_id are required for SQL function execution.
    """
    current = working
    run_results: list[dict] = []
    # #264: output config (output_mode / append_name / output_targets) is per-FUNCTION,
    # resolved inside the loop from each fn dict; the step-level value is the legacy fallback.
    step_output_mode = step["output_mode"]

    def _emit(*, fn_name, bound_col, status, error):
        bundle_key = bound_col or ""
        label_seed = bound_col if bound_col else fn_name
        rr = RunResult(
            function_name=fn_name,
            function_type="transform",
            source_id=source_id if source_id is not None else uuid.UUID(int=0),
            bundle_key=bundle_key,
            label=normalize_label(label_seed),
            status=status,
            error=error,
        )
        run_results.append(rr.to_dict())

    for fn in step["functions"]:
        if fn["function_type"] != "transform":
            continue

        module_path = fn["module_path"]
        fn_name = fn["function_name"]
        fn_class = fn["function_class"]
        # #264: this function's own output config (per-function, legacy step fallback).
        output_mode = fn.get("output_mode", step_output_mode)
        append_name = fn.get("append_name")
        output_targets = fn.get("output_targets") or []

        # SQL functions: execute directly on DuckDB connection (whole-table, one run).
        if module_path and module_path.endswith(".sql"):
            if conn is None or source_id is None:
                return working, "SQL function execution requires conn and source_id", run_results
            result = _execute_sql_function(conn, module_path, source_id)
            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "SQL execution failed"
                return working, errors, run_results
            current = result
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            return working, f"cannot read module: {exc}", run_results

        # #258: resolve scalar params once; broadcast into every call/bundle. A
        # required param with no value/default fails this function cleanly (the step
        # continues so other functions still run).
        params = fn["params"]
        try:
            extra_kwargs = resolve_scalar_kwargs(params)
        except RequiredParamError as exc:
            _emit(fn_name=fn_name, bound_col=None, status="failed", error=str(exc))
            continue

        # pd.DataFrame transform: whole table in ONE run, regardless of output_mode.
        if fn_class == "pd.dataframe":
            kwarg_name = "df"
            for p in params:
                if p["param_type"] == "pd.DataFrame":
                    kwarg_name = p["param_name"]
                    break
            result = call_function(fn_source, fn_name, kwarg_name, current, extra_kwargs=extra_kwargs)
            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
                return working, errors, run_results
            if isinstance(result, pd.DataFrame):
                current = result
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        # Column-bound transform: resolve the bound param + its ordered columns.
        bound_param = None
        kwarg_name = params[0]["param_name"] if params else "data"
        for p in params:
            if p["param_type"] == "pd.Series":
                if not p["bindings"]:
                    return working, f"param '{p['param_name']}' is unbound — attach a column binding first", run_results
                bound_param = p
                kwarg_name = p["param_name"]
                break
            elif p["param_type"] in ("str", "int", "float", "bool") and p["bindings"]:
                bound_param = p
                kwarg_name = p["param_name"]
                break

        if bound_param is None:
            # No bound column — pass the full working table once (scalar param defaults).
            result = call_function(fn_source, fn_name, kwarg_name, current, extra_kwargs=extra_kwargs)
            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
                return working, errors, run_results
            if isinstance(result, pd.DataFrame):
                current = result
            else:
                current = current.copy()
                series = _normalize_to_series(result, len(current))
                new_col = _unique_column_name(
                    append_name or normalize_label(fn_name), set(current.columns)
                )
                current[new_col] = series.values
            _emit(fn_name=fn_name, bound_col=None, status="ok", error=None)
            continue

        # Pair the bound columns into argument bundles (one varying param → N bundles,
        # in user-placed/position order). N=1 is the single-column special case.
        bundles = pair_bundles([
            {"param_id": bound_param["param_id"], "columns": list(bound_param["bindings"])}
        ])
        is_scalar_shape = bound_param["param_type"] in ("str", "int", "float", "bool")

        # Bundles read their INPUT columns from the table as it was at the start of
        # this function's run — not from the progressively-mutated `current`. A
        # replace target can overlap another bundle's input column, so reading from
        # `current` would feed an already-overwritten value into a later bundle.
        fn_input = current.copy()

        for i, bundle in enumerate(bundles):
            bound_col = bundle.columns[bound_param["param_id"]]
            if bound_col not in fn_input.columns:
                return (
                    working,
                    f"bound column '{bound_col}' not found in source data — detach and re-attach the function to refresh the binding",
                    run_results,
                )

            column_series = fn_input[bound_col]
            if is_scalar_shape:
                # #258: forward the broadcast scalar kwargs through the element-wise wrapper.
                wrapper = (
                    "import pandas as _pd\n"
                    "def __wrapper__(series, **__extra):\n"
                    f"    return series.apply(lambda v: {fn_name}(**{{'{kwarg_name}': None if _pd.isna(v) else v}}, **__extra))\n"
                )
                result = call_function(wrapper + "\n" + fn_source, "__wrapper__", "series", column_series, extra_kwargs=extra_kwargs)
            else:
                result = call_function(fn_source, fn_name, kwarg_name, column_series, extra_kwargs=extra_kwargs)

            if isinstance(result, FailedFunctionEntry):
                errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
                return working, errors, run_results

            current = current.copy()
            series = _normalize_to_series(result, len(current))

            if output_mode == "replace":
                # bundle i -> target i (output_targets in position order); with no
                # explicit target, default to overwriting the input varying column.
                target_col = output_targets[i] if i < len(output_targets) else bound_col
                if target_col in current.columns:
                    current[target_col] = series.values
                else:
                    current[target_col] = series.values
            else:
                # append: a new column per bundle, never clobbering an existing one.
                # #264: default auto-label = function name + the column it ran on
                # (e.g. uppercase_email), so N appends are self-describing and distinct;
                # a user-provided append name overrides it. Collision suffix is a last
                # resort only (same fn+col twice).
                base_name = append_name or normalize_label(f"{fn_name}_{bound_col}")
                new_col = _unique_column_name(base_name, set(current.columns))
                current[new_col] = series.values

            _emit(fn_name=fn_name, bound_col=bound_col, status="ok", error=None)

    return current, None, run_results


def _validation_runresult(
    *,
    fn_name: str,
    source_id: uuid.UUID | None,
    bound_col: str | None,
    status: str,
    rows_passed: int | None,
    rows_failed: int | None,
    failing_rows: list[dict],
    error: str | None,
) -> ValidationRunResult:
    """Build a ValidationRunResult for one validation function run (one N=1 bundle).

    The argument bundle for the single-column path is the bound column name (or "")
    so the UUID5 identity is stable per (function, bound column, source). The label is
    the normalized bound column name, falling back to the function name.
    """
    bundle_key = bound_col or ""
    label_seed = bound_col if bound_col else fn_name
    return ValidationRunResult(
        function_name=fn_name,
        function_type="validation",
        source_id=source_id if source_id is not None else uuid.UUID(int=0),
        bundle_key=bundle_key,
        label=normalize_label(label_seed),
        status=status,
        error=error,
        rows_passed=rows_passed,
        rows_failed=rows_failed,
        failing_rows=failing_rows,
    )


def _step_transform_function(step: dict) -> dict | None:
    """Return the first transform function in a step (the N=1 step's function)."""
    for fn in step.get("functions", []):
        if fn.get("function_type") == "transform":
            return fn
    return None


def _first_bound_column(fn: dict | None) -> str | None:
    """Return the first bound column across a function's params (N=1 bundle key)."""
    if not fn:
        return None
    for p in fn.get("params", []):
        if p.get("bindings"):
            return p["bindings"][0]
    return None


def _transform_runresult(
    step: dict,
    source_id: uuid.UUID | None,
    *,
    status: str,
    error: str | None,
) -> RunResult:
    """Build the RunResult for a transform step (one N=1 argument bundle).

    The bundle key is the first bound column (or the function name when the step
    binds no column, e.g. a pd.DataFrame transform). The label is normalized.
    """
    fn = _step_transform_function(step)
    fn_name = fn["function_name"] if fn else (step.get("set_name") or "transform")
    bound_col = _first_bound_column(fn)
    bundle_key = bound_col or ""
    label_seed = bound_col if bound_col else fn_name
    return RunResult(
        function_name=fn_name,
        function_type="transform",
        source_id=source_id if source_id is not None else uuid.UUID(int=0),
        bundle_key=bundle_key,
        label=normalize_label(label_seed),
        status=status,
        error=error,
    )


def _execute_validation_step(
    original: pd.DataFrame,
    step: dict,
    conn: duckdb.DuckDBPyConnection | None = None,
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    """Execute all validation functions in a step against the original table.

    Each validation function produces one ValidationRunResult (the N=1 argument
    bundle). The returned dicts preserve the legacy wire keys (function_id,
    function_name, set_name, set_id, status, rows_passed, rows_failed, pass_rate,
    failing_rows, error) and additively carry the RunResult identity (result_id)
    and normalized label via ValidationRunResult.to_dict().
    conn and source_id are required for SQL function execution.
    """
    results = []
    set_name = step["set_name"]
    set_id = step["set_id"]

    def _emit(*, fn_id, fn_name, bound_col, status, rows_passed, rows_failed,
              failing_rows, error):
        rr = _validation_runresult(
            fn_name=fn_name, source_id=source_id, bound_col=bound_col,
            status=status, rows_passed=rows_passed, rows_failed=rows_failed,
            failing_rows=failing_rows, error=error,
        )
        entry = {
            "function_id": fn_id,
            "function_name": fn_name,
            "set_name": set_name,
            "set_id": set_id,
        }
        # RunResult is the source of truth for type/status/counts/identity/label.
        entry.update(rr.to_dict())
        return entry

    for fn in step["functions"]:
        if fn["function_type"] != "validation":
            continue

        fn_id = fn["function_id"]
        fn_name = fn["function_name"]
        fn_class = fn["function_class"]
        module_path = fn["module_path"]

        # SQL functions: execute directly on DuckDB connection (no column expansion).
        if module_path and module_path.endswith(".sql"):
            if conn is None or source_id is None:
                results.append(_emit(
                    fn_id=fn_id, fn_name=fn_name, bound_col=None,
                    status="failed", rows_passed=None, rows_failed=None,
                    failing_rows=[],
                    error="SQL function execution requires conn and source_id",
                ))
                continue
            sql_result = _execute_sql_function(conn, module_path, source_id)
            results.append(_interpret_validation_result(
                sql_result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[], error=f"cannot read module: {exc}",
            ))
            continue

        params = fn["params"]

        # #258: resolve scalar params once and broadcast them into every call/bundle.
        # A required param with no value and no default fails the function cleanly.
        try:
            extra_kwargs = resolve_scalar_kwargs(params)
        except RequiredParamError as exc:
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[], error=str(exc),
            ))
            continue

        # pd.DataFrame functions: the full table is passed once (no column expansion).
        if fn_class == "pd.dataframe":
            kwarg_name = "df"
            for p in params:
                if p["param_type"] == "pd.DataFrame":
                    kwarg_name = p["param_name"]
                    break
            df_result = call_function(fn_source, fn_name, kwarg_name, original, extra_kwargs=extra_kwargs)
            results.append(_interpret_validation_result(
                df_result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        # Column-bound function: resolve the column-eligible param and its bindings.
        # An eligible param bound to N columns expands into N argument bundles
        # (multi_select_eligible); the function runs once per bundle's column,
        # producing one RunResult per bundle (§12 / ADR-0001). N=1 is the single-
        # column path. A scalar-shaped param (str/int/float/bool) still runs per
        # record (the scalar run) for each of its bound columns.
        kwarg_name = params[0]["param_name"] if params else "data"
        bound_param = None
        unbound_series_param = None

        for p in params:
            if p["param_type"] == "pd.Series":
                if p["bindings"]:
                    kwarg_name = p["param_name"]
                    bound_param = p
                else:
                    unbound_series_param = p
                break
            elif p["param_type"] in ("str", "int", "float", "bool") and p["bindings"]:
                kwarg_name = p["param_name"]
                bound_param = p
                break

        if unbound_series_param is not None:
            # pd.Series with no binding — hard fail.
            results.append(_emit(
                fn_id=fn_id, fn_name=fn_name, bound_col=None,
                status="failed", rows_passed=None, rows_failed=None,
                failing_rows=[],
                error=f"param '{unbound_series_param['param_name']}' is unbound — attach a column binding first",
            ))
            continue

        if bound_param is None:
            # No bound column param — pass the full original table once.
            scalar_result = call_function(fn_source, fn_name, kwarg_name, original, extra_kwargs=extra_kwargs)
            results.append(_interpret_validation_result(
                scalar_result, original, fn_id=fn_id, fn_name=fn_name, bound_col=None, emit=_emit,
            ))
            continue

        # Pair the bound columns into argument bundles. A single eligible param with
        # N columns yields N single-column bundles, in user-placed (position) order.
        bundles = pair_bundles([
            {"param_id": bound_param["param_id"], "columns": list(bound_param["bindings"])}
        ])
        is_scalar_shape = bound_param["param_type"] in ("str", "int", "float", "bool")

        for bundle in bundles:
            bound_col = bundle.columns[bound_param["param_id"]]
            results.append(_run_validation_bundle(
                fn_source=fn_source, fn_name=fn_name, fn_id=fn_id,
                kwarg_name=kwarg_name, bound_col=bound_col, is_scalar_shape=is_scalar_shape,
                original=original, emit=_emit, extra_kwargs=extra_kwargs,
            ))

    return results


def _run_validation_bundle(
    *, fn_source, fn_name, fn_id, kwarg_name, bound_col, is_scalar_shape, original, emit,
    extra_kwargs=None,
):
    """Run a validation function for one argument bundle (one bound column) and emit.

    A scalar-shaped param dispatches element-wise via the .apply() wrapper (the scalar
    run — once per record); a pd.Series param receives the column as a Series. A bound
    column missing from the loaded table hard-fails with a refresh-the-binding diagnostic.
    """
    if bound_col not in original.columns:
        return emit(
            fn_id=fn_id, fn_name=fn_name, bound_col=bound_col,
            status="failed", rows_passed=None, rows_failed=None, failing_rows=[],
            error=f"bound column '{bound_col}' not found in source data — detach and re-attach the function to refresh the binding",
        )

    column_series = original[bound_col]
    if is_scalar_shape:
        # Element-wise dispatch (scalar run). Pandas NULL is float NaN in object/string
        # columns; convert to None so user functions receive a proper null sentinel.
        # #258: __wrapper__ accepts the broadcast scalar kwargs and forwards them
        # into the element-wise call so scalar params reach the user function.
        wrapper = (
            "import pandas as _pd\n"
            "def __wrapper__(series, **__extra):\n"
            f"    return series.apply(lambda v: {fn_name}(**{{'{kwarg_name}': None if _pd.isna(v) else v}}, **__extra))\n"
        )
        result = call_function(wrapper + "\n" + fn_source, "__wrapper__", "series", column_series, extra_kwargs=extra_kwargs)
    else:
        result = call_function(fn_source, fn_name, kwarg_name, column_series, extra_kwargs=extra_kwargs)

    return _interpret_validation_result(
        result, original, fn_id=fn_id, fn_name=fn_name, bound_col=bound_col, emit=emit,
    )


def _interpret_validation_result(result, original, *, fn_id, fn_name, bound_col, emit):
    """Normalize a validation worker result to pass/fail counts + failing rows, then emit.

    Accepts a pd.Series/pd.DataFrame boolean vector (the scalar-run-normalized output),
    a bare bool, or a FailedFunctionEntry. Returns the emit-dict for one RunResult.
    """
    if isinstance(result, FailedFunctionEntry):
        error_msg = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
        return emit(
            fn_id=fn_id, fn_name=fn_name, bound_col=bound_col,
            status="failed", rows_passed=None, rows_failed=None,
            failing_rows=[], error=error_msg,
        )

    failing_mask = None
    if isinstance(result, pd.Series):
        bool_series = result.reset_index(drop=True).astype(bool)
        passed = int(bool_series.sum())
        failed = len(bool_series) - passed
        failing_mask = ~bool_series
    elif isinstance(result, pd.DataFrame):
        bool_col = result.iloc[:, 0].astype(bool).reset_index(drop=True)
        passed = int(bool_col.sum())
        failed = len(bool_col) - passed
        failing_mask = ~bool_col
    elif isinstance(result, bool):
        passed = 1 if result else 0
        failed = 0 if result else 1
        failing_mask = None  # scalar: no individual rows to surface
    else:
        passed = 0
        failed = 0
        failing_mask = None

    # Collect failing rows (full row dicts, uncapped). DuckDB's .df() converts NULL
    # to float NaN; replace with None for JSON safety.
    if failing_mask is not None and failed > 0:
        original_reset = original.reset_index(drop=True)
        raw_rows = original_reset[failing_mask].to_dict(orient="records")
        failing_rows = [
            {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()}
            for row in raw_rows
        ]
    else:
        failing_rows = []

    return emit(
        fn_id=fn_id, fn_name=fn_name, bound_col=bound_col,
        status="ok", rows_passed=passed, rows_failed=failed,
        failing_rows=failing_rows, error=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _json_safe(v):
    """Convert one dataframe cell to a JSON-encodable value (#262).

    Pandas nulls become float NaN / NaT and DuckDB DOUBLEs can be inf — none of
    which stdlib JSON can encode, so a transformed-report export over real
    null-containing data 500s without this. NaN/NaT/None/inf -> None; numpy
    scalars -> Python natives.
    """
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass  # array-like / unhashable — not a scalar null
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def get_staging_rows(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> dict:
    """Return the most recent staging table rows for a source.

    Finds the staging table with the highest timestamp suffix (the part after
    the last '_' in staging_{source_id_short}_{timestamp}).

    Returns {"columns": [...], "rows": [...]} — empty lists if no staging
    table exists yet (not an error).
    """
    prefix = _staging_prefix(source_id)
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    ).fetchall()

    candidates = []
    for (tname,) in rows:
        if tname.startswith(prefix):
            suffix = tname[len(prefix):]
            try:
                ts = int(suffix)
                candidates.append((ts, tname))
            except ValueError:
                pass

    if not candidates:
        return {"columns": [], "rows": []}

    # Pick the table with the highest timestamp
    candidates.sort(key=lambda x: x[0])
    latest_tname = candidates[-1][1]

    df = conn.execute(f'SELECT * FROM "{latest_tname}"').df()
    columns = list(df.columns)
    data_rows = df.to_dict(orient="records")
    serialisable_rows = [
        {k: _json_safe(v) for k, v in row.items()} for row in data_rows
    ]
    return {"columns": columns, "rows": serialisable_rows}


def _step_has(step: dict, function_type: str) -> bool:
    """#266: a function set is a transparent container — a step 'has' a type when ANY
    of its functions is of that type. Routing reads this, not a single dominant type,
    so a mixed set runs both its transforms and its validations."""
    return any(f.get("function_type") == function_type for f in step.get("functions", []))


def run_pipeline(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    run_type: str,
    *,
    set_id: Optional[uuid.UUID] = None,
) -> dict | None:
    """Execute the pipeline for a source.

    Returns None if source_id is not found.
    Returns { run_type, steps: [...] } on completion.

    run_type values: "transforms", "validations", "set", "all"
    When run_type="set", set_id must be provided.
    """
    # Verify source exists
    src = conn.execute(
        "SELECT source_id FROM source_registry WHERE source_id = ?", [source_id]
    ).fetchone()
    if src is None:
        return None

    steps = _fetch_steps(conn, source_id)

    # Filter steps based on run_type. #266: a step qualifies when it CONTAINS a function
    # of the requested type (not by a single dominant type), so a mixed/multi-function
    # set is never excluded for the functions it does hold.
    if run_type == "transforms":
        active_steps = [s for s in steps if _step_has(s, "transform")]
    elif run_type == "validations":
        active_steps = [s for s in steps if _step_has(s, "validation")]
    elif run_type == "set":
        if set_id is None:
            active_steps = []
        else:
            active_steps = [s for s in steps if s["set_id"] == str(set_id)]
    elif run_type == "all":
        active_steps = steps
    else:
        active_steps = steps

    # Which function types this run processes; each step runs every function of these
    # types that it holds (a set is a transparent container).
    want_transforms = run_type in ("transforms", "all", "set")
    want_validations = run_type in ("validations", "all", "set")

    # Load the source instance table
    try:
        original_df = _load_instance_table(conn, source_id)
    except Exception as exc:
        return {
            "run_type": run_type,
            "steps": [],
            "error": f"Failed to load instance table: {exc}",
        }

    working_df = original_df.copy()

    # Drop prior staging tables (only when we'll write transforms)
    has_transforms = want_transforms and any(_step_has(s, "transform") for s in active_steps)
    if has_transforms:
        _drop_prior_staging_tables(conn, source_id)

    ts = int(time.time())
    step_results = []

    for step in active_steps:
        sfm_id = step["source_function_map_id"]
        set_name = step["set_name"]

        # #266: process EVERY function the step holds, each by its own type — transforms
        # (chain working_df, write staging) then validations (read original_df). A mixed
        # set thus runs both; the executors each filter to their own function type.
        if want_transforms and _step_has(step, "transform"):
            new_working, error, run_results = _execute_transform_step(
                working_df, step, conn=conn, source_id=source_id
            )
            if error:
                # A failed step surfaces one error entry (the step did not complete).
                # Prefer the failing run's identity when the executor produced one.
                tr = _transform_runresult(step, source_id, status="failed", error=error)
                entry = {
                    "source_function_map_id": sfm_id,
                    "set_name": set_name,
                    "rows_affected": None,
                    "rows_passed": None,
                    "rows_failed": None,
                }
                entry.update(tr.to_dict())
                step_results.append(entry)
            else:
                working_df = new_working
                _write_staging_table(conn, source_id, working_df, ts)
                # One result entry per bundle (per transform run). Fall back to the
                # step-level RunResult when a step produced no per-bundle runs.
                emitted = run_results or [
                    _transform_runresult(step, source_id, status="ok", error=None).to_dict()
                ]
                for rr in emitted:
                    entry = {
                        "source_function_map_id": sfm_id,
                        "set_name": set_name,
                        "rows_affected": len(working_df),
                        "rows_passed": None,
                        "rows_failed": None,
                    }
                    entry.update(rr)
                    step_results.append(entry)

        if want_validations and _step_has(step, "validation"):
            fn_results = _execute_validation_step(original_df, step, conn=conn, source_id=source_id)
            step_results.extend(fn_results)

    return {
        "run_type": run_type,
        "steps": step_results,
    }


def run_validation_across_sources(
    conn: duckdb.DuckDBPyConnection,
    function_id: uuid.UUID,
) -> dict | None:
    """Run a validation function across all sources it is attached to.

    Returns None if function_id is not found in function_registry.
    Returns { function_id, function_name, sources: [...] } on completion.

    Each source entry has:
      source_id, source_name, status, rows_passed, rows_failed,
      pass_rate, failing_rows, error

    A worker crash on one source marks that entry status="failed" without
    blocking the remaining sources.
    """
    # Verify function exists and is a validation function
    fn_row = conn.execute(
        "SELECT function_id, function_name FROM function_registry WHERE function_id = ?",
        [function_id],
    ).fetchone()
    if fn_row is None:
        return None

    fn_id_str, fn_name = str(fn_row[0]), fn_row[1]

    # Find all sources attached to this function via function_set_map + source_function_map
    source_rows = conn.execute(
        """
        SELECT DISTINCT sfm.source_id, sr.source_name
        FROM function_set_map fsm
        JOIN source_function_map sfm ON sfm.set_id = fsm.set_id
        JOIN source_registry sr ON sr.source_id = sfm.source_id
        WHERE fsm.function_id = ?
        ORDER BY sr.source_name
        """,
        [function_id],
    ).fetchall()

    source_results = []
    for (source_id_raw, source_name) in source_rows:
        source_id = uuid.UUID(str(source_id_raw))
        try:
            # Run validations for this source (validation-type run)
            result = run_pipeline(conn, source_id, "validations")
            if result is None:
                source_results.append({
                    "source_id": str(source_id),
                    "source_name": source_name,
                    "status": "failed",
                    "rows_passed": None,
                    "rows_failed": None,
                    "pass_rate": None,
                    "failing_rows": [],
                    "error": "Source not found during run",
                })
                continue

            # Find results for this specific function
            fn_steps = [s for s in (result.get("steps") or []) if s.get("function_id") == fn_id_str]

            if not fn_steps:
                # Function attached via set but produced no results (may be filtered out)
                source_results.append({
                    "source_id": str(source_id),
                    "source_name": source_name,
                    "status": "ok",
                    "rows_passed": None,
                    "rows_failed": None,
                    "pass_rate": None,
                    "failing_rows": [],
                    "error": None,
                })
                continue

            # Aggregate across multiple steps if the function appears more than once
            total_passed = 0
            total_failed = 0
            all_failing_rows: list[dict] = []
            any_error = None
            any_failed_status = False

            for step in fn_steps:
                if step.get("status") == "failed":
                    any_failed_status = True
                    any_error = step.get("error") or "worker failed"
                    continue
                rp = step.get("rows_passed") or 0
                rf = step.get("rows_failed") or 0
                total_passed += rp
                total_failed += rf
                all_failing_rows.extend(step.get("failing_rows") or [])

            if any_failed_status and total_passed == 0 and total_failed == 0:
                source_results.append({
                    "source_id": str(source_id),
                    "source_name": source_name,
                    "status": "failed",
                    "rows_passed": None,
                    "rows_failed": None,
                    "pass_rate": None,
                    "failing_rows": [],
                    "error": any_error,
                })
            else:
                total = total_passed + total_failed
                pass_rate = (total_passed / total) if total > 0 else None
                source_results.append({
                    "source_id": str(source_id),
                    "source_name": source_name,
                    "status": "failed" if any_failed_status else "ok",
                    "rows_passed": total_passed,
                    "rows_failed": total_failed,
                    "pass_rate": pass_rate,
                    "failing_rows": all_failing_rows,
                    "error": any_error,
                })

        except Exception as exc:
            source_results.append({
                "source_id": str(source_id),
                "source_name": source_name,
                "status": "failed",
                "rows_passed": None,
                "rows_failed": None,
                "pass_rate": None,
                "failing_rows": [],
                "error": str(exc),
            })

    # Each per-source entry IS a RunResult of this validation function on that source.
    # The argument bundle for a cross-source run keys on (function, source); the label
    # is the normalized source name so the results report stays well-formed.
    for entry in source_results:
        rr = RunResult(
            function_name=fn_name,
            function_type="validation",
            source_id=uuid.UUID(entry["source_id"]),
            bundle_key=entry["source_id"],
            label=normalize_label(entry.get("source_name") or fn_name),
            status=entry.get("status", "ok"),
            error=entry.get("error"),
        )
        entry.setdefault("result_id", rr.result_id)
        entry.setdefault("label", rr.label)

    return {
        "function_id": fn_id_str,
        "function_name": fn_name,
        "sources": source_results,
    }


def run_set_across_sources(
    conn: duckdb.DuckDBPyConnection,
    set_id: uuid.UUID,
) -> dict | None:
    """Run a function set across all sources it is attached to.

    Returns None if set_id is not found in function_set.
    Returns { set_id, set_name, sources: [...] } on completion.

    Each source entry has:
      source_id, source_name, steps: [...]

    A worker crash on one source marks that source's steps as failed without
    blocking the remaining sources.
    """
    # Verify set exists
    set_row = conn.execute(
        "SELECT set_id, set_name FROM function_set WHERE set_id = ?",
        [set_id],
    ).fetchone()
    if set_row is None:
        return None

    set_id_str, set_name = str(set_row[0]), set_row[1]

    # Find all sources attached to this set via source_function_map
    source_rows = conn.execute(
        """
        SELECT sfm.source_id, sr.source_name
        FROM source_function_map sfm
        JOIN source_registry sr ON sr.source_id = sfm.source_id
        WHERE sfm.set_id = ?
        ORDER BY sr.source_name
        """,
        [set_id],
    ).fetchall()

    source_results = []
    for (source_id_raw, source_name) in source_rows:
        source_id = uuid.UUID(str(source_id_raw))
        try:
            result = run_pipeline(conn, source_id, "set", set_id=set_id)
            if result is None:
                source_results.append({
                    "source_id": str(source_id),
                    "source_name": source_name,
                    "steps": [],
                    "error": "Source not found during run",
                })
                continue
            source_results.append({
                "source_id": str(source_id),
                "source_name": source_name,
                "steps": result.get("steps") or [],
                "error": result.get("error"),
            })
        except Exception as exc:
            source_results.append({
                "source_id": str(source_id),
                "source_name": source_name,
                "steps": [],
                "error": str(exc),
            })

    return {
        "set_id": set_id_str,
        "set_name": set_name,
        "sources": source_results,
    }
