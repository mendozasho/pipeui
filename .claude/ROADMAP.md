---
created: 2026-06-06
updated: 2026-06-08
purpose: >
  Build order for the codebase — the sequence of units of work that turn the
  design docs into code. Companion to design.md (intent), CLAUDE.md (what/why +
  principles), and CLAUDE_REFERENCE.md (how, by section). Phases 0–1 are
  implemented (see checkmarks); Phases A–F are the vertical delivery plan (each
  ships backend + API + frontend together). Reorg / fix debt is tracked
  separately in REFACTOR_PLAN.md. Living document: check off or delete units as
  they are completed.
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

Units are ordered by dependency. Phases 0–1 are completed (all items checked).
Phases A–F are the active vertical delivery plan — each ships backend + API +
frontend together so the app is runnable after every phase.

---

## Decisions to resolve first (gating)

These are CLAUDE.md → Active Deferred Work items. Each one blocks clean
implementation of the unit(s) noted; resolve before (or at the start of) that
unit rather than encoding an answer in code.

- **`column_type` enum** — concrete allowed set. Gates `feat/column-migration`
  (Phase C). (The *fallback* is resolved → `VARCHAR`; only the full allowed set
  is still open.)
- **PK uniqueness enforcement** — whether to validate the chosen/assumed PK is
  unique. Relevant to Phase A (`POST /sources`) if enforcement is added.
