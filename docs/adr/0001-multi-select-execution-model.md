---
status: accepted
---

# Multi-select execution model: argument bundles, a stateful runner, and RunResult

## Context & Decision

The runner must execute a function once per *combination* of bound columns, not once
total. We model this as the **argument bundle**: the runner pairs the bound columns of
every `multi_select_eligible` parameter **positionally**, in user-placed order, and runs
the function once per index `i` (`bundle[i]` = each varying param's `i`-th column). A
`static param` (one bound column) broadcasts into every bundle; all *varying* params (>1
column) must share one length `N`, enforced at attach in both frontend and backend.
Silent zip-shortest truncation is explicitly rejected — silent column loss was the defect
this model removes.

To carry this, the runner becomes a **stateful per-step executor** (it holds the
function, its input bindings, and — for `replace` transforms — an explicit ordered
**output-target map**), and every run yields a **`RunResult`**: the single backend
result-holder (status, pass/fail counts, the `scalar run`-normalized vector, and a
`UUID5(function, argument bundle, source)` identity), specialized for validations. Results
surface as a transposed `results report` (row per `RunResult`) and a `transformed report`
(the data table); the existing single-column path is the `N = 1` special case.

## Considered Options

- **Per-param expansion only** (design.md's original single-param example): run a function
  N times for one param's N columns. Rejected — it can't express the real need of pairing
  several params (`country` × `team`) into aligned runs.
- **Cartesian product** across params: rejected — produces nonsensical unintended
  combinations; the user wants positional pairing, not every combination.
- **`position` on a separate ordering table** vs on `alias_map`: rejected the separate
  table — `alias_map_id` already includes `source_id`, so positions are per-source and a
  separate table would only restate the binding `alias_map` holds (sync hazard, violates
  minimal-correct-design).
- **Disallow `replace` for multi-varying functions**: rejected in favor of a first-class,
  user-selected ordered output-target map, so multi-column transforms can write back
  predictably.

## Consequences

- Breaking DDL: `alias_map` gains a `position` column; a new output-target map table is
  introduced. Safe to recreate (no production data).
- `pd.DataFrame` params never drive expansion (full table always passed) — design.md
  line 257 and `CLAUDE_REFERENCE.md §12` are reconciled to this.
- N bundles = N process-isolated worker invocations per function; acceptable for the v1
  single-trusted-local-user model, batching deferred.
- The rich results drawer and the drag-reorder pane are design-gated; MVP ships backend
  add-order inference plus a minimal drawer reusing an existing component.
