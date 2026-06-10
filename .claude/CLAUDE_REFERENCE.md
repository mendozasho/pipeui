---
created: 2026-06-06
updated: 2026-06-07
purpose: >
  Project reference document — "how a specific thing is implemented." The
  companion to CLAUDE.md, which answers "what to build and why." Read the
  relevant section here before editing the matching source module; read
  CLAUDE.md first for design intent, principles, and Active Deferred Work.
  Section numbers are fixed by the routing table in CLAUDE.md and must stay
  in lockstep with it. The canonical long-form design intent is design.md;
  this file is the implementation-level companion to it, not a replacement.
---

# CLAUDE_REFERENCE.md

## How to read this file

Every section number below corresponds 1:1 to the routing table at the top of
CLAUDE.md. When you edit a source module, read the matching section here for the
implementation constraints, then check the cross-referenced CLAUDE.md principle
for *why* the constraint exists. This file documents mechanics only; whenever a
"what/why" question comes up it is answered in CLAUDE.md or design.md and
cross-linked, not re-argued here.

**Doc-split reminder (CLAUDE.md rule 5):** new *implementation detail* lands
here; new *design decisions* land in CLAUDE.md. If editing this file surfaces a
genuine design choice, stop and record it in CLAUDE.md first.

**Deferred items are not resolved here.** Anything under CLAUDE.md → Active
Deferred Work is referenced as open, never silently decided (CLAUDE.md rule 10).
Such points are tagged **[DEFERRED]** inline below.

**Decisions pinned so far** (all consistent with design.md): surrogate ids are
`uuid4`; `content_hash_id` uses two-level `uuid5`; `*Entry`/`*Update` objects are
pydantic v2; the user-function worker boundary uses Arrow IPC; v1 is Unix-only.
Pinned in the docs-sync session: ingestion methods are `upsert`/`append`/`skip`;
the uninferable `column_type` fallback is `VARCHAR`; the `content_hash_id`
edit-collision rule is **reject** (enforced at the write boundary);
`function_signature` is retained and defined. Each is documented in the relevant
section below. Code debt left by the implementation+reorg session is tracked in
REFACTOR_PLAN.md.

---

## §1 — Registry & relational table schemas

*Implements: CLAUDE.md → Architecture (registry vs instance tables) and
Principle 1 (dual-id identity). Design intent: design.md → Tables.*

All registry tables, all relational map tables, and every per-source (JIT)
instance table live as tables **inside one DuckDB database file**. There is no
`ATTACH`; cross-source joins are direct (see §8 for instance tables).

**Identity columns (every registry table).** Two id columns:
- the **surrogate `*_id`** (`source_id`, `function_id`, `column_id`,
  `param_id`) — DuckDB native `UUID`, `uuid4`, **primary key**, NOT NULL. This
  is the only value that maps and writes reference (§2).
- the **`content_hash_id`** — DuckDB native `UUID`, `uuid5`, mutable,
  recomputed on edit (§2, §3), unique *within its own table*.

**Enum storage.** Defined enums (`ingestion_method` ∈ {`upsert`, `append`,
`skip`}, and the function enums in §11) are stored as constrained `VARCHAR`,
validated at the app layer by the pydantic objects (§3), in v1. Promotion to
DuckDB native `ENUM` is possible once each vocabulary is final.
`column_registry.column_type` set is now pinned: `INTEGER`, `BIGINT`, `DOUBLE`,
`BOOLEAN`, `VARCHAR`, `DATE`, `TIMESTAMP` — promotion to DuckDB `ENUM` is
deferred to a future cleanup pass but is no longer blocked.

### Registry tables (concrete schema)

`source_registry`

| column | DuckDB type | null | notes |
| --- | --- | --- | --- |
| `source_id` | UUID | no | PK, surrogate (uuid4) |
| `content_hash_id` | UUID | no | uuid5 of (`source_name`, `primary_key`, `ingestion_method`); unique in table |
| `source_name` | VARCHAR | no | mutable |
| `date_ingested` | TIMESTAMP | yes | last-ingested timestamp (no per-ingestion history — §9) |
| `date_registered` | DATE | no | immutable, set once at create |
| `ingestion_method` | VARCHAR(enum) | no | `upsert` \| `append` \| `skip` (semantics in §9) |
| `pattern` | VARCHAR | yes | regex/naming convention inferred from filename (§6.1) |
| `primary_key` | VARCHAR | no | PK column of the instance table; first column assumed if undeterminable — no uniqueness check **[DEFERRED]** |
| `table_url` | VARCHAR | yes until create completes | resolves to the single DB file; instance table identified by name within it |

`function_registry`

| column | DuckDB type | null | notes |
| --- | --- | --- | --- |
| `function_id` | UUID | no | PK, surrogate (uuid4); preserved across re-upload collapse (§11, Principle 2) |
| `content_hash_id` | UUID | no | uuid5 of (`function_name`, `function_class`, `function_return_type`) |
| `function_class` | VARCHAR(enum) | no | derived, not stored as a fact about the fn — see §11 |
| `function_name` | VARCHAR | no | from `__name__` |
| `function_doc` | VARCHAR | yes | from docstring; tooltip source |
| `function_return_type` | VARCHAR(enum) | no | see §11 vocabulary note |
| `function_type` | VARCHAR(enum) | no | `validation` \| `transform`, derived (§11) |
| `module_path` | VARCHAR | no | path used to load the actual fn |
| `function_signature` | VARCHAR | no | canonical `param_name: type` signature (the `inspect.signature` form, including the return annotation) captured at registration. Its purpose is to make argument binding easy: attach/run binds arguments **by keyword** to these parameters (§12). The `parameter` rows are the queryable per-parameter decomposition; this column is the canonical signature string. (Must be added back to the DDL — see REFACTOR_PLAN.md.) |

`column_registry`

