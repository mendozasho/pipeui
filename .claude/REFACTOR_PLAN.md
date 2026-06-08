---
created: 2026-06-07
updated: 2026-06-07
purpose: >
  Tracks specific code items to move, rename, unify, add, or fix — the debt left
  by the implementation + directory-reorg session. Consult before moving,
  creating, or renaming functions (per the use-file skill). Companion to
  CLAUDE.md (what/why), CLAUDE_REFERENCE.md (how), and ROADMAP.md (build order).
  These are code changes for a Claude Code session, NOT documentation edits.
  Delete each item as it is completed.
---

# REFACTOR_PLAN.md

The docs (design.md, CLAUDE.md, CLAUDE_REFERENCE.md, ROADMAP.md) already describe
the intended end state. This file lists the concrete code edits needed to make
the code match those docs. Group order is roughly safest-first.

## Move / rename

- [ ] **Move `pipeui/validation/ids.py` → `pipeui/ids.py`.** It is foundational
  (imported by both `validation/` and `workflow/`) and shouldn't live under
  `validation/`. Update imports in `validation/source.py`, `validation/column.py`,
  and `workflow/create.py` (all currently `from pipeui.validation.ids import ...`).
  Then fix the test references below. Boundary rationale: CLAUDE.md → Architecture;
  layout in CLAUDE_REFERENCE.md §15.
- [ ] *(Optional, low priority)* **Rename `pipeui/duckdb.py`** to avoid shadowing
  the third-party `duckdb` package it imports (e.g. `engine.py` or `db.py`). Works
  today under Python 3 absolute imports, but the same-name module is a readability
  smell. If renamed, update imports in `duckdb.py`'s consumers and `conftest.py`.

## Unify

- [ ] **`IngestionMethod` enum → `{upsert, append, skip}`.** `schema/constants.py`
  currently defines `upsert`/`append` only; add `skip`. Semantics (CLAUDE_REFERENCE.md
  §9): `upsert` = update-or-insert on dup id; `append` = straight insert, no dup
  handling; `skip` = skip rows whose id already exists **and report the skipped
  rows to the user**.
- [ ] **Align `SourceRegistryEntry._validate_ingestion_method`** (`validation/source.py`)
  to accept all three (currently `upsert`/`skip`). Today the enum and validator
  disagree, so only `upsert` works end-to-end.
- [ ] **`create_source` invalid-method path** (`workflow/create.py`): route an
  invalid `ingestion_method` to `FailedRegistryEntry` instead of the bare
  `raise ValueError` on the `IngestionMethod.accepted()` gate, so the documented
  atomicity guarantee (`test_source_create_atomicity_rollback_leaves_db_unchanged`)
  holds rather than erroring.
- [ ] **Move the edit-collision check into real code.** It is currently simulated
  inline in `tests/test_validation.py::test_content_hash_id_collision_on_edit_surfaces_as_failure`.
  Implement it at the write boundary (CLAUDE_REFERENCE.md §2/§3): `*Update`
  recomputes the hash (already does), then the workflow layer checks the target
  table for an existing row carrying that `content_hash_id` on a *different*
  surrogate id, and on a hit routes to `FailedRegistryEntry` + rolls back (reject,
  no merge). Keep the model free of DB reads.

## Add

- [ ] **Re-add `function_signature` to the `function_registry` DDL**
  (`schema/queries.py`): `function_signature VARCHAR NOT NULL`. It was omitted in
  the schema; it is required for argument binding (CLAUDE_REFERENCE.md §1, §12).
  When `feat/function-registration` is built, populate it with the canonical
  `param_name: type` signature (the `inspect.signature` form incl. return).

## Fix

- [ ] **`tests/conftest.py::patch_new_id`** patches `pipeui.ids.new_id` — wrong
  while `new_id` lives in `pipeui.validation.ids`. After the `ids.py` move above,
  this target becomes correct; verify it patches the single factory so all callers
  see the fixed id.
- [ ] **`tests/test_source_create.py::test_source_create_var_fallback`**: patch
  target is `pipeui.source_create.infer_column_types` (module doesn't exist). It
  should patch where the name is *used*: `pipeui.workflow.create.infer_column_types`.
  Also update the assertion from `"var"` to `"VARCHAR"` (fallback decision).
- [ ] **`pipeui/schema/__init__.py`**: `____all__` (four underscores) → `__all__`.
- [ ] **`pipeui/validation/__init__.py`**: `__all__` lists class objects; it should
  list their **string names**.
- [ ] *(Optional)* **`pyproject.toml`**: duplicate `dev` dependency declarations
  (`[project.optional-dependencies].dev` vs `[dependency-groups].dev`, and
  `pytest>=8.0` vs `pytest>=9.0.3`) — reconcile to one source of truth.

## Build (owed, not strictly debt)

- [ ] **Quirk-encoding file fixture-builder** for §13: writes real CSV/xlsx to a
  temp dir from specs (mixed-type column for the `TRY_CAST` migration pre-check;
  ambiguous-type column for inference; a column that forces the `VARCHAR`
  fallback). Add when `feat/ingestion` / `feat/column-migration` tests need it.
  No committed fixture files.
