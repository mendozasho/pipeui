---
feature_slug: runner-resolution-model
source_discovery_hash: ae9482eb20df4c0ab668e1c917d3e93c1e4fbb09d5ffdca5d2123e7acb3a4691
---

## Problem Statement

An analyst building a report can put a `Join` step on a source and flip a "Use
transformed output" toggle to join against another source's pipeline output
rather than its raw data. The toggle looks like it works — it is saved with the
join and the column picker even offers the other source's transformed columns —
but the join silently ignores it and always joins the **raw source**. So any
report that depends on another source's transforms (for example, columns the
other report renamed or cleaned) quietly produces the wrong result, with no error
to signal it. The capability was specified but only its front end shipped; the
runner half was never built.

Underneath that bug is a structural reason it was easy to miss: the runner has no
single place that decides *where a step's input comes from* (raw vs transformed),
and no uniform way to run the different kinds of steps — `function step`s,
`built-in step`s, and the members of a `function set` are each executed by
separate, branchy code paths. Every new behavior has to be threaded through each
path by hand, which is how the raw/transformed choice ended up wired in one path
and dropped in another.

## Solution

Establish a single **runner resolution model** in which two things are
first-class concepts the runner resolves, rather than ad-hoc branches:

- **Step type.** `function step`s and `built-in step`s run through one uniform
  `StepExecutor` contract, dispatched from a step-type registry. A `function set`
  is handled by an adapter that flattens it into individual member executions —
  the runner treats each member as if it were a single step on the source (a
  multi-function run). The contract is shaped to accept heterogeneous members so
  built-in members can be added later without re-plumbing it.
- **Input source.** A single `resolve_frame(source, raw | transformed)` seam
  turns a source reference into a frame. **Raw** is the source's instance table;
  **transformed** is its latest transformed output — used as-is if present, or
  materialized on demand by running that source's pipeline once when it has never
  been run (snapshot semantics, no automatic refresh, guarded against cycles).

With those in place, the `Join` step honors **Use transformed output** by reading
through `resolve_frame`, fixing the dead toggle. The analyst gets the behavior the
UI already promises: a report can join another report's transformed output and
see its transforms reflected in the final result. The workflows that run through
the runner are migrated onto the model, and the execution code this directly
supersedes is removed — each removal guarded by a test proving behavior is
unchanged.

## User Stories

1. As an analyst, I want a `Join` step set to "Use transformed output" to actually
   join against the other source's transformed output, so that my report reflects
   the other report's transforms instead of silently joining its raw data.
2. As an analyst, I want "Use transformed output" off to keep joining the other
   source's raw instance table, so that raw remains the safe, explicit default.
3. As an analyst, I want a join against a transformed source that has never been
   run to run that source's pipeline on demand and use its output, so that I don't
   have to manually run sources in a particular order first.
4. As an analyst, I want a join against a transformed source to use whatever that
   source last produced (a snapshot), so that the result is predictable; refreshing
   it is simply re-running that source.