| column | DuckDB type | null | notes |
| --- | --- | --- | --- |
| `column_id` | UUID | no | PK, surrogate (uuid4) |
| `content_hash_id` | UUID | no | uuid5 of (`column_name`, `column_type`) |
| `column_name` | VARCHAR | no | taken verbatim from the spreadsheet |
| `column_type` | VARCHAR(enum) | no | inferred at read; falls back to `VARCHAR` when uninferable (§6.1) — allowed set: `INTEGER`, `BIGINT`, `DOUBLE`, `BOOLEAN`, `VARCHAR`, `DATE`, `TIMESTAMP` (resolved, CLAUDE.md). Validated at app layer; migration enforced in `workflow/migration.py` (§7). |

`parameter`

| column | DuckDB type | null | notes |
| --- | --- | --- | --- |
| `param_id` | UUID | no | PK, surrogate (uuid4) |
| `content_hash_id` | UUID | no | uuid5 of (`param_name`, `function_id`, `param_type`) — ties the param to a specific function |
| `param_name` | VARCHAR | no | name in the function definition; used for keyword binding (§12) |
| `param_type` | VARCHAR(enum) | no | user-typed in the module |
| `function_id` | UUID | no | FK → `function_registry.function_id` (surrogate) |

### Relational map tables

Map rows are written **directly** with a plain SQL/DuckDB insert — no pydantic
object between them and the table (module boundary in CLAUDE.md → Architecture).
Each map's own id is a `uuid5` composed from the two surrogate FKs it joins, so
the same pair never produces two rows.

- `source_column_map(source_column_map_id, column_id, source_id)` — columns of a
  source / sources using a column.
- `source_function_map(source_function_map_id, source_id, function_id)` —
  functions attached to a source / sources affected by a function edit.
- `alias_map(alias_map_id, column_id, parameter_id, source_id)` — see §12; the
  binding table that powers multi-column runs.

---

## §2 — `content_hash_id` (UUID5 + table namespace) and surrogate id generation

*Implements: CLAUDE.md → Principle 1. Design intent: design.md → "A note on
tables".*

**Surrogate `id` (`uuid4`).** Generated behind a **single injectable function**
(one module-level factory, `new_id()`), so it can be patched in one place for
deterministic tests (§13). It is the true PK and the only value referenced by
maps and writes; it is **never** recomputed or changed by an edit. The factory
lives in `pipeui/ids.py` (foundational module — see §15; relocation from
`pipeui/validation/ids.py` is tracked in REFACTOR_PLAN.md).

**`content_hash_id` (two-level `uuid5`).** Computed in two steps so the per-table
namespacing in Principle 1 is structural rather than convention:
1. A fixed **app-root namespace** constant (one `uuid5`/`UUID` literal defined
   once for the project).
2. A **per-table namespace** = `uuid5(app_root, table_name)`.
3. `content_hash_id` = `uuid5(table_namespace, canonical_input)`.

`canonical_input` is the table's contributing fields joined in a **fixed order
with a reserved separator**. Field order is the order the fields appear in the
schema (§1), so the serialization is stable and reproducible. Contributing
fields per table are exactly those listed in the §1 `content_hash_id` rows.

Because different tables use different namespaces, identical field values in
different tables never collide (Principle 1). Recompute happens only through the
`*Update` objects (§3); since maps reference the surrogate, recompute never
orphans a map row.

**Edit-collision rule (decided: reject).** A recompute can land on a
`content_hash_id` already present on another row in the same table. On collision
the edit is **rejected and surfaced as a failure** (no merge). Because detecting
it requires reading other rows — which the `*Update` objects do **not** do (§3,
and CLAUDE.md → Architecture) — the check lives at the **write/transaction
boundary** (the workflow layer that owns the connection): recompute the hash in
`*Update`, then, before/within the UPDATE, look for an existing row carrying the
new `content_hash_id` on a *different* surrogate id; on a hit, route to
`FailedRegistryEntry` (§4) and roll back. Implements CLAUDE.md → Principle 1.
(Wiring this into real code — currently simulated inline in a test — is tracked
in REFACTOR_PLAN.md.)

---

## §3 — Validation objects: `*Entry` / `*Update` mechanics, recompute-on-edit

*Implements: CLAUDE.md → Principle 1 and Architecture (registry rows go through
`*Entry`/`*Update`; map rows do not). Design intent: design.md → Validation
Objects.*

**Library: pydantic v2.** Chosen over dataclasses because the entire role of
these objects is field validation with structured error messages that feed the
rejection objects (§4), and because `*Update` needs all-optional fields with
partial-update semantics. Dependency cost is negligible.

**`*Entry` (`SourceRegistryEntry`, `ColumnRegistryEntry`, …).** Full mirror of
the table's fields; validates every field. Methods:
- `content_hash_id` generation following the §2 logic for that table.
- (`SourceRegistryEntry` only) `table_url` generation; resolves to the single DB
  file and updates the object in place.
- On any validation failure the whole object is handed to the matching rejection
  object (§4) with the error message; it does not write.
- Communicates only with the create-flow cache (§5) and a config holding the DB
  URL — not with other app objects, and **never reads other table rows**.

**`*Update` (`SourceRegistryUpdate`, `ColumnRegistryUpdate`, …).** Same fields,
all optional. Only fields the user actually touched are populated; untouched
fields are not re-validated. **Recompute-on-edit:** if an update touches any
field that feeds the `content_hash_id` (per §1/§2), the object recomputes
`content_hash_id`. The surrogate id is never changed by an update. All update
requests flow through these objects, never through `*Entry`.

**Collision check lives at the write boundary, not in the model.** After the
`*Update` recomputes the hash, the workflow layer checks the target table for an
existing row carrying that `content_hash_id` on a *different* surrogate id; a hit
is rejected via `FailedRegistryEntry` (§4) and the transaction rolls back (no
merge). The check is *not* a pydantic validator because the model holds no DB
handle and does not read other rows (boundary above) — see §2.

---

## §4 — Rejection objects: `FailedRegistryEntry`, `FailedFunctionEntry`, rollback triggers

*Implements: CLAUDE.md → Principle 3 (rollback semantics) and Principle 1
(edit-collision → reject). Design intent: design.md → Rejection Objects.*

Both are **stack** objects accumulating failures to return to the UI.

`FailedRegistryEntry` — registry tables only (tables with `registry` in the
name). Stores the attempted `*Entry` object (which both carries the attempted
values and identifies the target table) plus the error message / failure reason.
Also the sink for an edit-collision rejection (§2/§3).

