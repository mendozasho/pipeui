# PipeUI

Domain glossary for PipeUI — a browser-based pipeline builder over DuckDB where a
user registers data sources, attaches steps to a source's pipeline, and runs it.
These terms are canonical for every ez-skills pipeline phase.

## Pipeline steps

**Built-in step**:
A pipeline step backed by app-provided SQL logic (join, pivot, filter) rather than a
user-uploaded function. Persisted in `source_builtin_map`, identified by `step_id`,
configured by a `builtin_config` blob. Today only `join` is wired end-to-end.
_Avoid_: built-in function, builtin

**Function step**:
A pipeline step backed by a function set (one or more user-uploaded functions).
Persisted in `source_function_map`, identified by `source_function_map_id`, and carries
a nested `functions[]` payload (params, bindings, scalar values).
_Avoid_: set step, function-set step

**Placed step**:
A built-in or function step already attached to a source's pipeline (a row exists in its
map table), which therefore renders as a card on the Builder canvas — as opposed to a
palette card, which is only a draggable template.
_Avoid_: attached step (when the distinction from a palette card matters)

**Unified pipeline**:
The position-ordered merge of a source's function steps and built-in steps into one list,
each tagged with `step_type`.
_Avoid_: combined pipeline, full pipeline

**step_type**:
The discriminator field (`"function"` | `"builtin"`) on a pipeline step that tells the
canvas which card variant to render and which remove/edit endpoints to call.
_Avoid_: kind, bare "type"

## Runner execution

**scalar run** _(synonym: scalar loop)_:
The runner executing a scalar-shaped function — one whose bound parameter takes a single
value per call (`int`, `float`, or a `str` that is not `column_backed`), or whose return is
a single value — **once per record** of the column under it (R = row count), collecting the
per-row outputs into one normalized vector. Normalizing a scalar function to a vector is the
point: it lets the Results layer and any downstream step consume every function's output the
same way regardless of the function's declared shape. A scalar run is the loop over **rows**;
contrast `multi_select_eligible`, which is the loop over **columns**. The two are independent
and can both apply to one execution (a scalar function bound to N columns does N scalar runs).
_Avoid_: broadcast (that is the inverse — copying one scalar across rows), apply, vectorize.

**multi_select_eligible**:
A label on a parameter (and, derived, on its function) marking that it may bind **more than
one column** and must therefore be executed as a series of `argument bundle`s rather than a
single call. Eligibility is a property of the parameter's granularity being above `scalar`
(`column_backed`, `pd.Series`) — it is a statement of **intent the runner reads**, not a claim
that the columns are currently present in `alias_map`. A column may be unmapped yet still
*should* be bound; the label still applies, so the runner knows to expand the parameter once
the mapping exists. (Whether `pd.DataFrame` is eligible is unresolved — design.md includes it,
the implementation reference excludes it.)
_Avoid_: multi-column, multi-bind.

**argument bundle**:
One **positionally-paired** group of column arguments across the `multi_select_eligible`
parameters for a single run. A **varying param** (bound to more than one column) contributes
its `i`-th column to bundle `i`, in the user-placed column order; a `static param` (bound to a
single column) broadcasts that one column into every bundle. All varying params must bind the
same column count **N** (enforced at attach) — N is the number of bundles. Validity is
**all-or-nothing per bundle** — if any member column is invalid (missing, type-mismatched, or
not yet mapped), the entire bundle is skipped and never partially executed, because the
arguments only make sense together as the user grouped them. The runner builds N bundles in
order and runs the function once per bundle, storing N results.
_Avoid_: set (collides with `function_set`), tuple, group, row (collides with `scalar run`).

**static param**:
A `multi_select_eligible` parameter bound to exactly one column, whose single argument does not
change across a multi-select run — it is **broadcast** into every `argument bundle`. This lets a
user pair a constant column (e.g. `country → USA`) with a set of varying columns. Distinct from
a **scalar param**, which carries a single non-column value (its Python default or a per-run
override), not a column.
_Avoid_: constant param, fixed param.

