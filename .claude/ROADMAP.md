---
created: 2026-06-06
updated: 2026-06-07
purpose: >
  Build order for the codebase — the sequence of units of work that turn the
  design docs into code. Companion to design.md (intent), CLAUDE.md (what/why +
  principles), and CLAUDE_REFERENCE.md (how, by section). Phases 0–1 are
  implemented (see checkmarks); this file tracks the remaining build. Reorg /
  fix debt is tracked separately in REFACTOR_PLAN.md. Living document: check off
  or delete units as they are completed.
---

# ROADMAP.md

## How to use this file

- **One branch per unit** (CLAUDE.md rule 4). A suggested branch name is given
  per unit; use your own convention if you prefer.
- **Each unit ships its guarantee tests.** Any behavioral guarantee a unit
  implements must have a test (CLAUDE.md rule 9). Read CLAUDE_REFERENCE.md §13
  for the behavioral-guarantee pattern and the DuckDB sandbox / fixture strategy
  *before* writing them.
- **Read before editing** (rule 7). Each unit lists the CLAUDE_REFERENCE.md
  section to read first; that section is the unit's implementation contract.
- **Confirm reasoning before code** (rule 1) and **ask about module boundaries**
  (rule 2) at the start of each unit.
- **Don't start a unit whose gating decision is still open** without resolving it
  first (rule 10). Gating decisions are listed below and tagged per unit.
- **Reorg/fix debt → REFACTOR_PLAN.md.** Move/rename/unify items left by the
  implementation+reorg session live there, not here.

Units are ordered by dependency. The **source track** (Phase 1–2) and the
**function track** (Phase 3) are largely independent after Phase 0 and can run in
parallel; they converge at function-attach.

---

## Decisions to resolve first (gating)

These are CLAUDE.md → Active Deferred Work items. Each one blocks clean
implementation of the unit(s) noted; resolve before (or at the start of) that
unit rather than encoding an answer in code.

- **`column_type` enum** — concrete allowed set. Gates `db-schema` (final form),
  `source-create` (inference + fallback), `column-migration`. (The *fallback* is
  resolved → `VARCHAR`; only the full allowed set is still open.)
- **PK uniqueness enforcement** — whether to validate the chosen/assumed PK is
  unique. Gates `source-create`.
- **Return-type vocabulary** (`vector`/`matrix` vs `pd.series`/`pd.dataframe`) —
  gates the final form of `function-registration` (§11 is currently written
  vocabulary-agnostic).

*Resolved since the last revision (no longer gating):*
- **`content_hash_id` edit-collision rule** → **reject** (surface as failure),
  enforced at the write boundary (CLAUDE.md Principle 1; §2/§3). Wiring tracked in
  REFACTOR_PLAN.md.
- **`function_signature` field** → **retained and defined**: canonical
  `param_name: type` signature for keyword binding (§1, §12). Must be added back
  to the `function_registry` DDL (REFACTOR_PLAN.md).
- **`column_type` uninferable fallback** → **`VARCHAR`** (was `var`).

---

## Phase 0 — Foundations

Everything depends on these. They are small, prove the test pattern, and unblock
the rest.

- [x] **`feat/id-generation`** — §2. The single injectable `new_id()` (uuid4)
  factory and the two-level `uuid5` `content_hash_id` (app-root → per-table
  namespace → content). Pure logic, no DB. Implemented (`pipeui/validation/ids.py`).
  *Note:* being relocated to foundational `pipeui/ids.py` (REFACTOR_PLAN.md).
  *Guarantees met:* recompute changes the hash only on contributing-field change;
  different table namespace → different hash; surrogate id never changes.

- [x] **`feat/db-schema`** — §1. The single DuckDB file plus DDL for the four
  registries and three map tables; surrogate `*_id` as PK. `column_type` stored
  as VARCHAR for now. Implemented (`schema/queries.py`, `schema/constants.py`,
  `duckdb.py`).
  *Open items:* `column_type` enum (VARCHAR placeholder, noted); **`function_signature`
  must be added back to the `function_registry` DDL** — it was omitted
  (REFACTOR_PLAN.md).
  *Guarantees met:* tables/columns/PKs exist as specified; maps carry the composite
  `uuid5` id.

- [x] **`feat/test-harness`** — §13. Real-DuckDB sandbox fixtures (`db`,
  `db_file`), `patch_new_id` uuid fixture, `make_registered_source` seeding
  helper, `unit`/`integration` markers. Implemented (`tests/conftest.py`,
  `pyproject.toml`).
  *Partial / debt:* the richer quirk-encoding file fixture-builder (mixed-type
  for TRY_CAST, ambiguous-type, VARCHAR-fallback) is **not yet built** — owed for
  inference/migration tests. `patch_new_id` has a stale patch target
  (REFACTOR_PLAN.md).

---

## Phase 1 — Source write path

