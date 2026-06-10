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

import time
import uuid
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

from pipeui.sql_user_table import instance_table_name
from pipeui.validation.fails import FailedFunctionEntry
from pipeui.workflow.worker import call_function


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
      source_function_map_id, set_id, set_name, position, output_mode,
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
            sfm.output_mode
        FROM source_function_map sfm
        JOIN function_set fs ON fs.set_id = sfm.set_id
        WHERE sfm.source_id = ?
        ORDER BY sfm.position ASC
        """,
        [source_id],
    ).fetchall()

    steps = []
    for sfm_id, set_id, set_name, position, output_mode in set_rows:
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
                       cr.column_name
                FROM parameter p
                LEFT JOIN alias_map am ON am.parameter_id = p.param_id
                    AND am.source_id = ?
                LEFT JOIN column_registry cr ON cr.column_id = am.column_id
                WHERE p.function_id = ?
                ORDER BY p.param_name, cr.column_name
                """,
                [source_id, fn_id],
            ).fetchall()

            # Collapse multiple alias_map rows per param into a list of column names
            params_map: dict[str, dict] = {}
            for p_id, p_name, p_type, col_name in param_rows:
                key = str(p_id)
                if key not in params_map:
                    params_map[key] = {
                        "param_id": key,
                        "param_name": p_name,
                        "param_type": p_type,
                        "bindings": [],
                    }
                if col_name is not None:
                    params_map[key]["bindings"].append(col_name)

            functions.append({
                "function_id": str(fn_id),
                "function_name": fn_name,
                "function_type": fn_type,
                "function_class": fn_class,
                "function_return_type": fn_ret,
                "module_path": module_path,
                "params": list(params_map.values()),
            })

        # Derive the step's dominant function_type
        fn_types = {f["function_type"] for f in functions}
        if "transform" in fn_types:
            step_function_type = "transform"
        elif "validation" in fn_types:
            step_function_type = "validation"
        else:
            step_function_type = "unknown"

        steps.append({
            "source_function_map_id": str(sfm_id),
            "set_id": str(set_id),
            "set_name": set_name,
            "position": position,
            "output_mode": output_mode,
            "function_type": step_function_type,
            "functions": functions,
        })

    return steps


def _apply_output_mode(
    working: pd.DataFrame,
    result,
    output_mode: str,
    fn_name: str,
    bound_col: str | None,
) -> pd.DataFrame:
    """Apply output_mode logic to incorporate a function result into the working table.

    - pd.DataFrame result always replaces the full working table.
    - pd.Series result: append (new column named fn_name) or replace (overwrite bound_col).
    - scalar result: broadcast to all rows then append/replace.
    """
    if isinstance(result, pd.DataFrame):
        return result

    if isinstance(result, pd.Series):
        series = result.reset_index(drop=True)
    else:
        # scalar — broadcast
        series = pd.Series([result] * len(working))

    working = working.copy()
    if output_mode == "replace" and bound_col is not None and bound_col in working.columns:
        working[bound_col] = series.values
    else:
        working[fn_name] = series.values

    return working


def _execute_transform_step(
    working: pd.DataFrame,
    step: dict,
) -> tuple[pd.DataFrame, str | None]:
    """Execute all functions in a transform step against the working table.

    Returns (new_working_table, error_message_or_None).
    On error the original working table is returned unchanged.
    """
    current = working
    for fn in step["functions"]:
        if fn["function_type"] != "transform":
            continue

        module_path = fn["module_path"]
        fn_name = fn["function_name"]
        fn_class = fn["function_class"]
        output_mode = step["output_mode"]

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            return working, f"cannot read module: {exc}"

        if fn_class == "pd.dataframe":
            # Pass the full working table
            params = fn["params"]
            kwarg_name = "df"
            for p in params:
                if p["param_type"] == "pd.DataFrame":
                    kwarg_name = p["param_name"]
                    break
            result = call_function(fn_source, fn_name, kwarg_name, current)
            bound_col = None
        else:
            # Pass a series / scalar-column; use first bound column if available
            params = fn["params"]
            bound_col = None
            kwarg_name = params[0]["param_name"] if params else "data"
            for p in params:
                if p["param_type"] in ("pd.Series", "str") and p["bindings"]:
                    bound_col = p["bindings"][0]
                    kwarg_name = p["param_name"]
                    break

            if bound_col is not None and bound_col in current.columns:
                arg = current[bound_col]
            else:
                # scalar or unbound — pass the full table
                arg = current
                bound_col = None

            result = call_function(fn_source, fn_name, kwarg_name, arg)

        if isinstance(result, FailedFunctionEntry):
            errors = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
            return working, errors

        current = _apply_output_mode(current, result, output_mode, fn_name, bound_col)

    return current, None