## Results

**RunResult**:
The backend object holding the outcome of **one** normalized run — one `scalar run` vector for one
`argument bundle`: its status, pass/fail counts, the normalized result vector, and identifying
metadata (function, argument bundle, source) under a shortened `UUID5(function, argument bundle,
source)` identity. It is the **single backend source of truth for result data** — anything backend
that deals with results uses `RunResult` rather than ad-hoc dicts — and is kept deliberately
focused as a result-holder, not a catch-all. It **may be specialized per run kind** (e.g. a
validation `RunResult` carrying pass/fail counts) while sharing the base identity/metadata contract.
_Avoid_: Result, Outcome, CheckResult, result dict.

**results report**:
The exportable, **transposed** summary of a **validation** run: **one row per `RunResult`**, keyed by
its approved label (varying-column name; `UUID5` identity underneath), with columns for pass count,
fail count, and any future metadata — *not* result vectors appended as columns. Includes runs that
passed. Built from (validation-specialized) `RunResult`s. Two entry points feed it: a validation run
from the Functions page (each attached source ran) or validations tied to a source (each validation
function ran). Labels are **normalized** for clean file output (no `__` or odd tokens).
_Avoid_: result export, summary sheet, validation vector dump.

**transformed report**:
A source's materialized transformed data table — the working/staging output after every transform
assigned in the Builder completed its cycle, exportable. Each transform step writes per its
`output_mode`: **append** adds a new column (a cleaned auto-label, or a user-provided name); **replace**
overwrites an explicit, user-selected **ordered target column** per `argument bundle` (bundle `i`
replaces target `i`; target count must equal the bundle count). Distinct from the `results report`,
which is the transposed validation summary.
_Avoid_: staging dump, output table.

## Runner resolution model

**Input-source resolution** _(function: `resolve_frame`)_:
The runner seam that turns a `(source, raw | transformed)` reference into a DataFrame plus a
provenance `ref`. The single place where "where does this step's input come from" is decided, so
no executor hardcodes a table.
_Avoid_: source loading, table lookup.

**Raw source** / **Transformed source**:
The two input modes a step (today a `Join`) can read another source in. **Raw** = that source's
instance table (original ingested data); **transformed** = that source's transformed output (its
latest staging table, or one materialized on demand).
_Avoid_: original/clean (for raw), output/result (for transformed).

**Materialize-if-absent**:
The rule for a `transformed` reference — use the source's existing transformed output if present,
else run its pipeline once to produce it. Snapshot semantics: no automatic refresh (re-running the
source is the only refresh), guarded against cycles.
_Avoid_: lazy load, auto-run, refresh.

**StepExecutor**:
The uniform per-step-type execution contract the runner dispatches through a step-type registry —
`function step`s and `built-in step`s are resolved and run the same way, replacing inline type
branching.
_Avoid_: handler, dispatcher, bare "runner".

**StepContext**:
The typed object carrying one step's execution properties (the keys the step dict held),
constructed via factory classmethods from the map tables — `from_builtin()` (`source_builtin_map`),
`from_function()` / `from_set()` (`source_function_map`).
_Avoid_: step dict, context dict, run context.

**Function-set adapter**:
The component that flattens a set into a stream of uniform member executions, so the runner
processes each member as if it were an individual single-function step (a multi-function run).
Shaped to accept heterogeneous members (function or built-in).
_Avoid_: set expander, unpacker.

**Transformed-output result_id**:
The derived `UUID5` identity for a consumed transformed snapshot (over source + mode + staging
timestamp), tied into the `RunResult` scheme so the transformed output a step joins against is a
first-class, traceable result like any run.
_Avoid_: staging id, snapshot key.

## Module responsibilities (SRP)

The backend is divided so each module has **one reason to change**. Dependencies flow strictly
**down** the layers (`ARCHITECTURE.md §1–2`): `frontend → middleware → backend`, and inside the
backend `domain → data`; no module imports one above it, and there are **no in-function imports to
dodge a cycle** (an in-function import means a responsibility is in the wrong layer — move it down
or inject it). This table is the canonical "where does new code go" map.

