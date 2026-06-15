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
