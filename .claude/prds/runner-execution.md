---
feature_slug: runner-execution
source_discovery_hash: a491ad816ac0df34fc7f7a125de083155e0abd7a75c0ff8c98b231f979304043
---

## Problem Statement

A user can configure a pipeline — attach functions to a source and map columns to
parameters through `alias_map` — but the runner does not honor the multi-select
binding model the design promises. When a parameter is bound to multiple columns (or
several parameters are each bound to multiple columns), the runner silently runs only
the **first** column and drops the rest, so results are incomplete. Column order is
ignored (the runner sorts alphabetically, not by what the user placed), so paired
arguments don't line up. And because the runner returns shapeless result dicts, the
Results screen mis-renders — a mixed validation/transform set shows the wrong card type
and its export fails (#193). The net effect: the user runs a pipeline and cannot trust
that the output is complete, correctly ordered, or even visible.

## Solution

Make the runner execute the full multi-select model end to end and surface it cleanly on
the Results screen. The runner becomes a **stateful per-step executor** that forms
`argument bundle`s — a positional zip across every `multi_select_eligible` parameter, in
user-placed order, with a `static param` (one bound column) broadcast into every bundle
and unequal-length varying parameters rejected up front — then runs the function once per
bundle, normalizing scalar-shaped functions to a vector via a `scalar run`, and produces
one `RunResult` per bundle. Validations surface as pass/fail **count** cards with readable
labels and export as a transposed `results report`. Transforms apply per `output_mode`
(append with a cleaned auto-label or a user-provided name, or replace into an ordered set
of user-chosen target columns) into the `transformed report`, also exportable. A minimal
results drawer — reusing an existing component — shows the per-run detail. The existing
single-column path keeps working as the `N = 1` special case, and #193 is fixed because
`RunResult` carries a real per-result type and identity.

## User Stories

1. As a pipeline builder, I want a function bound to multiple columns to run once per column instead of only the first, so that I get a result for every column I mapped.
2. As a pipeline builder, I want several parameters each bound to multiple columns to run as positionally-paired `argument bundle`s, so that the i-th run uses the i-th column of every parameter together.
3. As a pipeline builder, I want a parameter bound to a single column to broadcast into every bundle (a `static param`), so that I can pair a constant column with a set of varying columns.
4. As a pipeline builder, I want the app to reject an attach where varying parameters bind unequal column counts, so that I never get silently mismatched or dropped runs.
5. As a pipeline builder, I want bundles to run in the order I placed the columns, so that paired arguments line up the way I intended.
6. As a pipeline builder, I want column order to default to the order I added the columns, so that a pipeline runs correctly without any extra reordering step.
6a. As a pipeline builder, I want a minimal reorder control in the mapping modal (e.g. move-up/down or a numbered order, following existing conventions), so that I can change column order and test the full workflow end-to-end before the polished drag pane exists.
6b. As a pipeline builder, I want to bind multiple columns to a parameter and select replace-target columns in the mapping modal, so that I can configure a multi-select run from the UI and run it.
7. As an analyst, I want a scalar-shaped function applied to a column to run once per record and return a full vector (a `scalar run`), so that its results line up row-for-row like any other function.
8. As an analyst, I want each run's outcome captured as a `RunResult` with a stable identity and metadata, so that results stay consistent and complete regardless of the function's shape.
9. As an analyst, I want validation runs shown on the Results screen as pass/fail counts with a readable per-bundle label, so that I can see at a glance which column passed or failed.
10. As an analyst, I want to open a minimal drawer on a result to see more detail about that run, so that I can inspect what ran without leaving the screen.
11. As an analyst, I want to export validation results as a transposed `results report` (one row per run, pass/fail/metadata columns) that includes runs which passed, so that I keep a complete record.
12. As an analyst, I want to export a source's `transformed report` after all its transforms complete, so that I can hand off the transformed data.
13. As a pipeline builder, I want a transform in `append` mode to add a new column with a clean auto-label or a name I provide, so that outputs never collide and the export stays readable.
14. As a pipeline builder, I want a transform in `replace` mode to overwrite the ordered target columns I select (one per bundle), so that I can transform columns in place, including the multi-column case.
15. As an analyst, I want export labels normalized (no leading underscores or odd tokens), so that exported files aren't broken by malformed column names.
16. As an analyst, I want a `pd.DataFrame` transform to operate on and return the whole table, so that table-shaped transforms behave predictably.
17. As an analyst, I want my existing single-column pipelines to keep working unchanged, so that this upgrade does not regress what I already rely on.
18. As an analyst, I want a mixed validation/transform set to show the correct result card type and export successfully, so that I'm not misled by a wrong tag (#193).

## Implementation Decisions

- **Argument-bundle execution model (ADR-0001).** The runner forms `argument bundle`s by a positional zip across every `multi_select_eligible` parameter, in user-placed order: bundle `i` is each varying parameter's `i`-th column. A `static param` (exactly one bound column) is broadcast into every bundle. The function runs once per bundle. A literal `zip` is the baseline; any faster mechanism is acceptable as long as user-controlled ordering is preserved. A Cartesian product was explicitly rejected — pairing is positional, not every combination.
- **Equal-length rule (enforced at attach, frontend *and* backend).** All varying parameters (>1 bound column) must share one length `N` (= the bundle count); a length-1 parameter broadcasts. Two or more distinct lengths among the varying parameters is an error rejected at attach. Zip-shortest / silent truncation is rejected — silent column loss is the defect this feature removes. (`3,3,1` → ok, broadcast the `1`; `3,2` → reject.)
- **`multi_select_eligible` is a granularity-derived label** (above `scalar`: `column_backed`, `pd.Series`), independent of whether the columns are currently in `alias_map`. `pd.DataFrame` is **not** eligible: it binds no column, gets no `alias_map` row, and always receives the full table (one run); it may still participate as a whole-table broadcast when *other* parameters expand. (`CLAUDE_REFERENCE` §12 wins over design.md line 257.)
- **`scalar run` normalization.** A scalar-shaped function (plain `int`/`float`/`str`-not-`column_backed` parameters, or a scalar return) runs once per record and its per-row outputs are collected into a normalized vector, so the Results layer and downstream steps consume every function's output uniformly.
- **Column-order persistence — `position` on `alias_map`.** Add a `position INTEGER` column to `alias_map`, scoped per `(parameter_id, source_id)`. Attach writes add-order; the step PATCH rewrites positions on reorder; the runner and `get_pipeline` read `ORDER BY parameter_id, position` (replacing the alphabetical `column_name` sort); bundles pair by equal position. A separate ordering table was rejected — `alias_map` rows are already per-source, so a position cannot leak across sources, and a separate table would restate the binding `alias_map` already holds. Breaking DDL recreate is safe (no production data).
- **Stateful runner executor.** Refactor `workflow/run.py`'s free functions into a per-step executor object that holds the function, its input bindings, and (for `replace` transforms) its output-target binding, produces `RunResult`s, and applies no target for `append`.
- **`RunResult` object (net-new — no `Result`/`Outcome` type exists today).** The single backend source of truth for result data: status, pass/fail counts, the normalized vector, and a stable identity = a shortened `UUID5(function, argument bundle, source)`. A validation-specialized subclass carries pass/fail. It replaces the bare result dicts the runner returns today and is kept a focused result-holder — anything backend that deals with results uses it, but it is not a catch-all.
- **Transform output.** `append` adds a new column named by a cleaned auto-label (strip leading underscores and odd tokens — clean, never empty) or an optional user-provided name. `replace` overwrites an explicit, user-selected **ordered set of target columns** paired with bundles (bundle `i` → target `i`); the target count must equal the bundle count; for a single varying parameter the target defaults to the input varying column, user-overridable. A `pd.DataFrame` return always replaces the whole working table.
- **New output-target map.** A new mapping ties a transform step's output to its ordered target columns (`replace` only) — no existing table maps a function's *output* to a column (`alias_map` is param-keyed *input* bindings; `source_function_map` is source→set; `source_scalar_map` is scalar values). Likely keyed `(source_function_map_id, function_id)` → ordered `(column_id, position)`; whether it is a new table or `alias_map` with a role discriminator, and the exact DDL, is a `to-slices`/`to-code` decision.
- **Results surfacing — three tiers.** (1) Results screen **card** = pass/fail count + a readable varying-column label, with the `UUID5` as the identity underneath; (2) a **minimal drawer** reusing an existing drawer component shows `RunResult` metadata (the full rich drawer is out of scope / design-gated); (3) two **exports** — the `results report` (transposed: one row per `RunResult`, pass/fail/metadata columns) for validations, with two entry points (from the Functions page each attached source ran; tied to a source each validation function ran), and the `transformed report` (the transformed data table). Both include passing runs; all export labels are normalized.
- **API.** `POST /pipelines/{source_id}/run` and `GET /pipelines/{source_id}/staging` serialize `RunResult`s; an equal-length violation returns a **structured failure**, not a 500; the step `PATCH` supports column reorder.
- **#193 folded in.** `RunResult`'s per-result type and identity fix the mixed-set wrong card-type tag and the staging-export failure. #160 (empty-pipeline result card) stays a separate ticket.
- **Docs.** Reconcile `design.md` (state `multi_select_eligible` as a granularity label; document the multi-parameter argument-bundle model with static-param broadcast and the equal-length rule; remove `pd.DataFrame` from the eligible enumeration at line 257; introduce the canonical terms) and `CLAUDE_REFERENCE` §12, as a doc deliverable of this feature.

## Testing Decisions

**What makes a good test here.** Assert observable behavior — per-table row counts, DB snapshot state, API response shape, exported-file shape — never internal call sequences. Per §13, SQL/transaction behavior runs against a **real ephemeral DuckDB sandbox** (fresh per test via the `create_schema` builder + `make_registered_source`), not a fake; the worker boundary is mocked only when a test cares solely about write-back. One test per documented guarantee, named for it, referencing the clause it guards.

**Feature-level seams:**

- **Pure-logic seam (`unit`, no DB) — load-bearing.** `argument bundle` pairing is **extracted as a pure function** (per-parameter ordered bindings → an ordered list of bundles, or a validation error) so it is tested directly and exhaustively, independent of DuckDB and the worker: zip-by-position yields `N` ordered bundles; a length-1 `static param` broadcasts; unequal-length-among-varying is rejected; the matrix `3,3,1`→ok, `3,2`→reject, single-param `N`→`N` bundles, all-static→1 bundle. Also covers label normalization (no leading `_`, no `__`, never empty) and `RunResult` `UUID5` identity determinism. The same pure function is exercised end-to-end by the executor seam, so pairing has both fast exhaustive coverage and integration coverage.
- **Runner/executor seam (`integration`)** — extend `test_run_workflow.py` (highest seam = the stateful executor / `run_pipeline`): bundles run in position order; `static param` broadcast; `scalar run` normalization to a vector; one `RunResult` per bundle; transform `append` (cleaned/user name) and `replace` (ordered target columns) into the `transformed report`; `pd.DataFrame` whole-table edge. Real subprocess where execution itself is the guarantee.
- **Attach seam (`integration`)** — extend `test_attach.py`: `alias_map.position` written in add-order; PATCH rewrites positions; the equal-length-among-varying guard rejects a mismatched attach as a structured failure; output-target map rows written for `replace` steps.
- **API seam (`integration`, FastAPI TestClient)** — extend `test_api_pipelines_run.py`, `test_api_staging.py`, `test_api_validations.py`: `POST /run` serializes per-bundle `RunResult`s with labels; an equal-length violation returns a structured error (not 500); reorder `PATCH`; `results report` (transposed) and `transformed report` exports produce the documented shapes, including passing runs.
- **Schema seam** — extend migration/schema tests (prior art `test_api_migration.py` + the `create_schema` builder): `alias_map` has `position`; the output-target map table exists with the expected shape.
- **Frontend seam (vitest + jsdom)** — the Results screen renders pass/fail **count** cards with readable labels; the minimal drawer opens with `RunResult` metadata; the Builder surfaces the equal-length guard.
- **Regression lock** — the `N = 1` single-column path is unchanged (extend the existing run tests); a **named test** asserts #193's mixed-set card-type is correct (and export succeeds) via `RunResult`.

**Prior art:** `test_api_pipelines_run.py`, `test_run_workflow.py`, `test_attach.py`, `test_api_staging.py`, `test_api_validations.py`, `test_api_migration.py`, and the existing frontend vitest + jsdom harness.

## Out of Scope

- **Polished/rich results drawer redesign** — design-gated, future; MVP **does** ship a minimal drawer reusing an existing component, so only the polished redesign is gated and layers on top.
- **Polished drag-reorder pane UI** — design-gated, future; MVP **does** ship a minimal conventions-following reorder control (move-up/down or numbered order in the mapping modal) plus backend add-order inference and the `position` column. Only the polished drag pane is gated and layers on top.
- **#160 empty-pipeline result card** — a separate, orthogonal frontend guard with its own ticket.
- **Broadcasting parameters with length >1 but <N** — only length-1 `static param`s broadcast; every other varying parameter must equal `N`.
- **`pd.DataFrame` multi-select as column-subset execution** — rejected; no mechanism exists, the full table is always passed.
- **v2 per-source scalar argument persistence** beyond what `source_scalar_map` already does — project-wide deferred.
- **Cross-source joins / multi-version staging history in the Results layer** — v2.

## Further Notes

- The feature branch `release/runner-execution` was cut from `release/v0.0.1-beta` (the repo's beta accumulator), so the eventual feature PR targets `v0.0.1-beta`, not `main`.
- The single-column transform + validation path is believed to work end-to-end today, but is to be **re-validated**, not assumed — it becomes the `N = 1` special case of the new path.
- `RunResult` must stay a focused result-holder (status, counts, normalized vector, identity metadata) and not absorb unrelated responsibilities.
- The minimal results drawer reuses an existing drawer component (the function/set detail drawer family, cf. #215); the exact component is a `to-slices`/`to-code` detail.
- Performance: `N` bundles = `N` process-isolated worker invocations per function. Acceptable for v1 (single trusted local user); batching is deferred and is not a blocker.
- `ADR-0001` (`docs/adr/0001-multi-select-execution-model.md`) records the architecture-shifting core (argument bundles, the stateful executor, `RunResult`, the output-target map) and the rejected alternatives.
- **Decomposition guidance for `to-slices` / `to-issues` (vertical slices, design-gating layered on top).** Per `to-slices`, each slice cuts every layer it needs — data → business logic → api → ui — so it is independently testable end-to-end; **do not** decompose by horizontal layer, and **no backend-only slices**. Concretely:
  - Every MVP slice ships a **minimal, conventions-following frontend** so the end user can run its user story start to finish. The new MVP frontend bits — **multi-column binding + order and replace-target selection in the mapping modal**, the **minimal reorder control** (move-up/down or numbered order), the **multi-result Results cards**, and the **minimal drawer** — follow existing mapping-modal/card conventions and live *inside* their vertical slice. None of these is design-gated.
  - The **two design-gated tickets** are the *polished redesign layer only*: the polished drag-reorder **pane** and the **rich** results drawer. Each is `blocked-on-design` and **`depends_on` the corresponding MVP vertical slice** — it enhances a workflow that is already shippable and testable. The gated UI **never blocks** an MVP slice; the dependency points from the polish to the MVP, not the reverse. (The earlier "backend ships alone + a tie ticket converges with the gated UI" framing was a horizontal split and is dropped.)