> **Layer map (`ARCHITECTURE.md §4`, epic #55 — complete).** The tree below is the on-disk
> shape: `middleware/` (the API seam) + `backend/{data,domain}/` + `app/` (composition root).

| Module | Single responsibility (its one reason to change) | New code goes here when… |
| --- | --- | --- |
| **`backend/data/base/`** — shared foundation, pulled by every feature | | |
| `ids.py` | **Id derivation** — random surrogate `new_id` + by-content `content_hash_id` (UUID5 over a per-table namespace). Zero `pipeui` imports. | a new id scheme or table namespace. |
| `db.py` | **Connection + schema lifecycle** — open a DuckDB connection, create/migrate the schema, resolve the db path. | a connection or DDL-bootstrap concern. ⚠ *currently also holds CSV/xlsx type-inference and the FastAPI `get_conn` provider — an SRP overlap slated to split out (#49).* |
| `schema/` | **DDL + type maps + seeds** — `constants.py` (`IngestionMethod`, DuckDB↔Python type maps), `queries.py` (table DDL, builtin seeds). | a schema table, a type-map entry, or a seed. |
| `tables.py` | **Instance-table DDL builder** — pure `instance_table_name` + `build_create_table_sql`; no DB, no registry. | how a per-source instance table is named or created. |
| `results.py` *(L0)* | **Result identity/data** — `RunResult`/`ValidationRunResult` + the `StepResultEntry` variant carriers; the single source of truth for result data. | a new result field/shape or result-kind variant (never an ad-hoc dict). |
| `settings.py` | **App-settings shape** — `AppSettings` (the `pipeui.config.json` schema) + `DEFAULTS`. | a new app/config setting. |
| `fails.py` | **Failure carriers** — `FailedRegistryEntry`/`FailedFunctionEntry` accumulators, shared across features. | a new rejection/failure shape. |
| **`backend/data/sources/`** — source feature data | | |
| `registry.py` | **Source registry write-contract** — `SourceRegistry{Entry,Update}`: validates a `source_registry` row and recomputes `content_hash_id`; holds no DB handle, reads no other rows. | a `source_registry` field or write-rule. |
| `columns.py` | **Column registry write-contract** — `ColumnRegistry{Entry,Update}`. | a `column_registry` field or write-rule. |
| **`backend/data/functions/`** — function feature data | | |
| `sets.py` | **Function-set registry write-contract** — `FunctionSet{Entry,Update}`. | a `function_set` field or write-rule. |
| **`backend/data/runner/`** — runner feature data | | |
| `steps.py` *(L0)* | **Step description** — typed, logic-free `StepContext` + variants + `from_*` factories. No DB, no dispatch, no execution. | a step gains a field, or a new way to build a step from a map row (a new `from_*`). |
| `bundles.py` | **Argument-bundle pairing** — pure positional pairing of multi-select columns (`pair_bundles`, `ArgumentBundle`). | a multi-select bundle-pairing rule. |
| `staging.py` *(L1)* | **Staging store** — write/read/drop a source's transformed-output staging tables. | anything about how transformed output is stored. |
| `step_loader.py` *(L1)* | **Step loading** — read the map tables into a source's ordered step list (`fetch_steps`, `get_builtin_steps`). Pure read. | a new step source or ordering rule. |
| **`backend/domain/sources/`** — source lifecycle (orchestration; owns transactions) | | |
| `create.py` | **Source creation** — the create-flow cache (`_stage_create_flow`), type/PK confirmation, and the atomic `source_registry` + `column_registry` + `source_column_map` write. | a source-creation step or create-flow staging concern. |
| `ingestion.py` | **Source ingestion** — load a file → `TRY_CAST` type-validate → duplicate-handle → write clean rows to the instance table. *(Also currently holds the source read/preview helpers `get_source_rows`/`get_source_detail` — a read-path concern the audit flags for extraction.)* | an ingest-phase concern. |
| `migration.py` | **Column-type migration** — recreate-and-copy + `TRY_CAST` pre-check + atomic swap when a column's type changes, via `ColumnRegistryUpdate`. | a column-type-migration rule. |
| **`backend/domain/functions/`** — function registration + pipeline wiring | | |
| `registration.py` | **Function registration** — discover (`.py`/`.sql`) + classify (`function_class`/`type`/`return`) + register (`function_registry` + `parameter` rows). | a discovery/classification/registration concern. *(SRP split slated — #47.)* |
| `sets.py` | **Function-set CRUD** — create / update / list function sets (`FunctionSet*` carriers at the write boundary). | a function-set lifecycle op. |
| `attach.py` | **Pipeline wiring** — attach/detach a function to a source's pipeline, suggest bindings, edit a placed step (`alias_map` writes); read the pipeline. | a pipeline attach/edit/suggest concern. *(SRP split slated — #46.)* |
| `builtins.py` *(L2)* | **Built-in steps** — definition (config + validation) + execution of join/pivot/filter. Lives here because a built-in is a *complex function* (a step backing, peer to a function set). | a new built-in type (e.g. rename) — its validator + `_execute_*`. ⚠ *contract-mediated `functions⇄runner` coupling: imports `runner.resolve` (`resolve_frame`); `runner.executors` imports it (`execute_builtin_step`). Resolution = the execution-model convergence (#41).* |
| **`backend/domain/runner/`** — run orchestration + execution | | |
| `resolve.py` *(L2)* | **Input resolution** — where a step reads its input: raw instance table vs transformed output, materialize-if-absent, cycle guard (`resolve_frame`, `FrameRef`). Runner **injected** (no orchestrator import). | a new input mode, materialize/cycle rule, or provenance field. |
| `executors.py` *(L3)* | **Step execution** — the `StepExecutor` registry + per-type executors (function, set-adapter, built-in) and the mechanics of running a step's functions into results. | a new **step type** (a new `StepExecutor` in `STEP_EXECUTORS`), or new per-function execution mechanics. *(SRP split slated — #45.)* |
| `worker.py` | **Process-isolated execution** — run a user function in a subprocess (`setrlimit`, Arrow IPC), strict data-in/data-out (never receives the connection). | the worker/sandbox mechanics or its IPC contract. |
| `run.py` *(L4)* | **Run orchestration** — drive a source's whole run end-to-end (load → resolve → execute via the registry → stage → collect); cross-source runners. Injects `run_pipeline` into `resolve`. | a new run phase, run-type, or cross-source entry point. |
| `export.py` | **Run export** — produce the exportable `results report` / `transformed report` from a run's output. | a new export format or report shape. |
| **`middleware/`** — the API seam (HTTP routes; calls `backend` only) | | |
| `sources` `functions` `function_sets` `pipelines` `validations` `builtins` `settings` `.py` | **API routes** — one router module per resource: validate/parse the request, delegate to a `backend` function, shape the JSON response. No business logic of its own. | a new endpoint or request/response shape for that resource. *(Some handlers — `sources`/`pipelines` — still hold raw SQL/existence checks the audit flags for push-down into `backend` — #48.)* |
| **`app/`** — composition root (wires the layers; owned by no feature) | | |
| `main.py` | **App wiring** — build the FastAPI `app`, mount the routers + the `frontend/` static dir. | a new router include or app-level mount/middleware. |
| `config.py` | **Startup config** — the process-frozen `DB_PATH` (read once at import). | an app-level startup constant. |
| `helpers.py` | **App utilities** — settings-file I/O (`load_settings`/`save_settings`) + `infer_pattern`. *(Junk-drawer; split slated — #49.)* | (prefer a feature module; this is being dissolved.) |
| `cli.py` | **CLI entry** — `pipeui <init\|start>` (scaffold config/db; launch uvicorn). | a new CLI subcommand. |

Dependency direction: `base/*` (ids, schema, tables, settings, fails, results) underlies everything;
within the runner, `steps` → `staging`/`step_loader`/`bundles` → `resolve` → `executors` → `run`,
with `functions.builtins` consumed by `executors` (and itself consuming `resolve` — the #41 coupling).
The `registry`/`columns`/`sets` write-contracts feed the source/function **domain** modules (create,
attach, …), not the runner chain.

### Module contracts (carriers)

Data crosses a module boundary **only** through a carrier: a **frozen, behavior-free dataclass**
that is the sole legal shape for that boundary. Modules talk through carriers, never by reaching
into each other's internals. To change what crosses a boundary, change its carrier (a deliberate,
tested contract edit) — never an incidental dict key.

| Carrier (defined in) | Contract | Boundary: producer → consumer | Enforcement |
| --- | --- | --- | --- |
| `StepContext` + `FunctionStepContext` / `BuiltinStepContext` (`backend/data/runner/steps.py`) | typed, logic-free description of one step | `step_loader` → `steps.py` factories → `run` (dispatch) + `executors` | `frozen=True`; typed fields (no `data` dict / `get`); built only via `from_*` factories; the variant returned **is** the contract |
| `FunctionSpec` (`backend/data/runner/steps.py`) | one member of a function step | `step_loader` → `executors` (set adapter) | `frozen=True`; `params`/`builtin_config` are typed `Mapping` (the declared depth boundary) |
| `StepRunEnv` (`backend/domain/runner/executors.py`) | the run-scoped inputs an executor needs | `run` → `executors` | `frozen=True`; fully populated by the orchestrator; carries the injected `run_transforms` runner |
| `StepExecResult` (`backend/domain/runner/executors.py`) | the complete outcome of running one step | `executors` → `run` | `frozen=True`; `entries` are `StepResultEntry` variant carriers (never ad-hoc dicts) |
| `RunResult` / `ValidationRunResult` (`backend/data/base/results.py`) | canonical identity + data of one result | `executors` (mechanics) → `run` + Results/export | `frozen=True`; deterministic `result_id` |
| `StepResultEntry` + `Validation`/`Transform`/`BuiltinResultEntry` variants (`backend/data/base/results.py`) | one step's result = a `RunResult` plus its provenance, as a per-kind variant | `executors` → `run` (serialized to the wire dict at the published seam) | `frozen=True`; variant hierarchy ("the variant IS the contract"), each renders its own `to_dict()` — no kind-switch in the consumer |
| `FrameRef` (`backend/domain/runner/resolve.py`) | provenance of a resolved input frame | `resolve` → `functions.builtins` (`_execute_join`) + `middleware/sources`, then into `RunResult.consumed_result_id` | `frozen=True`; invariant `result_id is None ⟺ mode == RAW` (in `__post_init__`); returned as `(frame, FrameRef)` |
| `run_transforms` runner *(behavioral port, not data)* (`backend/domain/runner/resolve.py` declares the type) | "produce a source's transformed output by running its transforms" | `run` → `resolve` (DIP) | `resolve` declares the callable signature; orchestrator supplies it; **zero `run` import in `resolve`** |

Enforcement is real, not aspirational: frozen dataclasses block mutation; `__post_init__` invariants
make illegal carriers unconstructable; one contract test per carrier asserts the producer fills the
full shape and the consumer reads only declared fields; the hostile-auditor's in-function-import and
"results use `RunResult`, not dicts" checks guard it at review.

`StepContext` is variant-typed (not a `data` dict): a base `StepContext(step_type, position)` with
`FunctionStepContext` (`source_function_map_id`, `set_id`, `set_name`, `functions: tuple[FunctionSpec, …]`,
`output_mode`, `append_name`, `output_targets`) and `BuiltinStepContext` (`step_id`, `builtin_type`,
`builtin_config`). `from_function`/`from_set` build the function variant; `from_builtin` builds the
built-in variant; each executor consumes its matching variant.
