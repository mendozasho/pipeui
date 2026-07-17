# PipeUI

Domain glossary for PipeUI — a browser-based pipeline builder over DuckDB where a
user registers data sources, attaches steps to a source's pipeline, and runs it.
These terms are canonical for every ez-skills pipeline phase.

## Pipeline steps

**Built-in step**:
A pipeline step backed by app-provided logic (join, pivot, filter, rename, date_range)
rather than a user-uploaded function. Persisted in `source_builtin_map`, identified by
`step_id`, configured by a `builtin_config` blob. All five types are wired end-to-end and
registered in `BUILTIN_EXECUTORS` (`backend/domain/functions/builtins.py`); execution is
lowered onto `FunctionContract`s (`builtin_lowering.py`); `rename` is singleton
(one per source) and pinned last in the pipeline.
_Avoid_: built-in function, builtin

**date_range step**:
A singleton built-in step (`builtin_type: "date_range"`) holding the source's date filter:
one or more `filter group`s of `range condition`s over registered date-typed columns.
Runs in the `pinned tail` (before rename), so the transformed output contains only
matching rows.
_Avoid_: date filter step, calendar filter

**range condition**:
One `{column, start, end}` predicate inside a `filter group`: the date-typed column's value
falls inclusively between the bounds. One-sided (start-only = on-or-after, end-only =
on-or-before) is valid; both-empty is not. TIMESTAMP/TIMESTAMPTZ columns compare at DATE
granularity (cast before comparison); a NULL date fails the condition.
_Avoid_: date rule, bound pair, between clause

**filter group**:
An AND-ed set of `range condition`s inside a `date_range step`. Groups combine with OR
(one-level disjunctive normal form) — mixed AND/OR expressions are expressed as
OR-of-AND-groups, never as chained combinators or nested groups.
_Avoid_: condition set, branch, clause

