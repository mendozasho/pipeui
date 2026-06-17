"""Input-source resolution — the runner seam that turns a ``(source, raw |
transformed)`` reference into a DataFrame plus a provenance ``ref``.

``resolve_frame`` is the single place where "where does this step's input come
from" is decided, so no executor hardcodes a table (CONTEXT.md → Input-source
resolution; PRD Implementation Decisions → Input-source resolution).

- **raw** resolves to the source's instance table (the original ingested data).
- **transformed** resolves to the source's latest transformed output — its most
  recent staging table — used as-is if present (snapshot semantics, no automatic
  refresh), else **materialized on demand** by running that source's pipeline once
  (materialize-if-absent). The materialize path is **cycle-guarded**: a
  transformed reference forming a cycle (A->C->A) raises ``TransformedCycleError``
  naming the sources rather than looping.

The transformed ``ref`` carries a deterministic ``UUID5`` ``result_id`` derived
with the same identity helper as ``RunResult`` (over source + mode + staging
timestamp), so a consumed transformed output is a first-class, traceable result.

This slice introduces the seam only; it does NOT change the join (slice 2).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import duckdb
import pandas as pd

from pipeui.results import transformed_result_id
from pipeui.sql_user_table import instance_table_name
from pipeui.workflow.staging import _latest_staging, _staging_prefix
from pipeui.workflow.step_loader import get_builtin_steps

RAW = "raw"
TRANSFORMED = "transformed"

# The injected "produce a source's transformed output" runner (DIP). resolve declares
# the callable signature; the orchestrator (run.py) supplies it — resolve never imports
# ``pipeui.workflow.run`` (CONTEXT.md → carriers → ``run_transforms`` behavioral port).
RunTransforms = Callable[[duckdb.DuckDBPyConnection, uuid.UUID], None]


class TransformedCycleError(Exception):
    """A transformed reference forms a cycle (e.g. A->C->A) — the materialize path
    would loop. Raised naming the sources involved so the run fails with an
    actionable message instead of hanging."""

    def __init__(self, cycle: list[uuid.UUID]):
        self.cycle = list(cycle)
        names = " -> ".join(str(s) for s in self.cycle)
        super().__init__(f"transformed-output cycle detected: {names}")


@dataclass(frozen=True)
class FrameRef:
    """Provenance for a resolved frame.

    ``mode`` is "raw" or "transformed"; ``source_id`` is the resolved source.
    ``result_id`` is the deterministic transformed-output identity (None for raw,
    which is the source's own data rather than a produced result). ``staging_table``
    names the materialized snapshot for a transformed frame (None for raw).
    """

    source_id: uuid.UUID
    mode: str
    result_id: Optional[str] = None
    staging_table: Optional[str] = None

    def __post_init__(self):
        """Enforce the carrier invariant: a raw frame is the source's own data (no
        produced ``result_id``); a transformed frame is a produced result (must carry
        one). ``result_id is None ⟺ mode == RAW`` — any other combination is an
        illegal carrier and is unconstructable (CONTEXT.md → carriers → FrameRef)."""
        if self.mode == RAW and self.result_id is not None:
            raise ValueError("FrameRef raw frame must not carry a result_id")
        if self.mode == TRANSFORMED and self.result_id is None:
            raise ValueError("FrameRef transformed frame must carry a result_id")


def _transformed_source_refs(
    conn: duckdb.DuckDBPyConnection, source_id: uuid.UUID
) -> list[uuid.UUID]:
    """Source ids this source reads in *transformed* mode (cycle-guard frontier).

    A transformed reference is a built-in join step whose config sets
    ``use_transformed`` against another source. This is the path a transformed-join
    cycle forms through, and is forward-compatible with slice 2 wiring the join.
    """
    refs: list[uuid.UUID] = []
    for step in get_builtin_steps(conn, source_id):
        if step.builtin_type != "join":
            continue
        cfg = step.builtin_config or {}
        if not cfg.get("use_transformed"):
            continue
        rsid = cfg.get("right_source_id")
        if rsid:
            refs.append(uuid.UUID(str(rsid)))
    return refs


def resolve_frame(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    mode: str,
    *,
    run_transforms: Optional[RunTransforms] = None,
    _in_progress: frozenset[uuid.UUID] = frozenset(),
) -> tuple[pd.DataFrame, FrameRef]:
    """Resolve a ``(source, raw | transformed)`` reference to ``(frame, ref)``.

    raw -> the source's instance table. transformed -> the latest staging table if
    present, else the source's pipeline is run once to materialize it. The
    materialize path is cycle-guarded (``TransformedCycleError``).

    ``run_transforms`` is the injected runner (DIP) used only on the materialize path
    — when a transformed reference has no staging table yet. It is required there;
    a raw reference or a transformed reference with existing staging never needs it.
    """
    if mode not in (RAW, TRANSFORMED):
        raise ValueError(f"mode must be {RAW!r} or {TRANSFORMED!r}; got {mode!r}")

    if mode == RAW:
        tname = instance_table_name(source_id)
        frame = conn.execute(f'SELECT * FROM "{tname}"').df()
        return frame, FrameRef(source_id=source_id, mode=RAW)

    # transformed: snapshot-if-present, else materialize-if-absent.
    latest = _latest_staging(conn, source_id)
    if latest is None:
        latest = _materialize(
            conn, source_id, run_transforms=run_transforms, _in_progress=_in_progress
        )

    tname, ts = latest
    frame = conn.execute(f'SELECT * FROM "{tname}"').df()
    ref = FrameRef(
        source_id=source_id,
        mode=TRANSFORMED,
        result_id=transformed_result_id(source_id, TRANSFORMED, ts),
        staging_table=tname,
    )
    return frame, ref


def _materialize(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
    *,
    run_transforms: Optional[RunTransforms],
    _in_progress: frozenset[uuid.UUID],
) -> tuple[str, int]:
    """Run the source's pipeline once to produce its transformed output.

    Cycle-guarded: re-entering a source already being materialized raises
    ``TransformedCycleError`` naming the cycle. Transformed-join dependencies are
    resolved first (recursing through ``resolve_frame``) so a cycle is caught
    before the producing run, never as a hang.

    The producing run is the injected ``run_transforms`` (DIP) — resolve never imports
    the orchestrator. If it is needed (a transformed reference with no staging table)
    but not supplied, raise a clear error rather than silently failing.
    """
    if run_transforms is None:
        raise RuntimeError(
            f"transformed output for source {source_id} must be materialized but no "
            "run_transforms runner was injected — pass run_transforms to resolve_frame"
        )

    if source_id in _in_progress:
        # Build the cycle path: the in-progress frontier plus the re-entered source.
        cycle = list(_in_progress) + [source_id]
        raise TransformedCycleError(cycle)

    next_in_progress = _in_progress | {source_id}

    # Resolve transformed dependencies first — this is where a cycle surfaces.
    for dep in _transformed_source_refs(conn, source_id):
        resolve_frame(
            conn, dep, TRANSFORMED,
            run_transforms=run_transforms, _in_progress=next_in_progress,
        )

    # Produce this source's transformed output (snapshot). Transforms write staging.
    run_transforms(conn, source_id)

    latest = _latest_staging(conn, source_id)
    if latest is None:
        # No transform steps produced output (e.g. a validations-only pipeline);
        # fall back to a staged copy of the instance table so transformed resolution
        # still yields a frame. DECIDED behavior (keep, not error): a source with
        # nothing to transform has its raw data as its "transformed output". Tested:
        # test_resolve_frame_transformed_no_transforms_falls_back_to_raw.
        import time

        tname = instance_table_name(source_id)
        ts = int(time.time())
        staged = f"{_staging_prefix(source_id)}{ts}"
        conn.execute(f'DROP TABLE IF EXISTS "{staged}"')
        conn.execute(f'CREATE TABLE "{staged}" AS SELECT * FROM "{tname}"')
        return staged, ts
    return latest
