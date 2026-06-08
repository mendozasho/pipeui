---
created: 2026-06-07
updated: 2026-06-09
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

- [ ] *(Optional, low priority)* **Rename `pipeui/duckdb.py`** to avoid shadowing
  the third-party `duckdb` package it imports (e.g. `engine.py` or `db.py`). Works
  today under Python 3 absolute imports, but the same-name module is a readability
  smell. If renamed, update imports in `duckdb.py`'s consumers and `conftest.py`.

## Add

- [ ] **Populate `function_signature` when `feat/function-registration` is built.**
  The DDL column (`function_signature VARCHAR NOT NULL`) now exists in
  `schema/queries.py`. When function registration is implemented, populate it with
  the canonical `param_name: type` signature (the `inspect.signature` form incl.
  return). Binding mechanics: CLAUDE_REFERENCE.md §1, §12.

## Build (owed, not strictly debt)

- [ ] **Quirk-encoding file fixture-builder** for §13: writes real CSV/xlsx to a
  temp dir from specs (mixed-type column for the `TRY_CAST` migration pre-check;
  ambiguous-type column for inference; a column that forces the `VARCHAR`
  fallback). Add when `feat/ingestion` / `feat/column-migration` tests need it.
  No committed fixture files.
