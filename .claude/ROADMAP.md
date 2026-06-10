---
created: 2026-06-06
updated: 2026-06-09
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

- ~~**`column_type` enum**~~ — **resolved**: `INTEGER`, `BIGINT`, `DOUBLE`, `BOOLEAN`, `VARCHAR`, `DATE`, `TIMESTAMP`. No longer gates Phase C.
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

- [x] **`feat/app-settings`** — §14 (API + frontend).
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

### Phase B2 — Data View & Ingest UX Polish

*Builds directly on Phase B. No gating decisions.*

- [x] **`fix/ingest-modal-double-picker`** — `frontend/screen-data.jsx`.
  `e.stopPropagation()` added to both the overlay and inner panel divs. Ingestion
  method selector removed from `IngestModal`; the stored method is used automatically.
  *Guarantees met:* clicking the file picker does not close the drawer; the modal never
  sends an `ingestion_method` override.

- [x] **`feat/source-data-preview`** — §9 (workflow), §14 (API + frontend).
  `pipeui/workflow/ingestion.py`: `get_source_rows(conn, source_id, limit=200) →
  list[dict]` — queries the JIT instance table directly; returns empty list (not error)
  when table does not yet exist.
  `pipeui/api/sources.py`: `GET /sources/{id}/rows?limit=200` — returns
  `{"columns": [...], "rows": [...]}`. 404 if source not found; empty rows if not ingested.
  *Frontend:* `screen-data.jsx` drawer — "Data (up to 200 rows)" section below Columns;
  fetches on open when `date_ingested` is set and after every successful ingest;
  horizontally-scrollable table. Tests in `tests/test_ingestion.py`.
  *Guarantees met:* empty list before ingestion; correct rows after; limit cap respected.

---

### Phase C — Column Type Migration

*Gating decision resolved: `column_type` enum = `INTEGER`, `BIGINT`, `DOUBLE`, `BOOLEAN`, `VARCHAR`, `DATE`, `TIMESTAMP`.*

- [x] **`feat/column-migration`** — §7. `pipeui/workflow/migration.py`:
  `migrate_column(conn, source_id, column_id, new_type, scope, on_uncastable, dry_run)`.
  `TRY_CAST` pre-check → copy-on-write `column_registry` update → recreate-and-copy
  with column cast → atomic swap, all in one transaction. `dry_run=True` mode returns
  counts and shared-source list without mutating. `scope="all_shared"` migrates every
  source sharing the same `column_registry` UUID5 row in one transaction.
  `on_uncastable="nullify"` proceeds and returns `nullified: [{pk, column}]`;
  `on_uncastable="abort"` rolls back on any un-castable rows. Copy-on-write reuses an
  existing `(column_name, new_type)` row when one is already in `column_registry`.
  `content_hash_id` edit-collision enforced at the write boundary (Principle 1).
  Tests in `tests/test_migration.py` (13 tests).
  *Guarantees met:* migration atomicity; no silent NULL loss; shared-row isolation;
  collision detection; rollback on failure.

- [x] **`feat/api-sources-migrate`** — §14 (API), §7 (workflow).
  `pipeui/api/sources.py`: `PATCH /sources/{id}/columns/{col_id}?dry_run=false`.
  Body: `{"column_type", "scope": "this_source"|"all_shared", "on_uncastable": "nullify"|"abort"}`.
  Dry-run returns `{"castable", "uncastable", "shared_sources"}`. Commit returns
  `{"ok", "rows_migrated", "nullified"}`. 404 on unknown source/column; structured
  failure (not 500) on invalid type or aborted migration. Tests in `tests/test_api_migration.py`.
  *Frontend:* `screen-data.jsx` — `ColumnTypeRow` component replaces static type badge
  with a 7-option `<select>`. Happy path (zero un-castable, no shared sources) commits
  directly. Non-happy path shows `MigrationConfirmModal` with un-castable count,
  shared-source names, and scope radio buttons. After commit: drawer columns + data
  preview both refresh. Nullified rows surface in a "Nullified values" section in the
  drawer (ephemeral — resets on drawer close).