- [x] **`feat/validation-objects`** — §3, §4. pydantic v2 `*Entry`/`*Update` for
  source and column; `FailedRegistryEntry`/`FailedFunctionEntry` stacks.
  Implemented (`validation/source.py`, `column.py`, `fails.py`).
  *Debt:* the edit-collision **reject** check is not yet wired into the write
  path (currently simulated inline in `test_validation.py`); the
  `SourceRegistryEntry` ingestion_method validator allows only `upsert`/`skip` and
  must accept all three (REFACTOR_PLAN.md).
  *Guarantees met:* field validation routes failures to the stack; `*Update`
  recomputes `content_hash_id` only on contributing-field edits; surrogate
  unchanged.

- [x] **`feat/staging`** — §5. The transient DuckDB staging mechanism,
  create-flow (registration-metadata) variant (`workflow/staging.py`,
  `CreateFlowCache`). Boundary: talks only to the cache and the DB connection.

- [x] **`feat/source-create`** — §6. First full vertical: read upload (filename
  `pattern`, columns, DuckDB-native type inference with `VARCHAR` fallback,
  `st_mtime`) → stage in cache → build `SourceRegistryEntry` → the **one**
  transaction writing `source_registry` + `column_registry` + `source_column_map`
  → failures to `FailedRegistryEntry`. Implemented (`workflow/create.py`).
  *Debt:* invalid `ingestion_method` currently raises uncaught instead of routing
  to `FailedRegistryEntry`; enum/validator disagreement means only `upsert` works
  today (REFACTOR_PLAN.md).
  *Gated by:* `column_type` enum; PK uniqueness (both still open).

---

## Phase 2 — Instance tables & data

- [ ] **`feat/jit-instance-table`** — §8. Build the per-source instance table JIT
  from `source_registry` + `column_registry`; the `sql_user_table/` generator,
  one module per table named `<source>_source_sql.py`.
  *Boundary guarantee:* the instance table never references the registry.

- [ ] **`feat/ingestion`** — §9. Staged load → write-to-real-table-on-success;
  `upsert` / `append` / `skip` on duplicate ids (`append` = straight insert;
  `skip` = skip existing ids **and report the skipped rows to the user**);
  ingested rows retained.
  *Guarantees:* ingestion atomicity (failed load leaves the table at last
  committed state); `skip` reports the dropped rows (rule 9 — owes a test).

- [ ] **`feat/column-migration`** — §7. `TRY_CAST` pre-check (report
  un-castable rows) → recreate-and-copy with the column cast → atomic swap,
  all in one transaction. Comes after data exists.
  *Gated by:* `column_type` enum.
  *Guarantees:* migration atomicity; `TRY_CAST` reports un-castable rows before
  commit (no silent NULL loss).

---

## Phase 3 — Function track (parallel with Phases 1–2 after Phase 0)

- [ ] **`feat/function-worker`** — §10. Per-call worker process; wall-clock
  timeout; unconditional `setrlimit` CPU/memory caps (Unix-only, no Windows
  guard); Arrow IPC data boundary; per-user venv + lockfile; data-only
  interface. Large — consider splitting worker-isolation from venv/lockfile.
  *Guarantees:* worker receives data only (never the connection/app objects);
  timeout kills a looping function; a crashing function takes the worker not the
  app (surfaced via `FailedFunctionEntry`); `setrlimit` memory cap kills an
  allocate-big function. (Integration tests run on Linux CI.)

- [ ] **`feat/function-registration`** — §10 (registration txn), §11. Load a
  `.py`, validate typed params/returns, derive `function_class` /
  `function_type` / `function_return_type`, capture `function_signature` (the
  canonical `param_name: type` string), write `function_registry` + `parameter`
  rows as **one** transaction, collapse on `content_hash_id`.
  *Gated by:* return-type vocabulary.
  *Guarantees:* the derivation table (§11); collapse preserves the surrogate
  `function_id` and overwrites only mutables; registration atomicity.

- [ ] **`feat/function-attach`** — §12. Validate the `alias_map` mapping
  (unmapped parameter/column fails the attach with a message), write
  `source_function_map` + `alias_map` as **one** transaction, keyword binding
  via `param_name` (following `function_signature`), multi-select loop
  (N eligible columns → N runs).
  **Convergence point:** needs a source (Phase 1) and a registered function
  (Phase 3).
  *Guarantees:* attach atomicity; unmapped param/column fails the attach;
  multi-select runs once per eligible column.

---

## Phase 4 — Deferred

- [ ] **Results & Summary layer** — shape depends on the user's data; deferred
  until the tracks above exist (CLAUDE.md → Active Deferred Work).
- [ ] **CLI + visual layer** — §14 (reference) / CLAUDE.md CLI reference. Document
  commands and the visual layer here and in §14 as built.
- [ ] **v2 scalar persistence** — per-source scalar-override store so UI
  overrides survive across runs (CLAUDE.md → Active Deferred Work).

---

## Dependency summary

```
Phase 0:  id-generation ─┬─ db-schema ─── test-harness          [Phase 0 done]
                         │
Phase 1:                 ├─ validation-objects ─┐               [Phase 1 done]
                         │                       ├─ source-create ─┐
                         └─ staging ─────────────┘                 │
Phase 2:                                  jit-instance-table ── ingestion ── column-migration
                                                                              │
Phase 3:  function-worker ── function-registration ───────────────────┐      │
                                                                       └─ function-attach
                                          (attach needs a source + a registered function)
```