def _execute_validation_step(
    original: pd.DataFrame,
    step: dict,
) -> list[dict]:
    """Execute all validation functions in a step against the original table.

    Returns a list of per-function result dicts, each with:
      function_id, function_name, set_name, set_id, status,
      rows_passed, rows_failed, pass_rate, failing_rows, error
    """
    results = []
    set_name = step["set_name"]
    set_id = step["set_id"]

    for fn in step["functions"]:
        if fn["function_type"] != "validation":
            continue

        fn_id = fn["function_id"]
        fn_name = fn["function_name"]
        fn_class = fn["function_class"]
        module_path = fn["module_path"]

        try:
            fn_source = Path(module_path).read_text(encoding="utf-8")
        except OSError as exc:
            results.append({
                "function_id": fn_id,
                "function_name": fn_name,
                "set_name": set_name,
                "set_id": set_id,
                "function_type": "validation",
                "status": "failed",
                "rows_passed": None,
                "rows_failed": None,
                "pass_rate": None,
                "failing_rows": [],
                "error": f"cannot read module: {exc}",
            })
            continue

        params = fn["params"]
        if fn_class == "pd.dataframe":
            kwarg_name = "df"
            for p in params:
                if p["param_type"] == "pd.DataFrame":
                    kwarg_name = p["param_name"]
                    break
            arg = original
        else:
            bound_col = None
            kwarg_name = params[0]["param_name"] if params else "data"
            for p in params:
                if p["param_type"] in ("pd.Series", "str") and p["bindings"]:
                    bound_col = p["bindings"][0]
                    kwarg_name = p["param_name"]
                    break
            arg = original[bound_col] if (bound_col and bound_col in original.columns) else original

        result = call_function(fn_source, fn_name, kwarg_name, arg)

        if isinstance(result, FailedFunctionEntry):
            error_msg = "; ".join(reason for _, reason in result.failures) if result.failures else "worker failed"
            results.append({
                "function_id": fn_id,
                "function_name": fn_name,
                "set_name": set_name,
                "set_id": set_id,
                "function_type": "validation",
                "status": "failed",
                "rows_passed": None,
                "rows_failed": None,
                "pass_rate": None,
                "failing_rows": [],
                "error": error_msg,
            })
            continue

        # Interpret boolean result and collect failing row indices
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

        # Collect failing rows (full row dicts, uncapped)
        if failing_mask is not None and failed > 0:
            original_reset = original.reset_index(drop=True)
            failing_rows = original_reset[failing_mask].to_dict(orient="records")
        else:
            failing_rows = []

        total = passed + failed
        pass_rate = (passed / total) if total > 0 else None

        results.append({
            "function_id": fn_id,
            "function_name": fn_name,
            "set_name": set_name,
            "set_id": set_id,
            "function_type": "validation",
            "status": "ok",
            "rows_passed": passed,
            "rows_failed": failed,
            "pass_rate": pass_rate,
            "failing_rows": failing_rows,
            "error": None,
        })

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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

    # Filter steps based on run_type
    if run_type == "transforms":
        active_steps = [s for s in steps if s["function_type"] == "transform"]
    elif run_type == "validations":
        active_steps = [s for s in steps if s["function_type"] == "validation"]
    elif run_type == "set":
        if set_id is None:
            active_steps = []
        else:
            active_steps = [s for s in steps if s["set_id"] == str(set_id)]
    elif run_type == "all":
        # Execute all steps regardless of function_type, preserving position order
        active_steps = steps
    else:
        active_steps = steps

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
    has_transforms = any(s["function_type"] == "transform" for s in active_steps)
    if has_transforms:
        _drop_prior_staging_tables(conn, source_id)

    ts = int(time.time())
    step_results = []

    for step in active_steps:
        sfm_id = step["source_function_map_id"]
        set_name = step["set_name"]
        fn_type = step["function_type"]

        if fn_type == "transform":
            new_working, error = _execute_transform_step(working_df, step)
            if error:
                step_results.append({
                    "source_function_map_id": sfm_id,
                    "set_name": set_name,
                    "function_type": fn_type,
                    "status": "failed",
                    "rows_affected": None,
                    "rows_passed": None,
                    "rows_failed": None,
                    "error": error,
                })
            else:
                working_df = new_working
                _write_staging_table(conn, source_id, working_df, ts)
                step_results.append({
                    "source_function_map_id": sfm_id,
                    "set_name": set_name,
                    "function_type": fn_type,
                    "status": "ok",
                    "rows_affected": len(working_df),
                    "rows_passed": None,
                    "rows_failed": None,
                    "error": None,
                })

        elif fn_type == "validation":
            fn_results = _execute_validation_step(original_df, step)
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

    return {
        "function_id": fn_id_str,
        "function_name": fn_name,
        "sources": source_results,
    }