---

### Phase D — Function Registration *(complete)*

- [x] **`feat/phase-d-functions-paths-setting`** (#23) — `function_signature` (NOT NULL) + `is_active` columns added to `function_registry` DDL; `functions_paths: list[str]` added to `AppSettings`; Settings screen "Functions" subsection for add/remove path list.

- [x] **`feat/phase-d-worker`** (#24) — Per-call worker subprocess; wall-clock timeout; unconditional `setrlimit` CPU/memory caps (Unix-only); Arrow IPC data boundary; `FailedFunctionEntry` on timeout/crash/OOM.

- [x] **`feat/phase-d-scan-and-list`** (#25) — Scan workflow: discovers eligible functions in `functions_paths`, derives `function_class`/`function_type`/`function_return_type`/`param_type`, writes `function_registry` + `parameter` rows in one transaction per function, collapses on `content_hash_id` (Principle 2). `POST /functions/scan` + `GET /functions`. Functions screen replaces `MODULES` mock; "Rescan" button + collapsible scan log.

- [x] **`feat/phase-d-inactive-functions`** (#26) — Rescan flips `is_active = false` for missing files; restores on reappearance; scan log includes `file_missing` entries; inactive cards show muted style + "Unavailable" badge.

- [x] **`feat/phase-d-function-detail-drawer`** (#27) — `GET /functions/{id}` with `attached_sources` join; detail drawer on Functions screen (signature, doc, params, attached sources).

---

### Phase D2 — Function Sets *(complete)*

*Builds on Phase D. Adds the Sets tab to the Functions screen.*

- [x] **`feat/phase-d2-function-sets`** — Schema: `function_set` (`set_id` UUID4, `set_name` VARCHAR NOT NULL, `set_description` VARCHAR, `content_hash_id` UUID5) + `function_set_map` (`set_map_id` UUID4, `set_id`, `function_id`, `position` INTEGER). Dual-id identity (Principle 1) on `function_set`. API: `GET /function-sets`, `POST /function-sets`, `GET /function-sets/{id}`, `PATCH /function-sets/{id}`, `DELETE /function-sets/{id}`. Frontend: Functions screen gains a **Sets tab** alongside the Functions tab. Two-panel create/edit layout — left panel: filterable registered function list; right panel: ordered pipeline for this set (drag to reorder, click to remove). Set card shows name, description, function count, warning marker when any member function has `is_active = false`. Replace `FUNCTION_SETS` mock in `data.jsx` with real `fetch()`.
  *Guarantees:* set creation atomicity (set row + all set_map rows in one transaction); position ordering is preserved on read; deleting a set does not delete member functions.

---

### Phase E1 — Function Attach *(complete)*

*Needs a registered source (Phase B) and registered functions (Phase D).*

- [x] **`feat/phase-e1-function-attach`** — §12. Attach individual functions or all functions in a set to a source: writes `source_function_map` + `alias_map` rows in one transaction per function. Validates that all non-scalar parameters have an alias_map binding — unmapped required param fails the attach with a message. Auto-suggests bindings when the new source shares a `column_id` with an existing binding on another source (same `column_name + column_type` → same `column_id`). Keyword binding via `param_name`. Multi-select: when a function's `function_class` is `column_backed`/`pd.Series`/`pd.dataframe`, it can be run once per eligible mapped column.
  API: `POST /sources/{source_id}/attach` (body: `{function_id}` or `{set_id}`), `DELETE /sources/{source_id}/attach/{function_id}`, `GET /sources/{source_id}/pipeline` (returns ordered attached functions with bindings).
  *Frontend:* attach UI lives in the Builder screen — source selector + function/set palette on the left, pipeline steps on the right; column binding dropdowns per parameter; auto-fill highlighted; save writes the attach.
  *Guarantees:* attach atomicity; unmapped required param fails; multi-select runs once per eligible column; detach removes `source_function_map` + all `alias_map` rows in one transaction.

---

### Phase E2 — Builder Execution *(thin v1)*

*Needs Phase E1 (attach) to be complete.*

- [x] **`feat/phase-e2-pipeline-run`** — Execute the pipeline for a source: call each attached function in `position` order via the Phase D worker, collect results. Validation functions (`function_type = validation`) produce pass/fail rows; transform functions write results back to the instance table (or to a session-only staging table). API: `POST /pipelines/{source_id}/run` → returns `{steps: [{function_name, status, rows_passed, rows_failed, error}]}`.
  *Frontend:* Builder screen side panel gains **Run Validations** and **Run Transforms** buttons; per-set run icon on each pipeline card; per-set result tags (`success` / `issues` / `error`) applied after run; clicking a result tag navigates to the Results screen. **Results** nav item added as the fourth nav item, replacing the former separate Validations/Staging placeholders — routes to a placeholder screen ("Run a pipeline to see results here").
  *Guarantees:* a crashing function step surfaces as a failed step (not a 500); subsequent steps still run; pipeline result is returned even if some steps fail.

---

### Phase F1 — Validations Screen *(complete)*

- [x] **Validations screen** (#87, #88, #89) — Full implementation of the Validations screen within the Results nav item. Two sub-tabs: **By Source** (pick a source, run validations, see per-function pass/fail counts + expandable failing rows preview, export per-function CSV) and **By Function** (pick a validation function, run across all attached sources via `POST /validations/run?function_id={id}`, per-source results + export). Results are ephemeral React state (session-only); export is the durable artifact. Builder result-tag click deep-links to By Source pre-scoped to the relevant source.

---

### Phase F2 — Unified Results Screen with Cards *(complete)*

- [x] **Unified Results screen** (#94–#97) — Replaced the F1 dropdown+run-button model with a flat card grid (most-recent-first). Every run — validation or transform, triggered from Data, Functions, or Builder — appends a result card. Cards are tagged `validation` or `transform`, show a summary on the face, and expand to reveal per-function or per-source detail. Cards are selectable for mass export; each card also has an inline Export button. Export formats: CSV and xlsx via SheetJS. Run triggers moved to Data page (per-source Run button) and Functions page (per-function and per-set Run buttons). Builder run buttons removed; result tags preserved and deep-link to Results. New backend: `run_type=all` on `POST /pipelines/{id}/run`; `GET /pipelines/{id}/staging`; `POST /pipelines/run-set?set_id={id}`. Cards are session-only (lost on refresh). `run_id` (UUID4) assigned frontend-side as card identity.
  *Deferred to v2:* persistent staging tables; cross-source join UI.

---

### Phase F3 — Deferred

- [ ] **v2 scalar persistence** — per-source scalar-override store so UI overrides survive across runs (CLAUDE.md → Active Deferred Work).

---

## Dependency summary

```
Phases 0–1:  [done] id-generation, db-schema, test-harness,
                     validation-objects, staging, source-create

Phase A:   api-sources-register                                          [done]
             │
Phase A2:  app-settings                                                  [done]
             │
Phase B:   jit-instance-table ── ingestion ── api-sources-ingest        [done]
             │
Phase B2:  fix/ingest-modal  feat/source-data-preview                   [done]
             │
Phase C:   column-migration ── api-sources-migrate                      [done]
             │
Phase D:   functions-paths-setting ── worker ── scan-and-list           [done]
             └── inactive-functions ── function-detail-drawer           [done]
             │
Phase D2:  function-sets  (Sets tab on Functions screen)
             │
Phase E1:  function-attach  (needs Phase B + Phase D + Phase D2)
             │
Phase E2:  pipeline-run  (thin: run + placeholder Validations/Staging nav)
             │
Phase F1:  validations-screen  (full pass/fail UI + export)
Phase F2:  staging-screen      (full post-run table view + export)
Phase F3:  v2 scalar persistence  (deferred)
```
