---
feature_slug: param-binding-output-mode
source_discovery_hash: 96b526b6462bee284722e7d19caa36f3e888a8147d247501c3c630909ff70530
---

## Problem Statement

In the Report Builder, a function parameter can only be backed by a column when
it is a `str` (or a `pd.Series`/`pd.DataFrame`). A numeric or boolean parameter
(`int`, `float`, `bool`) can *only* take a typed constant — there is no way to
point it at a column. So an analyst who wants to feed an integer column into a
function, or run a per-record numeric/boolean check, simply can't: the
"Column-backed" option never appears. The same limitation has a second face —
because the **replace-target** picker only shows for column-bound parameters, a
function whose only inputs are numeric has nothing to replace and nothing to
append a result against, so its output controls look broken.

Separately, when several functions are grouped into a `function_set`, the Builder
offers a single Append/Replace choice for the *whole set*, even though each
function may need its own — one function appending a new column while another
replaces an existing one.

## Solution

Give numeric and boolean parameters (`int`, `float`, `bool`) the **same Plain
string / Column-backed toggle** that `str` already has. **Text mode** keeps
today's behaviour exactly: a typed constant broadcast to every row, or — when the
field is left blank — the function's Python default. **Column mode** lets the
user pick one or more columns; the function then runs **once per record** (a
`scalar run`), and binding multiple columns produces one run per `argument
bundle`, exactly as `column_backed`/`pd.Series` parameters already do. With a
numeric parameter now column-bound, the Append/Replace output controls and the
replace-target picker light up for those functions too.

And each function inside a `function_set` gets its **own** Append-vs-Replace
control in the Builder (with its own new-column name or ordered replace targets),
so a multi-function set can mix outputs per member.

## User Stories

1. As a pipeline builder, I want to bind an `int`/`float`/`bool` parameter to a
   column instead of only typing a constant, so that I can run a function over a
   numeric or boolean column once per record.
2. As a pipeline builder, I want a numeric parameter I leave in text mode to keep
   broadcasting its typed constant to every row, so that the simple constant-
   argument workflow is unchanged.
3. As a pipeline builder, I want a numeric parameter I leave blank to fall back to
   the function's Python default, so that I don't have to restate defaults the
   function already declares.
4. As a pipeline builder, I want to bind a numeric parameter to several columns and
   have the function run once per `argument bundle`, so that I can process multiple
   columns in a single step under the same equal-length rules as other params.
5. As a pipeline builder, when a parameter has no typed value, no bound column, and
   no Python default, I want to be blocked when I save the step — with the same
   clear message an unbound `str` gives — so that I never hit a per-row crash at run
   time.
6. As a pipeline builder, when I re-open a saved step, I want my numeric parameter's
   mode (text vs column) and bound-column order restored exactly as I left them, so
   that editing one field never silently resets another.
7. As a pipeline builder, I want each function within a `function_set` to have its
   own Append vs Replace choice — with its own new-column name or ordered replace
   target columns — so that a multi-function set can mix per-member outputs.
8. As a pipeline builder, I want the replace-target picker to appear for a function
   whose inputs are numeric once I've bound a column, so that I can direct that
   function's output to an explicit column like any other.

## Implementation Decisions

**SRP / SOLID is a binding constraint on this feature, not an aspiration.** Every
decision below is shaped so the change *removes* duplication and special-casing
rather than adding more. The coding agents must honour these guardrails:

- **One source of truth for binding eligibility (`binding_kind`).** Add a single
  pure derivation, `binding_kind(type_str)`, in the function-classification leaf,
  reading off the **existing** `function_class`: `scalar` (`int`/`float`/`bool`/
  `str`) → `value_or_column`; `pd.Series`/`pd.Series[bool]` → `column_only`;
  `pd.DataFrame` → `table`. The four parallel literals that encode "which types may
  bind a column" today — the binding-suggestion module's scalar/suggest type sets
  and the attach module's requires-binding set — are **deleted** and replaced by
  calls to `binding_kind`. The frontend keys off the `binding_kind` the API emits,
  never hardcoded type strings. (DRY + OCP: a future bindable type is one descriptor
  concern, not a four-site edit.)
- **No granularity change.** Investigation showed the descriptor's `granularity`
  field has no consumer outside the classification leaf and does not drive binding;
  bumping it would be inert and would muddy the documented §11 ordering. Do not touch
  it. Eligibility flows through `binding_kind` only.
- **Classification stays a DB-free leaf.** `binding_kind` is pure derivation — no
  connection, filesystem, or app object. It is the single place the rule lives.
- **The runner is closed for modification.** The execution engine (step loading,
  `argument bundle` pairing, scalar-param resolution, the per-record `scalar run`
  wrapper, output append/replace, RunResult identity) is already type-agnostic and
  already detects scalar-shaped parameters. This feature adds **zero** runner code
  and **no** type branches downward. It adopts the frozen `runner-execution`
  semantics (`multi_select_eligible`, `argument bundle`, the output-target map) as-is.
- **Optional binding, blocked-at-attach.** The attach-time missing-binding check is
  generalized so a parameter is satisfied by **any** of: a column binding, a typed
  literal value, or a declared Python default. A parameter with none of the three is
  rejected as the same structured failure an unbound `str`/`pd.Series` produces
  today. The frontend Save-guard mirrors this so the user is blocked before the
  request, not after.
- **Suggestion + round-trip.** The binding-suggestion module emits `binding_kind`
  per parameter and returns `current_bindings` (in saved `alias_map.position` order)
  for `value_or_column` params, exactly as it does for `str`/`pd.Series` today. On
  edit-load the modal opens a numeric param in column mode when `current_bindings`
  is non-empty (order preserved) and in text mode with its saved value otherwise;
  switching text→column clears the stale scalar so there is a single source of truth
  for that param's argument. (Principle 7 — partial-update discipline.)