`FailedFunctionEntry` — function rejections (missing return, untyped parameter,
etc.). Stores the function and its breakdown plus the error message and
suggested remediation.

**Rollback trigger.** A rejection object requests rollback of **the entire
transaction set it belongs to**, never a single write (this is the one meaning
of "rollback" — a DuckDB transaction abort to last committed state; see §9 and
Principle 3):
- `FailedRegistryEntry` in source-create → unwinds the `source_registry` row +
  every `column_registry` row + every `source_column_map` row (§6).
- `FailedFunctionEntry` → unwinds whichever function set it belongs to:
  registration (`function_registry` + `parameter` rows) or attach
  (`source_function_map` + `alias_map` rows) (§10, §11).

---

## §5 — Cache / staging table mechanics

*Implements: CLAUDE.md → Principle 3 (staged-then-committed). Design intent:
design.md → Initializing a new source, backend step 1.4 and step 10.*

A **transient DuckDB staging table** (DuckDB built-in temp/staging
functionality) backs two distinct uses of the *same mechanism*:
- **Create-flow cache** — holds *registration metadata*: read column names,
  user-confirmed `column_type`s, and the PK choice. The user's final
  confirmation is the source of truth; edits the user makes update the cache
  before values are pulled into `*Entry` objects (§3). Implemented as
  `CreateFlowCache` in `pipeui/workflow/staging.py`.
- **Ingestion staging** — holds *actual rows* during a load (§9).

Both stage writes during an operation and abort the transaction on any error in
the set, returning the DB to its last working state. The two uses never mix
contents; only the mechanism is shared.

---

## §6 — Source initialization flow (backend steps 1–6)

*Implements: CLAUDE.md → Architecture and Principle 3 (source-create is one
transaction). Design intent: design.md → Initializing a new source → Backend
Perspective.* Implemented in `pipeui/workflow/create.py::create_source`.

1. **Read the uploads.** Per file: (1.1) infer a regex `pattern` from the
   filename; (1.2) add any unknown columns to `column_registry`; (1.3) infer
   `column_type` from sample data using DuckDB-native inference, falling back to
   `VARCHAR` when inference errors or has insufficient data (optionally
   cross-check `column_registry` for a known type); (1.4) stage everything in the
   create-flow cache (§5) and request the PK column from the user; (1.5) get
   `date_ingested` from `st_mtime` (or faster); (1.6) everything is now known
   except `table_url` (optional until step 2).
2. **Build `SourceRegistryEntry`** from the cache (column data is not in
   `source_registry`, so it is not pulled in here). Validate; generate
   `table_url` and `content_hash_id` (§2, §3).
3.–5. **Write as ONE transaction** (`BEGIN`/`COMMIT`/`ROLLBACK`): the
   `source_registry` row (3) + a `column_registry` row per column via
   `ColumnRegistryEntry` (4) + a `source_column_map` row per column written
   directly with the new `uuid5` map id (5). Any failure → no rows written, and
   the relevant `*Entry` goes to `FailedRegistryEntry` (§4). A source is never
   left half-registered.
6. The source is now filterable by `source_id` and joinable to `column_registry`
   for column detail. Subsequent user edits funnel through `*Update` objects (§3).

**[DEFERRED]** — no validation that the chosen/assumed PK is unique (CLAUDE.md →
Active Deferred Work: single-column PK / no uniqueness check).

**Known gap (REFACTOR_PLAN.md):** an invalid `ingestion_method` currently raises
uncaught in `create_source` (the `IngestionMethod.accepted()` gate) rather than
routing to `FailedRegistryEntry`; and the enum (`upsert`/`append`) and the
`SourceRegistryEntry` validator (`upsert`/`skip`) disagree, so only `upsert`
works today. Both need unifying to the full `upsert`/`append`/`skip` set with the
invalid path routed to the rejection stack.

---

## §7 — Column-type migration (recreate-and-copy + `TRY_CAST` + atomic swap)

*Implements: CLAUDE.md → Principle 6 (migrate over reject). Design intent:
design.md → Initializing a new source, step 7. Implemented in
`pipeui/workflow/migration.py`.*

When a user changes a column's type, the already-ingested data is migrated,
keeping `column_registry` (source of truth) and the materialized instance table
in sync.

**Allowed `column_type` set** (resolved from Active Deferred Work):
`INTEGER`, `BIGINT`, `DOUBLE`, `BOOLEAN`, `VARCHAR`, `DATE`, `TIMESTAMP`.
Validated at the app layer as a constrained `VARCHAR`.

**Entry point:** `migrate_column(conn, source_id, column_id, new_type,
scope="this_source", on_uncastable="abort", dry_run=False) → dict`

Steps, entirely inside one transaction:
1. **Validate `new_type`** — reject immediately if outside the allowed set.
2. **TRY_CAST pre-check** — count rows that cannot be cast; collect their PKs
   when `on_uncastable="nullify"`. No silent loss to NULL.
3. **Shared-row detection** — query `source_column_map` for all sources sharing
   the same `column_registry` UUID5 row. Return as `shared_sources`.
4. **Dry-run mode** (`dry_run=True`) — runs steps 1–3, rolls back, returns
   `{"castable", "uncastable", "shared_sources"}`. DB is unchanged.
5. **`on_uncastable="abort"`** — if pre-check finds any un-castable rows, return
   structured failure before opening a transaction. DB unchanged.
6. **`column_registry` update (copy-on-write, `scope="this_source"`)** — look up
   whether a row for `(column_name, new_type)` already exists by UUID5; if yes,
   re-point `source_column_map` to it; if no, insert a new row. The old shared
   row is left intact for other sources.
7. **`column_registry` update (`scope="all_shared"`)** — update the single shared
   row in place; migrate all sharing sources' instance tables in the same
   transaction.
8. **`content_hash_id` collision check** (Principle 1) — if `scope="all_shared"`
   and the new hash would land on a *different* existing row, return structured
   failure before any mutation.
9. **Recreate-and-copy** — for each affected instance table: `CREATE TABLE`
   (strict, not IF NOT EXISTS) with updated column type; `INSERT … SELECT` with
   `TRY_CAST` on the changed column; `DROP` old table; `RENAME` new table.
