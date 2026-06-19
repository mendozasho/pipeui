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
from typing import Optional

import duckdb
import pandas as pd

from pipeui.backend.data.base.results import RunResult, normalize_label
from pipeui.backend.data.base.tables import instance_table_name
from pipeui.backend.domain.runner import executors as _executors
from pipeui.backend.domain.runner.executors import StepRunEnv, step_has
from pipeui.backend.data.runner.steps import BUILTIN
from pipeui.backend.data.runner.staging import (
    drop_prior_staging_tables,
    staging_prefix,
)
from pipeui.backend.data.runner.step_loader import fetch_steps, get_builtin_steps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_instance_table(conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID) -> pd.DataFrame:
    """Load the full source instance table as a DataFrame."""
    tname = instance_table_name(source_id)
    return conn.execute(f'SELECT * FROM "{tname}"').df()


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
    prefix = staging_prefix(source_id)
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

    # fetch_steps produces the typed FunctionStepContext carrier (with FunctionSpec
    # members); get_builtin_steps produces the typed BuiltinStepContext carrier. The
    # runner reads typed fields, never dict keys.
    steps = fetch_steps(conn, source_id)

    # Filter FUNCTION steps based on run_type. #266: a step qualifies when it CONTAINS a
    # function of the requested type (not by a single dominant type), so a mixed/multi-
    # function set is never excluded for the functions it does hold.
    if run_type == "transforms":
        fn_steps = [s for s in steps if step_has(s, "transform")]
    elif run_type == "validations":
        fn_steps = [s for s in steps if step_has(s, "validation")]
    elif run_type == "set":
        fn_steps = [s for s in steps if s.set_id == str(set_id)] if set_id is not None else []
    elif run_type == "all":
        fn_steps = steps
    else:
        fn_steps = steps

    # Built-in steps (join/pivot/filter) live in source_builtin_map and share the
    # position space. They reshape the working table, so they run as part of the
    # transform chain — on full-pipeline and transforms runs, not on a validations-only
    # or single-set run. Merge with function steps by position; Python's stable sort
    # keeps function steps ahead of a built-in that ties the same position.
    # #40: a rename built-in is PINNED LAST — it operates on the final output (incl.
    # joined columns), so it always executes after every other step regardless of its
    # stored position. The primary sort key flags rename; all other steps keep their
    # by-position order, so pipelines without a rename sort identically to before.
    # NOTE (#83): "rename" is a magic string repeated in 3 sort sites (here,
    # get_pipeline, get_unified_pipeline). When a 2nd pinned-* built-in lands, promote
    # this to a BuiltinSpec flag + one shared sort helper — do not add a third literal.
    want_builtins = run_type in ("transforms", "all")
    builtin_steps = get_builtin_steps(conn, source_id) if want_builtins else []
    active_steps = sorted(
        fn_steps + builtin_steps,
        key=lambda s: (1 if getattr(s, "builtin_type", None) == "rename" else 0, s.position),
    )

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

    # Drop prior staging tables when this run writes any (a transform step, or a
    # built-in step which also reshapes and stages the working table).
    writes_staging = any(
        s.step_type == BUILTIN
        or (want_transforms and step_has(s, "transform"))
        for s in active_steps
    )
    if writes_staging:
        drop_prior_staging_tables(conn, source_id)

    ts = int(time.time())
    step_results = []

    # Uniform dispatch (runner-resolution-model slice 3): every step is wrapped in a
    # StepContext and run through the StepExecutor resolved from STEP_EXECUTORS by its
    # step_type. This replaces the inline if/elif type branching; behavior is preserved
    # because each executor wraps the same helpers the inline branch used.
    # DIP wiring: the orchestrator supplies the "produce a source's transformed output"
    # runner to resolve (via the built-in join executor), so resolve never imports
    # run_pipeline. A transformed join's materialize path runs the right source's
    # transforms through this callable.
    env = StepRunEnv(
        conn=conn,
        source_id=source_id,
        original_df=original_df,
        ts=ts,
        want_transforms=want_transforms,
        want_validations=want_validations,
        run_transforms=lambda c, sid: run_pipeline(c, sid, "transforms"),
    )
    for ctx in active_steps:
        executor = _executors.STEP_EXECUTORS.get(ctx.step_type)
        if executor is None:
            # No registered executor for this step type — skip it (the registry is
            # the sole dispatch authority; an unregistered type produces no output).
            continue
        outcome = executor.execute(ctx, working_df, env)
        working_df = outcome.working
        # Serialize the typed StepResultEntry carriers to the wire dict here, at the
        # runner's published return (the api/export seam). Executors traffic in typed
        # carriers; only this boundary produces the external {"steps": [...]} dicts.
        step_results.extend(entry.to_dict() for entry in outcome.entries)

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