- **Per-function output mode — extend, don't add surface.** Per-function output is
  already persisted and executed (`function_output_config` + the output-target map,
  keyed by `(source_function_map_id, function_id)`); the gap is the UI and the edit
  path. Extend the existing placed-step `PATCH` (`patch_pipeline_step`) with an
  optional `function_output` map keyed by `function_id` that updates
  `function_output_config` + the output-target map transactionally, and **stops**
  writing the now-vestigial set-level `output_mode` (which cannot represent a mixed
  set; the runner already reads the per-function config first). The API route widens
  the existing `PATCH` body only — **no new route, no new table**. `step_edit`
  remains the single owner of placed-step edits (SRP); the route stays a thin seam
  with no raw SQL.
- **Frontend is a single-owner shared surface.** All mapping-modal changes for both
  capabilities land in `screen-builder.jsx` through one work-unit. Generalize the
  `str`-only conditionals to the backend-emitted `binding_kind` via a single
  predicate — do not scatter `["str","int","float","bool"]` literals across the
  modal. Relocate the Append/Replace control from the set-level step card into each
  function's group.
- **No schema migration.** Capability 1 adds no DDL (numeric parameters can already
  hold `alias_map` rows structurally). Capability 2's tables already exist. Any DDL
  that did arise would live in the data/base schema layer, never inline.

## Testing Decisions

**These are feature-level test seams — layered by where behaviour is observable —
NOT vertical slices.** Slicing into verticals happens next, in `to-slices`, which
grounds each slice's acceptance criteria on the seams below. The feature is
described here as one whole; no `slice_id`s appear in this PRD.

**What makes a good test.** Assert observable behaviour — per-table row counts, DB
snapshot state, API response shape, rendered controls — never internal call
sequences. SQL/transaction behaviour runs against a **real ephemeral DuckDB
sandbox** (fresh per test via the schema builder + a registered-source helper), per
§13. One test per documented guarantee, named for the guarantee it guards.

**Feature-level seams:**

- **Pure-logic seam (`unit`, no DB) — load-bearing.** `binding_kind(type_str)` is a
  pure function tested exhaustively and directly: `int`/`float`/`bool`/`str` →
  `value_or_column`; `pd.Series`/`pd.Series[bool]` → `column_only`; `pd.DataFrame` →
  `table`. Because every other layer derives from it, this is where eligibility gets
  fast, complete coverage. Prior art: the existing classification-leaf unit tests
  alongside `derive_function_class`.
- **Attach seam (`integration`)** — extend `test_attach.py`: a numeric param bound
  to column(s) writes `alias_map` rows; the generalized missing-binding rule is
  satisfied by binding OR literal OR `has_default`, and a numeric with none of the
  three is rejected as a structured failure; a multi-column numeric exercises the
  equal-length-among-varying guard. The existing
  `test_scalar_param_exempt_from_binding` / `test_dataframe_param_exempt_from_binding`
  are updated to the new rule.
- **Suggestion seam (`integration`)** — extend `test_attach.py`: a numeric param is
  returned with `binding_kind = value_or_column`, populated `suggested_columns`, and
  `current_bindings` round-tripping in `alias_map.position` order (Principle 7).
- **API seam (`integration`, FastAPI TestClient)** — extend `test_api_pipelines.py`:
  the extended step `PATCH` accepts a `function_output` map keyed by `function_id`;
  per-function config persists; a **mixed set** (one member appends, another
  replaces) round-trips; a block-at-attach violation returns a structured error, not
  a 500.
- **Frontend seam (`vitest` + jsdom)** — extend `screen-builder.test.jsx`: a numeric
  param renders the Plain string / Column-backed toggle; column mode sends bindings
  and clears the stale scalar; round-trip restore opens in column mode when
  `current_bindings` exist (order preserved); the per-function Append/Replace control
  renders per function within a set and patches only that function.
- **Regression lock** — the existing `str`/`pd.Series` toggle and the numeric
  *text-mode* broadcast path are unchanged; a legacy per-set `output_mode` still
  resolves for legacy steps lacking per-function config.

**Prior art:** `test_attach.py`, `test_api_pipelines.py`, the classification-leaf
unit tests, and the existing frontend vitest + jsdom harness (`screen-builder.test.jsx`).

## Out of Scope

- The `rename` built-in step (#40, runs last) — kept exactly as-is: not modified,
  not removed, not folded into output-mode. It remains a first-class built-in.
- Any runner / executor / bundle / scalar-resolution execution-engine code change —
  the runner is already type-agnostic and per-function-output-aware.
- v2 per-source scalar-argument persistence beyond what `source_scalar_map` already
  does (project-wide deferred, inherited from `runner-execution`).
- `pd.DataFrame` column-subset binding (rejected in `runner-execution`; the full
  table is always passed).

## Further Notes

- **Extends the frozen `runner-execution` epic.** This feature reuses its
  `multi_select_eligible`, `argument bundle`, and output-target-map semantics rather
  than re-deriving them.
- **Glossary.** The new canonical term `binding_kind` is defined in `CONTEXT.md`, and
  the `scalar run` definition was reconciled to admit column-bound numerics.
- **Assumptions carried from discovery:** `screen-builder.jsx` is a single-owner
  shared surface (slicing concern); the runner requires zero code changes for
  capability 1 (re-verify against current code at build time); `has_default` /
  `default_value` hold the Python default a numeric param falls back to; existing
  attachments with a numeric scalar value keep working and open in text mode (no
  regression, no migration).