10. **Commit** — all the above in one `BEGIN`/`COMMIT`. Any exception triggers
    `ROLLBACK`, leaving the DB at its last committed state.

Any failure returns `{"ok": False, "error": "...", "reason": "..."}` — never raises.
Success returns `{"ok": True, "rows_migrated": N, "nullified": [{"pk", "column"}]}`.

In-place `ALTER … ALTER COLUMN … TYPE` is **not** used: DuckDB's in-place change
fails if conflicting-type values ever existed (even if since deleted) and cannot
alter an indexed column (which includes the PK). A fresh table carries no such
history or dependency.

**API:** `PATCH /sources/{id}/columns/{col_id}?dry_run=false` in
`pipeui/api/sources.py`. Body: `{"column_type", "scope", "on_uncastable"}`.
Dry-run ignores `scope`/`on_uncastable`. Returns 404 on unknown source/column;
structured failure (not 500) on invalid type or aborted migration.

**Frontend:** `ColumnTypeRow` component in `screen-data.jsx` replaces the static
type badge with a `<select>`. On change: fires dry-run first. If zero un-castable
and no shared sources, commits immediately. Otherwise shows `MigrationConfirmModal`
(un-castable count + shared source names + scope selector). After commit: refreshes
drawer columns and data preview. Nullified rows surface in an ephemeral "Nullified
values" section (resets on drawer close).

---

## §8 — JIT per-source table creation & `sql_user_table` modules

*Implements: CLAUDE.md → Architecture (instance tables built JIT; instance table
must not know about the registry). Design intent: design.md steps 8–9.*

When the user first ingests rows, the backend builds a **per-source instance
table JIT** from `source_registry` + `column_registry` (column names + confirmed
types + PK). This is an end-user-dependent table: its schema and contents come
from the upload, not a fixed schema. Once built, files tied to the source are
written directly via SQL (§9 governs atomicity).

`sql_user_table/` is a **fixed module containing a pure DDL generator function**
— no per-source files are written to disk. The generator takes plain data and
returns a SQL string; it holds no DB connection and knows nothing about the
registry.

```python
def build_create_table_sql(
    table_name: str,
    columns: list[tuple[str, str]],  # (column_name, column_type)
    primary_key: str,
) -> str:
```

