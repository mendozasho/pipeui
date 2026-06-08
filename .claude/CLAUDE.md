---
created: 2026-06-06
updated: 2026-06-08
purpose: >
  Project operating document — "what to build and why." Read first for every
  conversation. Design decisions and rationale live here; implementation
  mechanics live in CLAUDE_REFERENCE.md (see the routing table). The canonical
  long-form design intent lives in design.md; this file is the distilled,
  load-bearing version of it.
---

# CLAUDE.md

## Routing table — where to read for detail

`CLAUDE.md` (this file) answers *what to build and why*. `CLAUDE_REFERENCE.md`
answers *how a specific thing is implemented*. When editing a source file, read
the matching reference section first. §13 is fixed as Testing Conventions by the
`use-file` skill.

| Topic | CLAUDE_REFERENCE.md § |
| ----- | --------------------- |
| Registry & relational table schemas | §1 |
| `content_hash_id` (UUID5 + table namespace) and surrogate `id` generation | §2 |
| Validation objects — `*Entry` / `*Update` mechanics, recompute-on-edit | §3 |
| Rejection objects — `FailedRegistryEntry`, `FailedFunctionEntry`, rollback triggers | §4 |
| Cache / staging table mechanics (create-flow metadata vs ingestion rows) | §5 |
| Source initialization flow (backend steps 1–6) | §6 |
| Column-type migration (recreate-and-copy + `TRY_CAST` + atomic swap) | §7 |
| JIT per-source table creation & `sql_user_table` modules | §8 |
| Ingestion atomicity mechanics | §9 |
| Function objects & execution model (process isolation, `setrlimit`, Arrow IPC, venv/lockfile) | §10 |
| Function classification mechanics (`function_class` / `function_type` / `function_return_type` derivation) | §11 |
| `alias_map` binding & multi-select execution | §12 |
| Testing conventions (behavioral-guarantee pattern, mocking strategy) | §13 |
| Frontend & API layer | §14 |
| Package structure | §15 |
| Completed work history | §16 |

Move/rename/fix debt left by the implementation+reorg session is tracked in
**REFACTOR_PLAN.md** (consult it before moving, creating, or renaming functions).

---

## Collaboration rules

1. **Confirm reasoning before code.** Lay out the logic and get explicit sign-off
   before writing a single line of code.
2. **Sectioned code; ask about boundaries.** Break code into individual sections.
   If it is unclear which files or modules a piece is allowed to talk to, ask
   before writing rather than guessing (see Architecture → module boundaries).
3. **design.md is the source of design intent.** Reference it; this file is its
   distilled form. Keep the two consistent.
4. **One branch per unit of work.** Each piece of work happens on its own branch. When the work is complete, open a pull request with `Closes #<issue-number>` in the body so the issue is auto-closed on merge.
5. **Doc split.** New design decisions go in CLAUDE.md; new implementation details
   go in CLAUDE_REFERENCE.md. Split criterion: *what to build and why* → CLAUDE.md,
   *how it is implemented* → CLAUDE_REFERENCE.md.
6. **Cross-check first.** Before proposing any design change or code update, verify
   it aligns with the Key Design Principles below.
7. **Read before editing.** When editing a specific module, read the corresponding
   CLAUDE_REFERENCE.md section for implementation constraints.
8. **Respect declared module boundaries.** Do not introduce a dependency the design
   forbids (e.g. the per-source instance table must not know about the registry;
   user functions must never receive the DB connection).
9. **Guarantees require tests.** Any new behavioral guarantee documented in either
   file must have corresponding tests.
10. **Don't silently resolve open questions.** Items under Active Deferred Work are
    undecided; surface them rather than encoding an answer in code.

---

## Architecture

- **One DuckDB database for everything.** Registry tables, relational map tables,
  and every per-source (JIT) data table live as tables inside a single DuckDB
  file. Cross-source joins (the "join with other reports" feature) are therefore
  direct and need no `ATTACH`.
- **Registry vs instance tables.** The registries (`source_registry`,
  `function_registry`, `column_registry`, `parameter`) describe sources; the
  per-source data ("instance") tables hold the user's actual rows and are built
  JIT from `source_registry` + `column_registry` at ingestion.
- **Repository layout (`src`-style).** The Python package lives under `src/` so
  it is never importable without an install step (prevents accidental shadow
  imports). The frontend is a peer directory at the repo root, not inside the
  package:
  ```
  src/pipeui/     ← installable Python package (backend + API)
  frontend/       ← React app (no build step; CDN React + Babel standalone)
  tests/          ← pytest suite
  pyproject.toml
  ```