- **Return-type vocabulary** (`vector`/`matrix` vs `pd.series`/`pd.dataframe`) —
  gates `feat/function-registration` (Phase D; §11 is currently written
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

## Vertical Phases — all three layers per phase

Each phase ships backend workflow + API route + frontend feature together. The
app is runnable and end-to-end testable after every phase. `frontend/data.jsx`
mock data shrinks one slice per phase as real `fetch()` calls replace it.

Read CLAUDE_REFERENCE.md §14 for the full screen-to-route wiring table and the
frontend design system before working on any frontend or API unit.

---

### Phase A — Source Registration

*Backend already done (Phase 1). This phase wires the API and Data screen.*

- [x] **`feat/api-sources-register`** — §14 (API), §6 (workflow).
  `pipeui/api/sources.py`: `GET /sources`, `POST /sources`. FastAPI app
  entry-point (`pipeui/main.py`) + static file mount for `frontend/`. Wires
  `create_source()` from `workflow/create.py` to the POST route via
  `Depends(get_conn)` injection. Fixed a bug in `create_source` where shared
  column definitions (same name+type across two sources) hit the `UNIQUE`
  constraint — existing `column_registry` rows are now reused.
  *Frontend:* `screen-data.jsx` — dropzone (CSV + xlsx) posts to `POST /sources`;
  reports table reads from `GET /sources`; drawer shows column schema. Full UI
  shell shipped: `index.html`, `app.jsx`, `ui.jsx`, `tweaks-panel.jsx`,
  `screen-modules.jsx` (Phase D placeholder), `screen-builder.jsx` (Phase E
  placeholder). `data.jsx` retains only Phase D–E mock stubs.
  *Note:* flat layout (`pipeui/` not `src/pipeui/`) retained; move deferred to
  production packaging. `DB_PATH` is a hardcoded constant; will become an app
  setting when that feature is wired.
  *Guarantees met:* `POST /sources` returns a `FailedRegistryEntry` payload (not
  a 500) when source creation fails; Data screen renders the failure inline.

---

### Phase A2 — App Settings

*Builds directly on Phase A's frontend shell. Resolves `DB_PATH` / `os` import
cleanup from REFACTOR_PLAN.md before Phase B adds more routes.*

- [ ] **`feat/app-settings`** — §14 (API + frontend).
  `pipeui/api/settings.py`: `GET /settings`, `PATCH /settings`. Read and write
  `pipeui.config.json` at the repo root via an `AppSettings` pydantic model
  (`db_path`, `accent`, `density`). Add `pipeui.config.json` to `.gitignore`.
  Replace hardcoded `DB_PATH` in `main.py` and `sources.py` with the settings
  object (clears REFACTOR_PLAN.md debt). `get_conn` reads `db_path` from
  `AppSettings` at startup.
  *Frontend:* `screen-settings.jsx` — fourth nav item triggered from the gear
  icon. Two sections: **Appearance** (accent colour picker + density selector,
  apply immediately, persist on save) and **App** (DB path text input, shows
  "restart required" notice when changed). Retire `tweaks-panel.jsx`.
  Update `app.jsx` nav rail to four items (Data, Functions, Builder, Settings).
  *Guarantees:* `PATCH /settings` with a changed `db_path` returns
  `restart_required: true`; appearance changes persist across restarts.

---

### Phase B — Data Ingestion

- [x] **`feat/jit-instance-table`** — §8. `pipeui/sql_user_table/`: fixed module
  with a pure `build_create_table_sql(table_name, columns, primary_key)` DDL
  generator + `instance_table_name(source_id)` helper. No per-source files
  written to disk; no DB connection or registry knowledge in the module.
  DDL uses `CREATE TABLE IF NOT EXISTS` and a table-level `PRIMARY KEY`
  constraint (safe to extend to composite PKs).
  *Boundary guarantee met:* the instance table never references the registry.

- [x] **`feat/ingestion`** — §9. `pipeui/workflow/ingestion.py`: `ingest_source`
  (staged load via DuckDB native readers → JIT table create → upsert / append /
  skip → `date_ingested` update in `source_registry`) + `get_source_detail`
  (live row_count + columns, shaped for drawer and Phase E).
  `ingestion_method` override parameter falls back to stored value.
  *Guarantees met:* ingestion atomicity (append PK collision rolls back; table
  untouched); `skip` returns PK values of dropped rows; JIT create is idempotent
  (`IF NOT EXISTS`). Tests in `tests/test_ingestion.py` (9 tests).

- [x] **`feat/api-sources-ingest`** — §14 (API), §9 (workflow).
  `pipeui/api/sources.py`: `POST /sources/{id}/ingest` (multipart file +
  optional `ingestion_method` override; returns `rows_ingested` + `rows_skipped`
  or a structured failure payload), `GET /sources/{id}` (source detail +
  row_count + columns).
  *Frontend:* `screen-data.jsx` — drawer fetches `GET /sources/{id}` for live
  row_count; "Ingest file" button opens `IngestModal` (file picker + method
  selector); status pill updates after ingest; skip report rendered inline in
  drawer when rows were dropped.

---

### Phase C — Column Type Migration

*Gated by: `column_type` enum decision (CLAUDE.md → Active Deferred Work).*

- [ ] **`feat/column-migration`** — §7. `src/pipeui/workflow/migration.py`:
  `TRY_CAST` pre-check → recreate-and-copy with column cast → atomic swap, all
  in one transaction.
  *Guarantees:* migration atomicity; `TRY_CAST` reports un-castable rows before
  commit (no silent NULL loss). Tests in `tests/test_migration.py`.

- [ ] **`feat/api-sources-migrate`** — §14 (API), §7 (workflow).
  `src/pipeui/api/sources.py`: `PATCH /sources/{id}/columns/{col_id}`.
  Returns un-castable row count before committing so the frontend can prompt.
  *Frontend:* `screen-data.jsx` drawer — schema rows get editable type
  dropdowns; un-castable row count surfaces as a warning modal before the user
  confirms the change.

---

### Phase D — Function Registration

- [ ] **`feat/function-worker`** — §10. Per-call worker process; wall-clock
  timeout; unconditional `setrlimit` CPU/memory caps (Unix-only, no Windows
  guard); Arrow IPC data boundary; per-user venv + lockfile; data-only interface.
  *Guarantees:* worker receives data only; timeout kills a looping function; a
  crashing function takes the worker not the app (surfaced via
  `FailedFunctionEntry`); `setrlimit` memory cap kills an allocate-big function.
  (Integration tests run on Linux CI.)

- [ ] **`feat/function-registration`** — §10 (registration txn), §11. Load a
  `.py`, validate typed params/returns, derive `function_class` /
  `function_type` / `function_return_type`, capture `function_signature`, write
  `function_registry` + `parameter` rows as **one** transaction, collapse on
  `content_hash_id`.
  *Gated by:* return-type vocabulary (CLAUDE.md → Active Deferred Work).
  *Guarantees:* derivation table (§11); collapse preserves surrogate
  `function_id` and overwrites only mutables; registration atomicity.

- [ ] **`feat/api-functions`** — §14 (API), §10–§11 (workflow).
  `src/pipeui/api/functions.py`: `POST /functions`, `GET /functions`,
  `GET /functions/{id}`.
  *Frontend:* `screen-modules.jsx` — `.py` upload, module list, function cards
  with real sig/doc/params; `FailedFunctionEntry` errors surface inline. Replace
  `MODULES` mock data with `fetch()`.

---

### Phase E — Function Attach & Execution *(convergence)*

- [ ] **`feat/function-attach`** — §12. Validate the `alias_map` mapping
  (unmapped parameter/column fails the attach with a message), write
  `source_function_map` + `alias_map` as **one** transaction, keyword binding
  via `param_name`, multi-select loop (N eligible columns → N runs).
  **Convergence point:** needs a registered source (Phase A/B) and a registered
  function (Phase D).
  *Guarantees:* attach atomicity; unmapped param/column fails the attach;
  multi-select runs once per eligible column.

- [ ] **`feat/api-pipelines`** — §14 (API), §12 (workflow).
  `src/pipeui/api/pipelines.py`: `GET /pipelines/{source_id}`,
  `POST /pipelines/{source_id}/steps`,
  `DELETE /pipelines/{source_id}/steps/{step_id}`,
  `POST /pipelines/{source_id}/run`.
  *Frontend:* `screen-builder.jsx` — Reports rail reads real sources; Function
  palette reads real functions; drag-and-drop adds real steps; column mapping
  binds real `alias_map`; Run executes real functions and shows pass/fail per
  step. Replace all remaining mock data in `data.jsx` with `fetch()`.

---

### Phase F — Results & Summary *(deferred)*

- [ ] **Results & Summary layer** — shape depends on the user's data; deferred
  until Phases A–E exist (CLAUDE.md → Active Deferred Work).
- [ ] **v2 scalar persistence** — per-source scalar-override store so UI
  overrides survive across runs (CLAUDE.md → Active Deferred Work).

---

## Dependency summary

```
Phases 0–1:  [done] id-generation, db-schema, test-harness,
                     validation-objects, staging, source-create

Phase A:   api-sources-register  (wires Phase 1 backend to Data screen)
             │
Phase A2:  app-settings  (Settings screen + config file; clears DB_PATH debt)
             │
Phase B:   jit-instance-table ── ingestion ── api-sources-ingest
             │
Phase C:   column-migration ── api-sources-migrate       [gated: column_type enum]
             │
Phase D:   function-worker ── function-registration ── api-functions
             │                                             [gated: return-type vocab]
Phase E:   function-attach ── api-pipelines              [needs Phase B + Phase D]
             │
Phase F:   results & summary (deferred)
```