DDL uses `CREATE TABLE IF NOT EXISTS` (defensive — safe to call on re-ingest) and
a table-level `PRIMARY KEY (col)` constraint (straightforward to extend to
composite PKs in a future phase without changing the generator's interface).

The workflow layer (ingestion) is responsible for reading the registry and
assembling the arguments; the generator never sees the registry or the connection.

**Boundary:** the instance table has no reference back to the registry; the
registry knows about the instance table, not vice versa.

---

## §9 — Ingestion atomicity mechanics

*Implements: CLAUDE.md → Principle 3 ("rollback" has one meaning). Design intent:
design.md step 10.*

Each upload is first loaded into a **temporary table** (§5 ingestion staging) via
DuckDB's native file readers (`read_csv_auto` / `read_xlsx`) and written into the
source's real instance table only if the load completes. Any failure aborts via
DuckDB transaction rollback (`BEGIN`/`COMMIT`/`ROLLBACK`), leaving the existing
table at its last committed state. Schema changes (table create/alter) are
transactional in DuckDB and fall under the same all-or-nothing guarantee.

On duplicate ids during ingestion, `ingestion_method` decides:
- **`upsert`** — `INSERT OR REPLACE`: update the existing row, or insert if new.
- **`append`** — straight `INSERT`; a PK collision raises a constraint violation
  and the whole transaction rolls back (the failure is surfaced via
  `FailedRegistryEntry`, not a 500).
- **`skip`** — `INSERT ... ON CONFLICT DO NOTHING`; the PK values of the skipped
  rows are collected **before** the insert and returned to the caller so dropped
  rows are visible (behavioral guarantee — rule 9; tested in `test_ingestion.py`).

On success, `source_registry.date_ingested` is updated inside the same transaction
so the list view and status pill reflect live state immediately.

`ingestion_method` is stored in `source_registry` as the default for that source.
The `ingest_source` workflow function accepts an optional override so the caller
(API layer) can pass a per-call value; it falls back to the stored method when
none is given.

Ingested rows are retained for summaries and deliverables.

**"Rollback" everywhere in this app = a DuckDB transaction abort to the last
committed state.** Reverting to an *earlier* ingestion (time-travel) is out of
scope — there is no per-ingestion history.

**Row preview (`get_source_rows`, Phase B2 — implemented).** A read-only helper
in `pipeui/workflow/ingestion.py` that queries the JIT instance table directly
and returns up to `limit` rows as plain dicts. Returns an empty list when the
table does not yet exist (source registered but not ingested). No transaction
needed — read-only. Exposed as `GET /sources/{id}/rows?limit=200`; returns
`{"columns": [...], "rows": [...]}`. The drawer "Data" section fetches on open
and after every ingest. Tests in `tests/test_ingestion.py`.
Future expansion: `?search=`, `?col=`, `?min=`, `?max=` query params and a
health-check endpoint (null counts, type distribution, duplicate PK check) can
be added without architectural changes.

---

## §10 — Function objects & execution model

*Implements: CLAUDE.md → Principle 5 (trust boundary) and Principle 3 (function
write transactions). Design intent: design.md → Function Objects.*

**Trust boundary (v1: single trusted local user).** Process isolation is a
**stability/accident** boundary, not a defense against malicious code. If the app
ever becomes multi-user/hosted, OS-level sandboxing must be added first.

**Data-only interface.** User functions receive **only data** — a scalar,
`pd.Series`, or `pd.DataFrame` — and return data. They never receive the DuckDB
connection, file paths, or any app object, so user code structurally cannot
touch the database, registries, or other sources. The backend pulls the
column/table out of DuckDB, calls the function, and writes the result back
itself.

**Isolation mechanics:**
- **Per-call worker process** — each function call runs in its own process; a
  crash, hang, or memory blowup takes the worker, not the app.
- **Wall-clock timeout** — the backend kills the worker after a wall-clock bound
  (cross-platform; the always-available safety net).
- **`resource.setrlimit`** — CPU-time and memory (address-space) caps. v1 is
  **Unix-only**, so the worker imports `resource` and applies the limits
  **unconditionally** — no Windows guard or graceful-degradation path to
  maintain. (Platform posture is recorded in CLAUDE.md; the mechanics live here.)
- **Per-user venv + lockfile** — user modules run in their own environment
  (e.g. a `uv`/`pip` env with a lockfile), separate from the app's, so user
  dependencies cannot shadow the app's. pandas is available by default.

**Data boundary transport: Arrow IPC.** Data crosses the process boundary as
Arrow IPC rather than pickle (deserialization is itself an execution vector).
Arrow is in-memory (no disk hop) for the transient per-call handoff, and DuckDB
has native Arrow support, so column-out-of-DuckDB → worker → result-back needs no
extra serialization step. Parquet remains a possible later spill-to-disk
fallback for very large frames — not v1.

**Function write transactions (Principle 3).** Two separate atomic units, since
they happen at different times:
- **Registration** (uploading a `.py`): `function_registry` row + all that
  function's `parameter` rows = one transaction.
- **Attach** (tying a function to a source): `source_function_map` row + its
  `alias_map` rows = a separate transaction.
- Either set is all-or-nothing; failure routes to `FailedFunctionEntry` (§4).

---

## §11 — Function classification mechanics

*Implements: CLAUDE.md → Principle 4 (binding derived, not stored) and Principle
2 (collapse on `content_hash_id`). Design intent: design.md → Backend Perspective
– function classification.*

Classification is **derived** from the parameter signature + the `alias_map`, not
persisted as an intrinsic fact about the function.

**`function_class`** — the least-granular (most generic) parameter drives it.
Granularity high → low:
1. `scalar` — a single-value primitive (`int`, `float`, `bool`), and `str` when
   **not** tied to an `alias_map` row. Uses its Python default; the UI may
   override per-run, but in v1 the override is **not persisted** (CLAUDE.md →
   Active Deferred Work: v2 scalar persistence).
2. `column_backed` — a `str` parameter tied to an `alias_map` row; resolved **at
   attach time** from metadata.
3. `pd.series`.
4. `pd.dataframe`.

Anything above `scalar` is **`multi_select_eligible`**: a parameter can take a
stack of eligible column arguments and the function runs once per argument
(e.g. 3 eligible columns → 3 runs → 3 results aggregated in the summary).

**`function_type`** — derived from the return: a `boolean` / `pd.Series[bool]`
return ⇒ `validation`; any non-boolean return ⇒ `transform`.

**`function_return_type`** — determines how results are delivered (a looped
scalar return must store per-row results and return them once all rows run; a
column-shaped return runs as a vector and is returned directly).

**[DEFERRED] — return-type vocabulary.** design.md uses `vector`/`matrix` for
`function_return_type` and `pd.series`/`pd.dataframe` for `function_class`. These
are **not yet reconciled** (CLAUDE.md → Active Deferred Work). This section is
written vocabulary-agnostic: a "column-shaped" (1-D) return and a "table-shaped"
(2-D) return are described by behavior; do not encode a single canonical spelling
until the reconciliation is decided.

**Collapse on re-upload (Principle 2).** Functions sharing `function_name`,
`function_class`, and `function_return_type` collapse **strictly on
`content_hash_id`**. On collision the existing surrogate `function_id` is
preserved and only mutable columns are overwritten — keeping `source_function_map`,
`alias_map`, and derived `parameter.content_hash_id` values intact. (This is the
function re-upload collapse; the registry edit-collision in §2 is a separate rule
that *rejects*.)

---

## §12 — `alias_map` binding & multi-select execution

*Implements: CLAUDE.md → Principle 4. Design intent: design.md → alias_map, and
"using the created function after upload".*

`alias_map(alias_map_id, column_id, parameter_id, source_id)` maps a function's
parameter to a source's column. It is what lets one function run across multiple
columns of one source, and lets a parameter↔column mapping be reused across
sources.

- **Resolution at attach time.** When a function is attached to a source, the
  app validates against the `alias_map` that the source's columns are mapped to
  the parameters; an unmapped parameter/column fails the attach with a message
  (no silent attach). `column_backed` params (§11) are resolved here from
  metadata.
- **Keyword binding.** Arguments are bound to parameters **by keyword** via
  `param_name` (§1 `parameter`), not positionally.
  `function_registry.function_signature` (§1) is the canonical captured
  `param_name: type` signature this binding follows; the `parameter` rows are the
  queryable decomposition `alias_map` joins against.
- **Multi-select loop.** For a `multi_select_eligible` parameter with N mapped
  eligible columns, the function runs N times (once per column); results are
  aggregated in the summary (§16/Results — deferred).
- **`pd.DataFrame` implicit binding.** A `pd.dataframe` parameter does **not**
  get an `alias_map` row. The full source table is passed automatically — no
  column mapping UI, no `alias_map` write. The backend fetches the entire instance
  table and passes it as a `pd.DataFrame` at run time. (The parameter still has a
  `parameter` row; it just has no `alias_map` counterpart.)
- **Multi-bind model.** `column_backed` and `pd.Series` parameters support
  **multiple `alias_map` rows per `(parameter_id, source_id)` pair** — this is
  the multi-select case. Each row maps one eligible column; the function runs once
  per row. `pd.DataFrame` parameters are excluded from multi-bind: the full table
  is always passed as a single argument (one run per attach, not one per column).
- The `alias_map` row id is the `uuid5` of (`parameter_id`, `column_id`,
  `source_id`); rows are written directly (no pydantic object), as part of the
  attach transaction (§10).

---

## §13 — Testing conventions

*Implements: CLAUDE.md → rule 9 (every documented behavioral guarantee has a
test). Defined by the `use-file` skill as the home of the behavioral-guarantee
pattern and mocking strategy.*

**Framework & tiers.** `pytest`. Two markers: `unit` (pure logic — no DB, no
subprocess) and `integration` (real DuckDB and/or real subprocess). v1 is
Unix-only and CI runs on Linux, so the `setrlimit` tests always execute — there
is no platform-skip tier to maintain.

**Mocking strategy — real ephemeral DuckDB sandbox, not a fake.** Anything that
touches SQL/transactions runs against a **real** DuckDB engine on disposable
storage; the guarantees under test (rollback, `TRY_CAST`, atomicity, cross-source
joins) *are* DuckDB behaviors and a fake interface cannot validate them.
- `:memory:` by default (the function-scoped `db` fixture); a temp file only when
  a test exercises `table_url` / file-path resolution or needs the DB to survive
  a reopen (the `db_file` fixture).
- **Fresh DB per test** (function-scoped fixture + the `create_schema` builder
  that creates the registry/map tables, plus the `make_registered_source(conn,
  n_columns)` seeding helper in `conftest.py` for "a registered source with N
  columns").
- **Do NOT** use the "wrap each test in a transaction, roll back at teardown"
  isolation trick — the system under test owns transactions/rollback, so a
  test-level transaction would collide with what is being verified.
- Mocks are used only at the pure-logic edge and the worker boundary (below).

**Behavioral-guarantee pattern (the rule-9 mechanism).** One test per documented
guarantee:
- test name encodes the guarantee (e.g. `test_source_create_atomic_when_column_row_fails`);
- arrange-act-assert against **observable state** (per-table row counts, DB
  snapshot equality), never internal call assertions;
- each test references the design.md / CLAUDE.md clause it guards, so the set of
  guarantees can be audited against the set of tests.

**Transaction / rollback recipe** (one per atomic set: source-create, function
registration, function attach, ingestion, migration): snapshot the DB → attempt
the set with a failure injected on a late write → assert **zero** rows from the
set persisted and the snapshot is unchanged. Prefer a **real** failure (data that
violates the schema) over monkeypatch; monkeypatch only where a real failure is
hard to provoke.

**Process isolation (§10).**
- *Boundary guarantee* (`unit`): assert the worker harness only ever passes
  scalar/`Series`/`DataFrame` and never the connection or app objects.
- *Real-subprocess tests* (`integration`): timeout (looping function killed
  within bound); crash (raising function → worker dies, app survives, error
  surfaced via `FailedFunctionEntry`); `setrlimit` memory cap (allocate-big
  function killed). Use tight limits (~1–2 s timeout, small mem cap) so they stay
  fast and deterministic.
- *Result write-back*: mock the worker boundary (a fake result coming back) when
  the test only cares that the backend writes the result into DuckDB correctly.

**Pure-logic tests** (`unit`, no DB, no subprocess) — the bulk of the suite:
`content_hash_id` recompute (contributing field changes → hash changes;
non-contributing field → unchanged; surrogate id never changes); the
edit-collision rejection (recompute onto an existing same-table hash → routed to
`FailedRegistryEntry`); function collapse (same name/class/return_type → same
`content_hash_id`, surrogate preserved, mutables overwritten); the full
`function_class` / `function_type` / `function_return_type` derivation table (§11).

**UUID determinism.** Patch the single injectable surrogate (`uuid4`) factory
(§2) via a fixture for equality assertions (`patch_new_id`). For `content_hash_id`
(`uuid5`, deterministic) assert **relationships** (equal inputs → equal hash;
different table namespace → different hash) rather than hardcoded UUID literals.

**Fixtures / sample data — no committed files.** Where the file-read / inference
path *is* under test, write **real** CSV/xlsx to a temp dir (currently via a
per-test `make_csv` helper in `test_source_create.py`); use in-memory frames /
SQL inserts for everything downstream of an already-loaded table. A richer
quirk-encoding fixture-builder (mixed-type for the `TRY_CAST` migration
pre-check; ambiguous-type for inference; a column that forces the `VARCHAR`
fallback) is **owed but not yet built** — add it when the inference/migration
tests need it. Rationale: keeps DuckDB's native inference honest while avoiding
repo bloat and — important for a tool that ingests user reports — never
committing user-shaped data.

**Known test debt (REFACTOR_PLAN.md):** `patch_new_id` and
`test_source_create_var_fallback` carry stale pre-reorg patch targets
(`pipeui.ids.new_id`, `pipeui.source_create.*`); the edit-collision check
is currently simulated inline in `test_validation.py` rather than exercising real
code.

---

## §14 — Frontend & API layer

*Implements: CLAUDE.md → Frontend & API. Design intent: design.md → Workflows
and Features (the three screens).*

### Design system

The frontend is a self-contained React 18 app with no build step. It uses CDN
React + Babel standalone so the only toolchain requirement is a running FastAPI
server. Key files at `frontend/`:

| file | role |
| --- | --- |
| `index.html` | CSS design tokens, font imports (Geist / Geist Mono), script tags |
| `app.jsx` | App shell — navigation rail (4 items: Data, Functions, Builder, Settings), global state, flash notifications |
| `data.jsx` | Mock data (MODULES, FUNCTION_SETS) — replaced per phase with real `fetch()` calls |
| `ui.jsx` | Shared primitives: `Icon`, `Btn`, `KindTag`, `StatusPill`, `SourceBadge`, `DataTable` |
| `tweaks-panel.jsx` | **Retired in Phase A2** — content moved to `screen-settings.jsx` |
| `screen-data.jsx` | Data screen |
| `screen-modules.jsx` | Functions screen |
| `screen-builder.jsx` | Report Builder screen |
| `screen-settings.jsx` | Settings screen — two sections: Appearance (accent, density) and App (DB path) |

**CSS tokens (defined in `index.html` `:root`):**
- Backgrounds: `--bg` `--panel` `--panel-2` `--panel-3`
- Borders: `--border` `--border-soft` `--hover`
- Text: `--text` `--text-2` `--text-3` `--text-4`
- Semantic: `--good` `--bad` `--warn` `--run`
- Accent (runtime-overridable): `--accent` `--accent-soft` `--accent-line` `--accent-ink`
- Function kinds: `--check` / `--check-bg` (blue) · `--xform` / `--xform-bg` (amber)
- Density: `compact` / `regular` / `comfy` classes override spacing tokens

### API module (`src/pipeui/api/`)

FastAPI route modules — one file per screen domain. Route modules call
`workflow/` functions only; they never reach into `schema/`, `validation/`, or
`sql_user_table/` directly (CLAUDE.md → Architecture module boundaries).

| module | routes | phase |
| --- | --- | --- |
| `sources.py` | `GET /sources` · `POST /sources` · `POST /sources/{id}/ingest` · `GET /sources/{id}` · `GET /sources/{id}/rows` · `PATCH /sources/{id}/columns/{col_id}` | A, B, B2, C |
| `settings.py` | `GET /settings` · `PATCH /settings` | A2 |
| `functions.py` | `GET /functions` · `POST /functions` · `GET /functions/{id}` | D |
| `pipelines.py` | `GET /pipelines/{source_id}` · `POST /pipelines/{source_id}/steps` · `DELETE /pipelines/{source_id}/steps/{step_id}` · `POST /pipelines/{source_id}/run` | E |
| `validations.py` | `POST /validations/run?function_id={id}` — fan-out: run one validation function across all sources it is attached to; returns `{ function_id, function_name, sources: [{ source_id, source_name, status, rows_passed, rows_failed, pass_rate, failing_rows, error }] }`; 404 on unknown function | F1 |

FastAPI also mounts `frontend/` as a `StaticFiles` directory so a single
`uvicorn src.pipeui.api:app` process serves both the UI and the JSON endpoints.

### Screen-to-route wiring (per phase)

| Phase | Screen | Replaces mock | Real endpoints used |
| --- | --- | --- | --- |
| A | Data | `REPORTS` list + source badges | `GET /sources` · `POST /sources` |
| A2 | Settings | `tweaks-panel.jsx` retired | `GET /settings` · `PATCH /settings` |
| B | Data | Row counts, status pills, skip report | `POST /sources/{id}/ingest` · `GET /sources/{id}` |
| C | Data drawer | Schema type dropdowns + castability modal | `PATCH /sources/{id}/columns/{col_id}` |
| D | Functions | Module list + function cards | `GET /functions` · `POST /functions` |
| E | Builder | Reports rail, function palette, pipeline steps, run results | all `pipelines.py` routes |
| F1 | Results (Validations) | Placeholder replaced; By Source + By Function sub-tabs | `POST /pipelines/{source_id}/run?run_type=validations` · `POST /validations/run?function_id={id}` · `GET /sources` · `GET /functions` |

### Settings config (`pipeui.config.json`)

Written to the repo root; added to `.gitignore` by Phase A2 so it is never committed.
Read at startup into an `AppSettings` pydantic model; written back on `PATCH /settings`.

| field | type | default | notes |
| --- | --- | --- | --- |
| `db_path` | string | `"pipeui.db"` | Path to the DuckDB file; change requires server restart |
| `accent` | string | `"#7c6cf5"` | CSS hex colour applied to `--accent` token on load |
| `density` | string | `"regular"` | One of `compact` \| `regular` \| `comfy`; applied as body class on load |

**Restart-required rule.** `PATCH /settings` always writes to `pipeui.config.json` and returns the updated values plus a `restart_required: true` flag when `db_path` changed. The Settings screen surfaces this as a persistent notice. Appearance fields (`accent`, `density`) apply immediately in the frontend without a restart — the saved values are just loaded on the next boot so they persist.

---

## §15 — Package structure

The Python package uses a flat layout (`pipeui/` at the repo root). The `src/`
move is deferred to production packaging. The React frontend is a peer directory
at the repo root. Current layout after Phase A, with planned additions annotated:

```
pipeui/
  __init__.py
  ids.py               # surrogate new_id() (uuid4) + two-level uuid5 content_hash_id (§2)
  duckdb.py            # get_connection, create_schema, DuckDB-native type inference, db-path resolution
  helpers.py           # filename → regex pattern inference (§6.1)
  main.py              # FastAPI app entry-point, static file mount, run() console script (§14)
  schema/
    __init__.py
    constants.py       # DUCKDB_TO_PYTHON / PYTHON_TO_DUCKDB maps, IngestionMethod enum
    queries.py         # DDL for the 4 registries + 3 map tables (§1)
  validation/
    __init__.py
    source.py          # SourceRegistryEntry / SourceRegistryUpdate (pydantic v2, §3)
    column.py          # ColumnRegistryEntry / ColumnRegistryUpdate (pydantic v2, §3)
    fails.py           # FailedRegistryEntry / FailedFunctionEntry stacks (§4)
  workflow/
    __init__.py
    staging.py         # CreateFlowCache — transient temp-table staging (§5)
    create.py          # create_source() — one-transaction source-create flow (§6)
    ingestion.py       # [Phase B] staged load + upsert/append/skip (§9)
    migration.py       # [Phase C] TRY_CAST pre-check + recreate-and-copy + atomic swap (§7)
  sql_user_table/      # [Phase B] pure DDL generator — no per-source files (§8)
    __init__.py        # build_create_table_sql() + instance_table_name()
  api/
    __init__.py
    sources.py         # /sources routes (Phase A + B + C) (§14)
    settings.py        # /settings routes (Phase A2) (§14)
    functions.py       # /functions routes (Phase D) (§14)
    pipelines.py       # /pipelines routes (Phase E) (§14)
frontend/              # React UI — no build step (CDN React 18 + Babel standalone) (§14)
  index.html           # CSS design tokens, font imports, script tags
  app.jsx              # Shell: 4-item nav rail, global state, flash notifications
  data.jsx             # Mock data stubs for Phases D–E — shrinks per phase
  ui.jsx               # Shared primitives: Icon, Btn, KindTag, StatusPill, SourceBadge, DataTable
  screen-data.jsx      # Data screen (Phase A + B + C)
  screen-modules.jsx   # Functions screen (Phase D)
  screen-builder.jsx   # Report Builder screen (Phase E)
  screen-settings.jsx  # [Phase A2] Settings screen — Appearance + App sections
pipeui.config.json     # [Phase A2] runtime config (db_path, accent, density) — gitignored
tests/
  __init__.py
  conftest.py          # db / db_file / patch_new_id fixtures + make_registered_source seeding helper
  test_ids.py          # §2
  test_validation.py   # §3, §4
  test_staging.py      # §5
  test_schema.py       # §1
  test_source_create.py  # §6
  test_api_sources.py  # §14 Phase A guarantees
  test_api_settings.py # [Phase A2] §14 Phase A2 guarantees
  test_ingestion.py    # [Phase B] §9
  test_migration.py    # [Phase C] §7
pyproject.toml
```

**Layer boundaries:**
- `ids.py` is foundational — imported by both `validation/` and `workflow/`; it
  holds no DB handle and depends on nothing else in the package.
- `schema/` + `duckdb.py` are the DB layer (DDL, type maps, connection,
  inference). No validation or workflow logic here.
- `validation/` holds the pydantic `*Entry`/`*Update` objects and rejection
  stacks. These **do not read other table rows** (§3 boundary).
- `workflow/` owns the DuckDB connection and transactions. The atomic write sets
  and the edit-collision check live here. Map rows are written directly from here;
  registry rows go through the `validation/` objects.
- `api/` calls `workflow/` functions only — never `schema/`, `validation/`, or
  `sql_user_table/` directly (CLAUDE.md → Architecture).
- `frontend/` communicates with the backend through `api/` HTTP endpoints only.

---

## §16 — Completed work history

- **Prior sessions** — authored `design.md` (canonical design intent) and
  `CLAUDE.md` (distilled "what/why", routing table, principles, Active Deferred
  Work).
- **CLAUDE_REFERENCE authoring session** — created this file with §1–§13
  populated and §14–§16 reserved, tied 1:1 to the CLAUDE.md routing table.
  Implementation decisions pinned (all consistent with design.md): surrogate
  ids `uuid4`; `content_hash_id` two-level `uuid5`; `*Entry`/`*Update` are
  pydantic v2; user-function worker boundary uses Arrow IPC; v1 is Unix-only.
- **Implementation + reorg session (Claude Code)** — built Phase 0 + Phase 1:
  id generation, the DuckDB schema/DDL, the test harness, the pydantic
  validation objects, the create-flow staging cache, and the one-transaction
  `create_source` flow — under the new `schema/` / `validation/` / `workflow/`
  package layout (§15). Left some debt (now in REFACTOR_PLAN.md).
- **Docs-sync session** — reconciled the docs to the implemented code and the
  reorg, and pinned the open decisions surfaced by the diff: ingestion methods =
  `upsert`/`append`/`skip` (semantics §9); `column_type` uninferable fallback =
  `VARCHAR` (not `var`); `content_hash_id` edit-collision = **reject** at the
  write boundary (§2/§3); `function_signature` **retained and defined** as the
  canonical `param_name: type` signature for keyword binding (§1, §12). Opened
  REFACTOR_PLAN.md for the code debt the reorg left.
- **Frontend & phase-restructure session (2026-06-08)** — introduced the React
  frontend design (`frontend/`), adopted the `src/`-style repository layout,
  replaced the CLI placeholder in §14 with the full frontend/API implementation
  spec, updated §15 to the `src/pipeui/` layout with `api/` and `sql_user_table/`
  annotations, and restructured ROADMAP.md from horizontal phases (backend-then-
  frontend) into vertical phases (A–F), each delivering backend + API + frontend
  together.
- **Phase B session (2026-06-08)** — shipped `feat/jit-instance-table`,
  `feat/ingestion`, and `feat/api-sources-ingest`. Added `pipeui/sql_user_table/`
  (pure DDL generator; corrected §8 from the per-file misconception),
  `pipeui/workflow/ingestion.py` (`ingest_source` + `get_source_detail`), and
  wired `POST /sources/{id}/ingest` + `GET /sources/{id}`. Frontend: drawer
  fetches live detail; `IngestModal` for file + method; skip report rendered
  inline. 17 new behavioral-guarantee tests; 63/63 passing.
- **Phase A session (2026-06-08)** — shipped `feat/api-sources-register`.
  Backend: `pipeui/main.py` (FastAPI entry-point, static file mount),
  `pipeui/api/sources.py` (`GET /sources`, `POST /sources`) using
  `Depends(get_conn)` for connection injection and `dependency_overrides` in
  tests. Fixed `create_source` to reuse existing `column_registry` rows when two
  sources share a column definition (same name+type → same `content_hash_id`),
  rather than hitting the `UNIQUE` constraint. Frontend: full React 18 CDN shell
  — `index.html`, `app.jsx`, `ui.jsx`, `tweaks-panel.jsx`, `screen-data.jsx`
  (live), `screen-modules.jsx` and `screen-builder.jsx` (Phase D/E placeholders).
  Added `httpx` to dev dependencies for `TestClient`. `DB_PATH` left as a
  hardcoded constant; env-var and app-settings approaches deferred. Layout stays
  flat (`pipeui/` not `src/pipeui/`); `src/` move deferred to production
  packaging. 5 behavioral-guarantee tests added; 46/46 passing.
- **Phase A2 session (2026-06-08)** — shipped `feat/app-settings`.
  Backend: `pipeui/api/settings.py` (`GET /settings`, `PATCH /settings`) with
  partial-merge semantics (`model.model_copy(update=...)`); `restart_required:
  true` returned only when `db_path` changes. Config file (`pipeui.config.json`)
  eagerly created at repo root on first startup with hardcoded defaults; loaded
  once into `DB_PATH` at startup in `main.py`. Cleared REFACTOR_PLAN.md debt:
  removed `os` import and `DB_PATH` env-var fallback from `main.py`; removed
  duplicate `DB_PATH` from `sources.py` (now reads from `main.DB_PATH`).
  Frontend: `screen-settings.jsx` — Appearance section (accent colour picker +
  density selector, live CSS-var apply on click) and App section (db_path input);
  Save button fires `PATCH /settings`; persistent warning banner when
  `restart_required`. Retired `tweaks-panel.jsx` (deleted); promoted its
  accent/density logic into `screen-settings.jsx`. Updated `app.jsx` to a
  4-item nav rail (Data, Functions, Builder, Settings); removed TweaksPanel
  wiring. Added `pipeui.config.json` to `.gitignore`. 6 behavioral-guarantee
  tests added; 52/52 passing.