- **`api/` lives inside `src/pipeui/`.** FastAPI route modules (`sources.py`,
  `functions.py`, `pipelines.py`) are part of the `pipeui` package, not a
  peer directory. FastAPI serves both the `frontend/` static files and the JSON
  endpoints from a single `uvicorn` process.
- **`frontend/` replaces the CLI.** The application is a browser-based React UI,
  not a command-line tool. §14 documents the frontend/API layer; the old CLI
  placeholder is retired.
- **Module boundaries (load-bearing, do not cross):**
  - `source_registry` knows about its instance table; the instance table must not
    know about the registry.
  - User-uploaded functions receive **data only** (a scalar, `pd.Series`, or
    `pd.DataFrame`) and return data. They never receive the DuckDB connection,
    file paths, or any app object. The backend pulls data out, calls the function,
    and writes results back itself.
  - Relational map rows are written directly (no Python validation object between
    them and the table); the registry rows go through `*Entry` / `*Update` objects.
  - Validation objects (`*Entry` / `*Update`) do **not** read other rows — they
    hold no DB handle and talk only to the create-flow cache + DB-URL config.
    Anything that needs to look at the table (e.g. the collision check in
    Principle 1) lives in the workflow layer, which owns the connection and the
    transaction.
  - `api/` route modules call `workflow/` functions; they do not touch `schema/`,
    `validation/`, or `sql_user_table/` directly. The workflow layer is the sole
    owner of the DuckDB connection and transactions.
  - `frontend/` communicates with the backend exclusively through the `api/`
    HTTP endpoints — it never imports Python modules.

---

## Key Design Principles

### 1. Dual-id identity
Every registry table carries two ids. A **random surrogate `id`** is the true
primary key and the only thing maps and writes reference. A **`content_hash_id`**
(UUID5) is a "by current content" lookup. The `content_hash_id` is **mutable**,
**recomputed whenever a contributing field changes** (via the `*Update` objects),
**unique within its own table**, and **namespaced per table** so identical inputs
in different tables never collide. Because every write and every map reference
uses the surrogate, recomputing a `content_hash_id` never orphans a map row.

*Decided (edit-collision rule):* a mutable + unique hash means an edit can
recompute onto a value that already exists on another row in the same table. On
such a collision the **edit is rejected and surfaced as a failure** — no merge.
The `*Update` object recomputes the hash; the collision **check is enforced at the
write boundary** (the workflow layer owns the connection), because the validation
objects do not read other rows (see Architecture → module boundaries). Mechanics:
CLAUDE_REFERENCE.md §2/§3.

### 2. Function collapse on `content_hash_id`
Re-uploading the "same" function (same `function_name`, `function_class`,
`function_return_type`) collapses **strictly on `content_hash_id`**. On collision
the **existing surrogate `function_id` is preserved** and only the mutable columns
are overwritten. This is what keeps `source_function_map`, `alias_map`, and the
derived `parameter.content_hash_id` values intact across a re-upload. (This is
the function re-upload collapse, distinct from the registry edit-collision in
Principle 1.)

### 3. Transaction boundaries; "rollback" has one meaning
Writes are grouped into atomic sets (`BEGIN` / `COMMIT` / `ROLLBACK`):
- **Source-create** — the `source_registry` row + every `column_registry` row +
  every `source_column_map` row commit as **one** transaction. A source is never
  left half-registered.
- **Function registration** (uploading a `.py`) — the `function_registry` row +
  all its `parameter` rows are one transaction.
- **Function attach** (tying a function to a source) — the `source_function_map`
  row + its `alias_map` rows are a separate transaction.
- **Ingestion** — staged into a temp table, written to the real table only on
  success. On duplicate ids the `ingestion_method` (`upsert` / `append` / `skip`)
  decides the behavior — see CLAUDE_REFERENCE.md §9.

Throughout the app, **"rollback" always means a DuckDB transaction abort that
returns the database to its last committed state.** Reverting to an *earlier*
ingestion (time-travel) is explicitly out of scope — there is no per-ingestion
history.

### 4. Binding model is derived, not stored
Per-parameter classification (`function_class`) is **derived** from `param_type`
plus the `alias_map`, not persisted as a fact about the function:
- `scalar` < `column_backed` < `pd.series` < `pd.dataframe` (highest → lowest
  granularity); anything above `scalar` is `multi_select_eligible`.
- `column_backed` (a `str` param tied to an `alias_map` row) is resolved **at
  attach time** from metadata.
