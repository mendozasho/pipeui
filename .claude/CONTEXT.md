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

## Runner module responsibilities (SRP)

The runner is divided so each module has **one reason to change**. Dependencies flow strictly
**down** the layers; no module imports one above it, and there are **no in-function imports to dodge
a cycle** (an in-function import means a responsibility is in the wrong layer — move it down or
inject it). This table is the canonical "where does new code go" map.

| Module (`pipeui/workflow/`) | Single responsibility (its one reason to change) | New code goes here when… |
| --- | --- | --- |
| `step.py` *(L0)* | **Step description** — the typed, logic-free description of one step (`StepContext` + variants + `from_*` factories). No DB, no dispatch, no execution. | a step gains a field, or there's a new way to construct a step from a map row (a new `from_*`). |
| `step_loader.py` *(L1)* | **Step loading** — read the map tables into a source's ordered step list (`_fetch_steps`, `get_builtin_steps`). Pure read. | a new step source or ordering rule. |
| `staging.py` *(L1)* | **Staging store** — write/read/drop a source's staging tables (its transformed-output store). | anything about how transformed output is stored. |
| `resolve.py` *(L2)* | **Input resolution** — where a step reads its input: raw instance table vs transformed output, materialize-if-absent, cycle guard (`resolve_frame`, `FrameRef`). Runner **injected** (no orchestrator import). | a new input mode, materialize/cycle rule, or provenance field. |
| `builtins.py` *(L2)* | **Built-in steps** — definition (config + validation) + execution of join/pivot/filter. | a new built-in type (e.g. rename) — its validator + `_execute_*`. |
| `executors.py` *(L3)* | **Step execution** — the `StepExecutor` registry + per-type executors (function, set-adapter, built-in) and the mechanics of running a step's functions into `RunResult`s. | a new **step type** (a new `StepExecutor` registered in `STEP_EXECUTORS`), or new per-function execution mechanics. |
| `run.py` *(L4)* | **Run orchestration** — drive a source's whole run end-to-end (load → resolve → execute each step via the registry → stage → collect); cross-source runners. Injects `run_pipeline` into `resolve`. | a new run phase, run-type, or cross-source entry point. |
| `results.py` *(L0, pkg root)* | **Result identity/data** — `RunResult`/`ValidationRunResult`, the single source of truth for result data. | a new result field/shape (never an ad-hoc dict). |

Dependency direction: `step` → `staging`/`step_loader` → `resolve`/`builtins` → `executors` → `run`.

### Module contracts (carriers)

Data crosses a module boundary **only** through a carrier: a **frozen, behavior-free dataclass**
that is the sole legal shape for that boundary. Modules talk through carriers, never by reaching
into each other's internals. To change what crosses a boundary, change its carrier (a deliberate,
tested contract edit) — never an incidental dict key.

| Carrier (defined in) | Contract | Boundary: producer → consumer | Enforcement |
| --- | --- | --- | --- |
| `StepContext` + `FunctionStepContext` / `BuiltinStepContext` (`step.py`) | typed, logic-free description of one step | `step_loader` → `step.py` factories → `run` (dispatch) + `executors` | `frozen=True`; typed fields (no `data` dict / `get`); built only via `from_*` factories; the variant returned **is** the contract |
| `FunctionSpec` (`step.py`) | one member of a function step | `step_loader` → `executors` (set adapter) | `frozen=True`; `params`/`builtin_config` are typed `Mapping` (the declared depth boundary) |
| `StepRunEnv` (`executors.py`) | the run-scoped inputs an executor needs | `run` → `executors` | `frozen=True`; fully populated by the orchestrator; carries the injected `run_transforms` runner |
| `StepExecResult` (`executors.py`) | the complete outcome of running one step | `executors` → `run` | `frozen=True`; `entries` are `RunResult`-derived, never ad-hoc dicts |
| `RunResult` / `ValidationRunResult` (`results.py`) | canonical identity + data of one result | `executors` (mechanics) → `run` + Results/export | `frozen=True`; deterministic `result_id` |
| `FrameRef` (`resolve.py`) | provenance of a resolved input frame | `resolve` → `builtins` (`_execute_join`) + `api/sources`, then into `RunResult.consumed_result_id` | `frozen=True`; invariant `result_id is None ⟺ mode == RAW` (in `__post_init__`); returned as `(frame, FrameRef)` |
| `run_transforms` runner *(behavioral port, not data)* (`resolve.py` declares the type) | "produce a source's transformed output by running its transforms" | `run` → `resolve` (DIP) | `resolve` declares the callable signature; orchestrator supplies it; **zero `pipeui.workflow.run` import in `resolve`** |

Enforcement is real, not aspirational: frozen dataclasses block mutation; `__post_init__` invariants
make illegal carriers unconstructable; one contract test per carrier asserts the producer fills the
full shape and the consumer reads only declared fields; the hostile-auditor's in-function-import and
"results use `RunResult`, not dicts" checks guard it at review.

`StepContext` is variant-typed (not a `data` dict): a base `StepContext(step_type, position)` with
`FunctionStepContext` (`source_function_map_id`, `set_id`, `set_name`, `functions: tuple[FunctionSpec, …]`,
`output_mode`, `append_name`, `output_targets`) and `BuiltinStepContext` (`step_id`, `builtin_type`,
`builtin_config`). `from_function`/`from_set` build the function variant; `from_builtin` builds the
built-in variant; each executor consumes its matching variant.