5. As an analyst, I want a transformed-join cycle (two sources each joining the
   other's transformed output) to fail with a clear message naming the sources,
   so that I get an actionable error instead of a hang.
6. As an analyst, I want the join column picker to show the other source's
   transformed columns when the toggle is on, so that I map keys against the
   columns the join will actually see.
7. As an analyst, I want a transformed output that a join consumes to be a
   traceable result with its own id, so that I can tell which produced output fed
   my report.
8. As a maintainer, I want `function step`s and `built-in step`s to execute through
   one uniform `StepExecutor` contract resolved from a registry, so that new step
   behavior is added in one place instead of threaded through separate branches.
9. As a maintainer, I want a `function set` flattened by an adapter into uniform
   member executions, so that a set behaves exactly like the same steps placed
   individually on the source.
10. As a maintainer, I want "where a step reads its input from" decided in one
    `resolve_frame` seam, so that raw-vs-transformed lives in a single place every
    step type can reuse.
11. As a maintainer, I want the execution model to accept heterogeneous set members
    now, so that adding built-in members later is additive and does not reopen the
    runner.
12. As a maintainer, I want the runner-dependent workflows migrated onto the model
    and only the directly-superseded code removed (each removal behavior-preserved
    by a test), so that the cleanup does not change behavior or balloon into an
    open-ended refactor.

## Implementation Decisions

- **Uniform step execution.** Replace the inline type branching in `run_pipeline`
  with a `StepExecutor` contract dispatched from a step-type registry;
  `function step` and `built-in step` executors are the first two registrants.
  This makes step type a resolved concept rather than a branch.
- **Class-based step + context.** `Step` and `StepContext` are typed classes
  carrying the properties currently passed as step-dict keys (`step_type`,
  `source_function_map_id`, `set_name`, `builtin_type`, `builtin_config`,
  `position`, member functions, etc.). `StepContext` is built via factory
  classmethods from the existing map tables — `from_builtin()` over
  `source_builtin_map`, `from_function()` / `from_set()` over
  `source_function_map` — the "instantiate from different sources" pattern.
- **Function-set adapter.** A `function set` is flattened into a stream of uniform
  member executions so the runner processes each member as an individual
  single-step run (generalizing today's "a set is a transparent container"). The
  adapter and contract accept heterogeneous members (function or built-in) so the
  built-in-member path is additive later; this feature builds context only from
  the existing tables.
- **Input-source resolution.** A runner-owned `resolve_frame(source, raw |
  transformed)` returns `(frame, ref)`. **Raw** resolves to the source's instance
  table. **Transformed** resolves to the source's latest transformed output (its
  most recent staging table); if none exists, the source's pipeline is run once to
  materialize it (materialize-if-absent). Snapshot semantics: no automatic
  staleness refresh. The materialize path is cycle-guarded — a transformed-join
  cycle errors the run with a message naming the sources.
- **Transformed-output identity.** `ref` carries a derived `UUID5` `result_id`,
  computed with the same identity helper as `RunResult` (over source + mode +
  staging timestamp), and tied into the `RunResult` scheme so a consumed
  transformed output is a first-class, traceable result like any run.
- **Join honors the toggle.** `_execute_join` / `_validate_join_config` read the
  right-hand source through `resolve_frame` per `use_transformed` instead of
  hardcoding the raw instance table. The right-column-fetch endpoint that the
  join modal already calls with the transformed flag returns the transformed
  column set.
- **Migration + bounded removal.** Migrate the workflows that route through
  `run_pipeline` (run / validation / results endpoints and staging) onto the
  model. Remove only execution code this refactor directly supersedes, each
  removal guarded by a behavior-preserving test. No blanket cleanup.
- **Affected areas.** The runner (`run_pipeline`, staging helpers, the new
  executor/registry and `resolve_frame`), the built-in join executor and its
  validator, the run/validation/results API routes, the join modal's
  column-fetch, and the results layer that surfaces transformed output.
- **Dependency / base.** Requires built-in steps to execute in `run_pipeline`
  (the `#7` fix), which is currently only on `release/pipeline-bugfixes`; this
  feature is based there and must be rebased onto `main` once that merges.

## Testing Decisions

Good tests assert **observable state** — per-table row counts, resolved frames,
API response shapes, DB snapshot equality — never internal call sequences. They
run against a real ephemeral DuckDB sandbox per §13 (fresh DB per test; no
test-level transaction wrapper, since the runner owns transactions). Because this
is largely a refactor, the dominant pattern is **behavior preservation**: migrated
paths and any removed code must yield results identical to today, proven by tests,
alongside the net-new guarantees. All data-shaped guarantees are exercised with
messy/null real-world data, not only clean fixtures.

- **Runner / executor seam — `test_run_workflow.py` (integration, highest seam).**
  The core guarantees: `function step` and `built-in step` dispatched through the
  `StepExecutor` registry produce the same results as before; `StepContext`
  factory constructors build correctly from the existing map tables; the
  function-set adapter yields results identical to the same functions placed
  individually; `resolve_frame` returns the raw instance table for raw and the
  transformed output for transformed; materialize-if-absent runs a never-run
  source on demand; a transformed-join cycle errors with the sources named; the
  transformed-output `result_id` is deterministic and surfaced through `RunResult`.
- **Built-in executor seam — `test_builtins.py` (integration).** `_execute_join` /
  `_validate_join_config` honor `use_transformed`: raw selects the instance table,
  transformed selects the resolved frame; verified with null-containing,
  type-messy join data.
- **API seam — `test_api_pipelines_run.py`, `test_api_staging.py`,
  `test_api_validations.py`, `test_api_run_set.py`, `test_api_run_all.py`
  (FastAPI TestClient, integration).** Migrated run / validation / results /
  staging endpoints preserve their response shapes and behavior; the
  right-column-fetch endpoint returns the transformed column set when the
  transformed flag is set.
- **Frontend seam — `screen-builder.test.jsx` (vitest + jsdom).** Regression only:
  `JoinModal` still sends `use_transformed` and requests the transformed column
  set; this feature introduces no new frontend behavior.

Each test is named for the guarantee it guards and references the clause it
protects, so guarantees can be audited against tests (rule 9 / 10).

## Out of Scope

- Built-in steps nestable **inside** a `function set` as stored membership — the
  `function_set_map` schema, persistence, and UI for it (tracked: `#275`,
  execution-model convergence). This feature only makes the execution model ready
  for heterogeneous members.
- Anything the source → builder → runner → results migration does not reach
  (`#275`).
- The `rename` built-in (`#274`) — it sits on top of working transformed joins.
- Joins-at-the-end pipeline ordering / per-source compose (`#276`).
- Automatic staleness detection or refresh of a source's transformed output.
  Snapshot semantics are intentional; the refresh is the user re-running the
  source.

## Further Notes

- **Assumptions carried from discovery.** Today `_execute_join` always joins the
  raw right-source instance table and ignores `use_transformed` (dead toggle:
  the front end sends it, the back end drops it). Transformed output of a source
  is its latest staging table (`staging_{source_id8}_{timestamp}`, dropped before
  each run, latest kept); that is what "transformed" resolves to.
- **Provenance of the gap.** The toggle was specified in the
  `bug-fix-wave-ui-and-builtins` PRD; only the front end shipped (PR `#155`), and
  that PRD never went through slicing/ticketing — which is why the backend half
  was absent.
- **Convergence alignment is the point.** The contract and `resolve_frame` seam
  are deliberately the shapes the execution-model convergence feature (`#275`)
  will reuse, so that feature's scope is additive rather than a re-plumb.