- Arguments are bound **by keyword** via `param_name`; `function_signature`
  (CLAUDE_REFERENCE.md §1) is the canonical captured `param_name: type` signature
  that this binding follows.
- A `scalar` param uses its **Python default**; the user may override it per-run
  in the UI, but in v1 that override is **not persisted** (v2: a per-source scalar
  store — see Active Deferred Work).

### 5. Trust boundary (v1: single trusted local user, Unix-only)
User functions run **process-isolated** with a strict data-in/data-out interface.
This is a stability/accident boundary, not a defense against malicious code. If
the app ever becomes multi-user or hosted, OS-level sandboxing must be added
before running untrusted modules.

**v1 is Unix-only.** The resource limits (`setrlimit` CPU-time and memory caps)
rely on Python's Unix-only `resource` module, so the worker applies them
unconditionally — no Windows branch or graceful-degradation path — and CI runs
on Linux. The wall-clock timeout is the cross-platform safety net that still
bounds runaway workers. Windows support (timeout-only, weaker memory isolation)
is out of scope for v1. *Mechanics (worker model, `setrlimit`, Arrow IPC vs
pickle, per-user venv + lockfile) → CLAUDE_REFERENCE.md §10.*

### 6. Type migration over rejection
When a user changes a column's type, the app **migrates** the already-ingested
data (recreate-and-copy, `TRY_CAST` pre-check, atomic swap, all in a transaction)
rather than rejecting the change or forcing a re-upload. The `column_registry`
(source of truth) and the materialized table stay in sync. *Steps and the reason
in-place `ALTER` is not used → CLAUDE_REFERENCE.md §7.*

---

## Active Deferred Work

Undecided or out-of-scope-for-now. Do not encode an answer in code without a
decision.

- **`column_type` enum** — ~~resolved; see below~~
- **Return-type vocabulary** — reconcile `vector`/`matrix` (function-classification
  prose) with `pd.series`/`pd.dataframe` (`function_class`). Pick one vocabulary.
- **Single-column PK / no uniqueness check (M4)** — design assumes the first column
  is the PK when one can't be determined; there is no validation that the chosen
  PK is unique. Decide whether to enforce.
- **v2 scalar persistence** — a table storing per-source scalar argument overrides
  so they survive across runs.
- **Results & Summary layer** — deferred until the rest of the codebase exists;
  shape depends on the user's data.

*Resolved since the last revision (no longer deferred):*
- **`content_hash_id` edit-collision rule** → **reject** (surface as failure), enforced
  at the write boundary — now Principle 1.
- **`function_signature` field** → **retained and defined**: canonical `param_name: type`
  signature for keyword binding — §1, §12. (It was never deliberately dropped.)
- **`column_type` uninferable fallback** → **`VARCHAR`** (was written as `var`).
- **`column_type` enum** → **`INTEGER`, `BIGINT`, `DOUBLE`, `BOOLEAN`, `VARCHAR`, `DATE`, `TIMESTAMP`**. Derived from the types DuckDB infers from CSV/xlsx uploads (`PYTHON_TO_DUCKDB`). Exotic types (`HUGEINT`, `FLOAT`, `REAL`, `SMALLINT`, `TINYINT`, `TIMESTAMPTZ`) excluded — never inferred at upload, never offered in the migration UI. `VARCHAR` is always a safe widening target. Validated at the app layer as a constrained `VARCHAR`; promotion to DuckDB native `ENUM` deferred until the set was final (now it is).

---

## Frontend & API

The application is a browser-based React UI served by a FastAPI backend. There
is no CLI. Implementation detail (design system, route map, screen-to-endpoint
wiring) lives in CLAUDE_REFERENCE.md §14.

**Four screens** (matching the frontend design):
- **Data** — import files / connect sources, browse registered reports, inspect
  schema and preview rows, edit column types.
- **Functions** — upload `.py` modules, browse registered functions with
  signature / doc / params.
- **Report Builder** — select a report, assemble a pipeline of functions via
  drag-and-drop, map columns to parameters, run, export.
- **Settings** — Appearance (accent colour, density) and App settings (DB path);
  changes persist to `pipeui.config.json`; DB path change shows restart notice.

**Vertical delivery order (Phases A–F in ROADMAP.md).** Each phase ships all
three layers together — backend workflow + API route + frontend feature wired to
that route — so the app is runnable and testable after every phase. `data.jsx`
mock data shrinks one slice per phase as real `fetch()` calls replace it.
