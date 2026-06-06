---
created: 2026-06-06
updated: 2026-06-06
purpose: >
  Build order for the codebase — the sequence of units of work that turn the
  design docs into code. Companion to design.md (intent), CLAUDE.md (what/why +
  principles), and CLAUDE_REFERENCE.md (how, by section). No application code
  exists yet; this file tracks the plan to build it. Living document: check off
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

Units are ordered by dependency. The **source track** (Phase 1–2) and the
**function track** (Phase 3) are largely independent after Phase 0 and can run in
parallel; they converge at function-attach.

---

## Decisions to resolve first (gating)

These are CLAUDE.md → Active Deferred Work items. Each one blocks clean
implementation of the unit(s) noted; resolve before (or at the start of) that
unit rather than encoding an answer in code.

- **`column_type` enum** — concrete allowed set. Gates `db-schema` (final form),
  `source-create` (inference + `var` fallback), `column-migration`.
- **PK uniqueness enforcement** — whether to validate the chosen/assumed PK is
  unique. Gates `source-create`.
- **`content_hash_id` edit-collision rule** (reject vs merge) — gates the
  `*Update` edit path in `validation-objects`.
- **Return-type vocabulary** (`vector`/`matrix` vs `pd.series`/`pd.dataframe`) —
  gates the final form of `function-registration` (§11 is currently written
  vocabulary-agnostic).
- **`function_signature` field** — store-or-drop decision. Gates
  `function-registration`.

---

## Phase 0 — Foundations

Everything depends on these. They are small, prove the test pattern, and unblock
the rest. Do them first.

- [ ] **`feat/id-generation`** — §2. The single injectable `new_id()` (uuid4)
  factory and the two-level `uuid5` `content_hash_id` (app-root → per-table
  namespace → content). Pure logic, no DB.
  *Guarantees:* recompute changes the hash only on contributing-field change;
  different table namespace → different hash; surrogate id never changes.

- [ ] **`feat/db-schema`** — §1. The single DuckDB file plus DDL for the four
  registries (`source_registry`, `function_registry`, `column_registry`,
  `parameter`) and three map tables (`source_column_map`, `source_function_map`,
  `alias_map`); surrogate `*_id` as PK. `column_type` stored as VARCHAR for now.
  *Gated by:* `column_type` enum (use VARCHAR placeholder; note the open item).
  *Guarantees:* tables/columns/PKs exist as specified; maps carry the composite
  `uuid5` id.

- [ ] **`feat/test-harness`** — §13. Real-DuckDB sandbox fixture (`:memory:`
  default, temp-file variant), schema-builder + "registered source with N
  columns" seeding helper, uuid-patch fixture, the file fixture-builder
  (writes real CSV/xlsx to a temp dir from quirk specs — no committed files),
  `unit`/`integration` markers. Must land before Phase 1 so later units can
  satisfy rule 9.

---

## Phase 1 — Source write path

- [ ] **`feat/validation-objects`** — §3, §4. pydantic v2 `*Entry`/`*Update` for
  source and column; `FailedRegistryEntry`/`FailedFunctionEntry` stacks.
  *Gated by:* `content_hash_id` edit-collision rule (surface the collision; do
  not decide it).
  *Guarantees:* field validation routes failures to the stack; `*Update`
  recomputes `content_hash_id` only on contributing-field edits; surrogate
  unchanged.

- [ ] **`feat/staging`** — §5. The transient DuckDB staging mechanism,
  create-flow (registration-metadata) variant. Boundary: talks only to the cache
  and the DB-URL config.

- [ ] **`feat/source-create`** — §6. First full vertical: read upload (filename
  `pattern`, columns, DuckDB-native type inference with `var` fallback,
  `st_mtime`) → stage in cache → build `SourceRegistryEntry` → the **one**
  transaction writing `source_registry` + `column_registry` (per column) +
  `source_column_map` (per column, direct write) → failures to
  `FailedRegistryEntry`.
  *Gated by:* `column_type` enum; PK uniqueness.
  *Headline guarantee:* source-create atomicity — inject a late failure, assert
  zero rows from the set persisted and the DB snapshot is unchanged.

---

## Phase 2 — Instance tables & data

- [ ] **`feat/jit-instance-table`** — §8. Build the per-source instance table JIT
  from `source_registry` + `column_registry`; the `sql_user_table/` generator,
  one module per table named `<source>_source_sql.py`.
  *Boundary guarantee:* the instance table never references the registry.

- [ ] **`feat/ingestion`** — §9. Staged load → write-to-real-table-on-success;
  `upsert`/`skip` on duplicate ids; ingested rows retained.
  *Guarantee:* ingestion atomicity (failed load leaves the table at last
  committed state).

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
  `function_type` / `function_return_type`, write `function_registry` +
  `parameter` rows as **one** transaction, collapse on `content_hash_id`.
  *Gated by:* return-type vocabulary; `function_signature`.
  *Guarantees:* the derivation table (§11); collapse preserves the surrogate
  `function_id` and overwrites only mutables; registration atomicity.

- [ ] **`feat/function-attach`** — §12. Validate the `alias_map` mapping
  (unmapped parameter/column fails the attach with a message), write
  `source_function_map` + `alias_map` as **one** transaction, keyword binding
  via `param_name`, multi-select loop (N eligible columns → N runs).
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
Phase 0:  id-generation ─┬─ db-schema ─── test-harness
                         │
Phase 1:                 ├─ validation-objects ─┐
                         │                       ├─ source-create ─┐
                         └─ staging ─────────────┘                 │
Phase 2:                                  jit-instance-table ── ingestion ── column-migration
                                                                              │
Phase 3:  function-worker ── function-registration ───────────────────┐      │
                                                                       └─ function-attach
                                          (attach needs a source + a registered function)
```