**pinned tail**:
The ordered suffix of the `unified pipeline` that runs last regardless of stored position:
`date_range` then `rename`. Generalizes rename's pinned-last rule; the ordering exists
because range conditions reference registered column names and rename relabels the output.
_Avoid_: pinned last (as a category — fine as a per-step adjective), end steps

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
each tagged with `step_type`. One exception to pure position order: a `rename` built-in is
**pinned last** regardless of its stored position (it operates on the final output). This
pinned-last sort is duplicated across three sites — `get_unified_pipeline`, `run.py`, and
`pipeline_read.py` (the magic string `"rename"`, tracked as cleanup in #83).
_Avoid_: combined pipeline, full pipeline

**step_type**:
The discriminator field (`"function"` | `"builtin"`) on a pipeline step that tells the
canvas which card variant to render and which remove/edit endpoints to call.
_Avoid_: kind, bare "type"

## Runner execution

**scalar run** _(synonym: scalar loop)_:
The runner executing a scalar-shaped function — one whose bound parameter takes a single
value per call (`int`, `float`, `bool`, or `str` — any `scalar`-class param, see
`binding_kind`), or whose return is
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

**binding_kind**:
The single derived answer to "how may this parameter receive its argument", read off the
parameter's existing `function_class`: **value_or_column** (a `scalar`-class param —
`int`/`float`/`bool`/`str` — that may take a literal value **or** bind a column);
**column_only** (a `pd.Series`-class param, always one-or-more bound columns, never a literal);
**table** (a `pd.DataFrame`, always the full table, never bound). Derived in exactly one place
(`classification.py`) and read by `suggest`, `attach`, and the Builder UI, so binding
eligibility is never re-enumerated as parallel type lists. Generalizes the older two-way
`param_kind` (`column`/`scalar`) split.
_Avoid_: binding_eligible, togglable.

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
| `db.py` | **Connection + schema lifecycle** — open a DuckDB connection (`get_connection`), create/migrate the registry schema (`create_schema`/`_run_migrations`), resolve the db path (`get_db_path`). Imports neither `app` nor FastAPI; emits no stdout. | a connection or DDL-bootstrap concern. |
| `schema/` | **DDL + type maps + seeds** — `constants.py` (`IngestionMethod`, DuckDB↔Python type maps), `queries.py` (table DDL, builtin seeds). | a schema table, a type-map entry, or a seed. |
| `tables.py` | **Instance-table DDL builder** — pure `instance_table_name` + `build_create_table_sql`; no DB, no registry. | how a per-source instance table is named or created. |
| `results.py` *(L0)* | **Result identity/data** — `RunResult`/`ValidationRunResult` + the `StepResultEntry` variant carriers; the single source of truth for result data. | a new result field/shape or result-kind variant (never an ad-hoc dict). |
| `settings.py` | **App-settings shape** — `AppSettings` (the `pipeui.config.json` schema) + `DEFAULTS`. | a new app/config setting. |
| `fails.py` | **Failure carriers** — `FailedRegistryEntry`/`FailedFunctionEntry` accumulators, shared across features. | a new rejection/failure shape. |
| **`backend/data/sources/`** — source feature data | | |
| `registry.py` | **Source registry write-contract** — `SourceRegistry{Entry,Update}`: validates a `source_registry` row and recomputes `content_hash_id`; holds no DB handle, reads no other rows. | a `source_registry` field or write-rule. |
| `columns.py` | **Column registry write-contract** — `ColumnRegistry{Entry,Update}`. | a `column_registry` field or write-rule. |
| `inference.py` | **CSV/xlsx column type-inference** — `infer_column_types` (DuckDB DESCRIBE-sniff a file into normalized `(name, type)` pairs, `VARCHAR` fallback) + `map_pandas_dtype` (the xlsx pandas-fallback). No DB writes, no stdout; consumed by `domain/sources/create.py`. | a file type-inference or sniff-normalization rule. |
| **`backend/data/functions/`** — function feature data | | |
| `classification.py` *(DB-free leaf)* | **Function classification** — pure derivation of `function_class`/`function_return_type`/`function_type`/`binding_kind`/`granularity` from the one `_TYPE_DESCRIPTORS` table (Principle 4, §11; OCP #51 — now incl. the sql-engine-only `date` and `source_ref` rows), plus annotation-string canonicalization (`annotation_to_str`, `is_known_*`). The leaf of the functions chain — `contract.py` derives from it. **Zero DB dependency.** | a classification/derivation rule, or a new param/return type (one `TypeDescriptor` row). |
| `contract.py` | **Function contract** — `FunctionContract`/`ParamContract`, the one interface every function-shaped thing (user `.py`, user `.sql`, lowered built-in) is described by: derived facts as properties over `classification` (`function_class`, `function_type`, `execution_mode()` — never stored), plus serialization (`to_dict`, `to_registry_dict`, `from_registry_rows`). DB-free leaf. | the contract's shape, a derived fact, or its (de)serialization. |
| `binding.py` | **Binding resolution** — `ParamBinding`/`StepBinding`/`BoundCall` + `bind_contract` (literals → shape homogeneity → `pair_bundles`), pure and frame-free; the canonical home of `RequiredParamError`/`MixedShapeError`/`coerce_scalar`/`composite_key`. | a binding-resolution or bound-args-semantics rule. |
| `sets.py` | **Function-set registry write-contract** — `FunctionSet{Entry,Update}`. | a `function_set` field or write-rule. |
| **`backend/data/runner/`** — runner feature data | | |
| `steps.py` *(L0)* | **Step description** — typed, logic-free `StepContext` + variants + `from_*` factories. No DB, no dispatch, no execution. | a step gains a field, or a new way to build a step from a map row (a new `from_*`). |
| `bundles.py` | **Argument-bundle pairing** — pure positional pairing of multi-select columns (`pair_bundles`, `ArgumentBundle`). | a multi-select bundle-pairing rule. |
| `staging.py` *(L1)* | **Staging store** — write/read/drop a source's transformed-output staging tables. | anything about how transformed output is stored. |
| `step_loader.py` *(L1)* | **Step loading** — read the map tables into a source's ordered step list (`fetch_steps`, `get_builtin_steps`), hydrating each `FunctionSpec` with its `contract` + `binding` (params ordered by `parameter.position` — signature order). Pure read. | a new step source, ordering rule, or hydration field. |
| **`backend/domain/sources/`** — source lifecycle (orchestration; owns transactions) | | |
| `create.py` | **Source creation** — the create-flow cache (`_stage_create_flow`), type/PK confirmation, and the atomic `source_registry` + `column_registry` + `source_column_map` write. Also owns the sources utils `infer_pattern` (filename → regex pattern) and `find_source_by_pattern`. | a source-creation step or create-flow staging concern. |
| `ingestion.py` | **Source ingestion (write-path)** — load a file → `TRY_CAST` type-validate → duplicate-handle → write clean rows to the instance table (`ingest_source` + the schema-diff helpers). | an ingest-phase / write concern. |
| `read.py` | **Source read-path** — pure registry reads + row preview, no transaction: `list_source_summaries` (all sources + columns + exact row_count in 2 base queries — no N+1), `get_source_summary` (one source, no row_count — the register/ingest-match echo), `get_source_columns` (join-modal picker), `source_exists` / `check_column_ownership` (existence/ownership guards returning a structured `"source_missing"\|"column_missing"\|"not_owned"\|"ok"` status the route maps to a 404), and `get_source_detail` / `get_source_rows` (per-source detail + row preview). | a source read / preview / existence-guard concern. |
| `migration.py` | **Column-type migration** — recreate-and-copy + `TRY_CAST` pre-check + atomic swap when a column's type changes, via `ColumnRegistryUpdate`. | a column-type-migration rule. |
| **`backend/domain/functions/`** — function registration + pipeline wiring | | |
| `guardrails.py` | **Upload screen** — `screen_module`: AST-only screen over an uploaded module's source, run BEFORE `discovery` exec's it; a `block` finding rejects the module (its top-level code never runs), `flag` findings surface in the scan log. An accident screen (Principle 5), not a sandbox. | a block/flag rule or screen severity. |
| `discovery.py` | **Function discovery/extraction** — `extract_contracts`: guardrail-screen a `.py` module, load + inspect it — or parse a `.sql` `-- param:` header — into one `FunctionContract` per eligible function (or a skip reason). Reads the filesystem; calls `classification`; no DB. | a file-discovery or signature/header-extraction concern. |
| `registration.py` | **Function registration (DB transaction owner)** — `register_function_entry` writes one `function_registry` row + its `parameter` rows in one transaction (§10), collapsing on `content_hash_id` (Principle 2); consumes `contract.to_registry_dict()`, persisting `parameter.position` + `function_registry.engine`/`function_body`; `scan_functions` scans dirs, registers, and deactivates vanished files. Holds the DuckDB connection; calls `discovery`. | a registration/transaction or scan-orchestration concern. |
| `function_read.py` | **Function read-API** — `get_function` (one function's registry fields + params + attached_sources) / `list_functions` (all, ordered). Pure read — no transaction, no discovery, no classification. | a function registry read/serialize concern. |
| `sets.py` | **Function-set CRUD** — create / update / list function sets (`FunctionSet*` carriers at the write boundary). | a function-set lifecycle op. |
| `attach.py` | **Pipeline-wiring writes** — `attach_function` (resolve → validate → per-table-write) + `detach_function` (`source_function_map` + `alias_map` writes, atomic). Owns `AttachBinding`, `_REQUIRES_BINDING`, and the **single-owner** auto-set rule `_is_auto_created_set` (reuse on attach via `_resolve_or_create_auto_set`; cleanup on detach). | an attach/detach write concern, or the auto-set rule. |
| `pipeline_read.py` | **Pipeline read/serialize** — `get_pipeline`: read committed pipeline state (columns + ordered function/built-in steps) into the API wire dict. Pure read, no transaction. | a pipeline read/serialize concern. |
| `suggest.py` | **Binding suggestion** — `suggest_bindings` (+ `_params_for_*`, `_SUGGEST_TYPES`, `_SCALAR_TYPES`): dry-run per-param column suggestions for the attach/edit modal, no writes. Returns `current_bindings` in saved `alias_map.position` order (Principle 7 / #260). | a binding-suggestion concern. |
| `step_edit.py` | **Placed-step edit** — `patch_pipeline_step` (+ `_VALID_OUTPUT_MODES`): edit a placed step's position / output_mode / bindings / scalars; transactional on the `alias_map` rewrite. | a placed-step edit concern. |
| `builtins.py` *(L2)* | **Built-in step definition** — config validation + attach/patch/detach writes for join/pivot/filter/rename/date_range, dispatched through the `BUILTIN_EXECUTORS: dict[str, BuiltinSpec]` registry (a frozen `BuiltinSpec(validate, execute, singleton)` carrier mirroring the runner's `STEP_EXECUTORS`; OCP — #50); execution **delegates to `builtin_lowering`**. Also `list_builtin_catalog` (the `builtin_registry` read + `config_schema` JSON parse). Lives here because a built-in is a *complex function* (a step backing, peer to a function set). | a new built-in type registers a `BuiltinSpec` (validator + lowering delegate, plus `singleton=True` for one-per-source like `rename`) in `BUILTIN_EXECUTORS` — no dispatch edit — or a built-in catalog read. |
| `builtin_lowering.py` | **Built-in lowering** — every built-in as `FunctionContract`s: `FILTER_CONTRACTS` (one SQL contract per operator family), the three `date_range` predicate contracts, `RENAME_COLUMN` (one in-process python contract — app-authored source, no worker; batch/swap semantics stay as orchestration), and the `join_contract`/`pivot_contract` factories — plus the shims lowering persisted `builtin_config` JSON into `StepBinding`s at run time. | how a built-in lowers onto the contract — a new operator/predicate contract or lowering shim. ⚠ *the contract-mediated `functions⇄runner` coupling lives here: it imports `runner.{sql_engine,dnf,resolve}` while `runner.executors` imports `builtins.execute_builtin_step` (#41).* |
| **`backend/domain/runner/`** — run orchestration + execution | | |
| `resolve.py` *(L2)* | **Input resolution** — where a step reads its input: raw instance table vs transformed output, materialize-if-absent, cycle guard (`resolve_frame`, `FrameRef`). Runner **injected** (no orchestrator import). | a new input mode, materialize/cycle rule, or provenance field. |
| `executors.py` *(L3)* | **Step execution** — the `StepExecutor` registry + per-type executors (function, set-adapter, built-in) and the mechanics of running a step's functions into results (the transform/validation dispatchers). Per-function binding + execution collapse onto `contract.bind()` + `realize` — no per-function-class arms. Depends **down** on `realize`/`sql_engine`/`interpret`. | a new **step type** (a new `StepExecutor` in `STEP_EXECUTORS`), or new per-function execution mechanics. |
| `realize.py` *(L3)* | **BoundCall realization** — `realize(contract, call)`: one `BoundCall` → one worker call, strategized per `mode` (packed-frame wrappers for `column`/`row`, whole frame for `table`/`value`); resolved literals travel as `extra_kwargs`; the worker wire format is untouched. | a realization strategy for an execution mode, or the contract→worker transport. |
| `sql_engine.py` *(L3)* | **SQL-contract engine** — `render_sql` (column params as identifiers validated against the frame's actual columns — reject, never quote; scalars ALWAYS as `?` bound params) + `execute_sql_contract` (temp views for table/`source_ref` params, the legacy implicit `{source_table}` path). Not process-isolated (the backend's own query). | a SQL rendering/binding rule or SQL-contract execution concern. |
| `dnf.py` *(L3)* | **DNF mask combination** — `normalize_mask`/`combine_dnf`: one-level AND-within-group / OR-across-groups over boolean row masks — the orchestration above `date_range`'s predicate contracts (a NULL fails its condition). | a mask-normalization or group-combination rule. |
| `interpret.py` *(L3)* | **Validation-result interpretation** — normalize a worker's boolean output (Series/DataFrame/bool/`FailedFunctionEntry`) to pass/fail counts + failing rows, then `emit`. | how a validation result's shape maps to counts/rows. |
| `worker.py` | **Process-isolated execution** — run a user function in a subprocess (`setrlimit`, Arrow IPC), strict data-in/data-out (never receives the connection). | the worker/sandbox mechanics or its IPC contract. |
| `run.py` *(L4)* | **Run orchestration** — drive a source's whole run end-to-end (load → resolve → execute via the registry → stage → collect); cross-source runners. Injects `run_pipeline` into `resolve`. | a new run phase, run-type, or cross-source entry point. |
| `export.py` | **Run export** — produce the exportable `results report` / `transformed report` from a run's output. | a new export format or report shape. |
| **`middleware/`** — the API seam (HTTP routes; calls `backend` only) | | |
| `deps.py` | **Shared FastAPI dependencies** — `get_conn`, the request-scoped DuckDB connection provider that wires `app.config.DB_PATH` to the data-layer `get_connection` + `create_schema`. Lives here (not the data layer) so the connection↔app composition stays above the backend, keeping the data layer app-free (#49). | a new request-scoped dependency or connection-wiring concern. |
| `sources` `functions` `function_sets` `pipelines` `validations` `builtins` `settings` `.py` | **API routes** — one router module per resource: validate/parse the request, delegate to a `backend` function, shape the JSON response. No business logic of its own — no `conn.execute` in the seam (reads/guards live in `backend`; guarded by `test_source_read.py::test_middleware_seam_has_no_raw_sql`). | a new endpoint or request/response shape for that resource. |
| **`app/`** — composition root (wires the layers; owned by no feature) | | |
| `main.py` | **App wiring** — build the FastAPI `app`, mount the routers + the `src/pipeui/frontend/` static dir. | a new router include or app-level mount/middleware. |
| `config.py` | **Startup config + settings I/O** — owns `pipeui.config.json`: `load_settings`/`save_settings`/`CONFIG_PATH` plus the process-frozen `DB_PATH` (read once at import). | an app-level startup constant or a settings-file read/write concern. |
| `cli.py` | **CLI entry** — `pipeui <init\|start>` (scaffold config/db; launch uvicorn). | a new CLI subcommand. |

Dependency direction: `base/*` (ids, schema, tables, settings, fails, results) underlies everything;
within `data/functions`, `classification` (leaf) ← `contract` ← `binding` (which also pulls
`data/runner/bundles`). Within the runner, `steps` → `staging`/`step_loader`/`bundles` → `resolve` →
`realize`/`sql_engine`/`dnf`/`interpret` → `executors` → `run`, with `functions.builtins` consumed by
`executors` and `functions.builtin_lowering` consuming `runner.{sql_engine,dnf,resolve}` (the #41
coupling, now contract-mediated). Within `functions`, the registration chain flows `guardrails` + the
data-layer `classification`/`contract` ← `discovery` ← `registration`; `function_read` is an
independent read-only seam (no transaction, no discovery).
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
| `FunctionSpec` (`backend/data/runner/steps.py`) | one member of a function step | `step_loader` → `executors` (set adapter) | `frozen=True`; `params`/`builtin_config` are typed `Mapping` (the declared depth boundary); carries its hydrated `contract` + `binding` |
| `FunctionContract` + `ParamContract` (`backend/data/functions/contract.py`) | the universal description of one function-shaped thing (user `.py`/`.sql`, lowered built-in) | `discovery` (extraction) → `registration` (persist) / `step_loader` (rebuild) → `executors` (bind + realize) | `frozen=True`; derived facts are properties over `classification`, never stored; `to_registry_dict`/`from_registry_rows` round-trip is derivation-faithful |
| `StepBinding` + `ParamBinding` → `BoundCall` (`backend/data/functions/binding.py`) | one function's persisted binding on a source; a fully-resolved argument bundle | `step_loader` / `builtin_lowering` → `contract.bind()` → `executors` → `realize`/`sql_engine` | `frozen=True`; `bind()` is pure and frame-free; illegal bindings raise (`RequiredParamError`/`MixedShapeError`) before any worker spawns |
| `StepRunEnv` (`backend/domain/runner/executors.py`) | the run-scoped inputs an executor needs | `run` → `executors` | `frozen=True`; fully populated by the orchestrator; carries the injected `run_transforms` runner |
| `StepExecResult` (`backend/domain/runner/executors.py`) | the complete outcome of running one step | `executors` → `run` | `frozen=True`; `entries` are `StepResultEntry` variant carriers (never ad-hoc dicts) |
| `RunResult` / `ValidationRunResult` (`backend/data/base/results.py`) | canonical identity + data of one result | `executors` (mechanics) → `run` + Results/export | `frozen=True`; deterministic `result_id` |
| `StepResultEntry` + `Validation`/`Transform`/`BuiltinResultEntry` variants (`backend/data/base/results.py`) | one step's result = a `RunResult` plus its provenance, as a per-kind variant | `executors` → `run` (serialized to the wire dict at the published seam) | `frozen=True`; variant hierarchy ("the variant IS the contract"), each renders its own `to_dict()` — no kind-switch in the consumer |
| `FrameRef` (`backend/domain/runner/resolve.py`) | provenance of a resolved input frame | `resolve` → `functions.builtin_lowering` (join lowering) + `middleware/sources`, then into `RunResult.consumed_result_id` | `frozen=True`; invariant `result_id is None ⟺ mode == RAW` (in `__post_init__`); returned as `(frame, FrameRef)` |
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
