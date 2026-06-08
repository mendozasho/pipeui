---
created: 2026-06-08
phase: D2
feature: feat/phase-d2-function-sets
depends_on: Phase D (function registration complete)
---

# PRD — Phase D2: Function Sets

## Problem Statement

A user registers many functions across multiple modules. When assembling a pipeline for a source, they want to apply a curated, reusable group of functions in a specific order — without having to re-select and re-order them each time. Today there is no way to save and reuse an ordered collection of functions.

## Solution

Introduce **function sets** — user-curated, named, ordered lists of registered functions. A set is a reusable playlist: the user assembles it once on the Functions screen, names it, and later attaches it to any source in the Builder (Phase E1). The Functions screen gains a **Sets tab** alongside the existing Functions tab, with a two-panel create/edit layout.

## User Stories

1. As a user, I want to create a new function set with a name, so that I can group related functions for reuse.
2. As a user, I want to add an optional description to a function set, so that I can explain its purpose to my future self.
3. As a user, I want to browse all registered functions in a filterable left panel, so that I can find the function I want to add to a set.
4. As a user, I want to add a function to the right panel (the ordered pipeline), so that it becomes a member of the set being created or edited.
5. As a user, I want to see the functions in the right panel in their execution order, so that I know what order they will run in.
6. As a user, I want to drag to reorder functions in the right panel, so that I can control execution order.
7. As a user, I want to click a function in the right panel to remove it, so that I can easily adjust the set's membership.
8. As a user, I want to be prevented from adding the same function to a set twice, so that I do not accidentally create duplicate pipeline steps (multi-column runs are handled by alias_map at attach time, not by repeating a function in the set).
9. As a user, I want to save a set, so that it persists and is available to attach to sources later.
10. As a user, I want to see all existing function sets as cards on the Sets tab, so that I can browse what I've created.
11. As a user, I want each set card to show the set name, description, and member count, so that I can identify the set at a glance.
12. As a user, I want to see a warning marker on a set card when one or more member functions are inactive (`is_active = false`), so that I know the set is partially unavailable before I try to attach it.
13. As a user, I want the warning marker to show how many functions are unavailable, so that I understand the severity.
14. As a user, I want to click a set card to open an edit view, so that I can rename, re-describe, or reorder its members.
15. As a user, I want to rename a set, so that I can correct a name after creation.
16. As a user, I want to update a set's description, so that I can keep its documentation current.
17. As a user, I want to reorder members of an existing set, so that I can change the execution order without deleting and recreating the set.
18. As a user, I want to add or remove member functions from an existing set, so that I can evolve the set as my needs change.
19. As a user, I want to delete a function set, so that I can remove sets I no longer need.
20. As a user, I want deleting a set to leave all member functions intact in the registry, so that deleting a set never removes a function I might use elsewhere.
21. As a user, I want inactive functions to remain visible in a set's member list (muted style), so that I understand the set's composition and can decide whether to remove the inactive member.
22. As a user, I want the Sets tab to load its data from the real API (not mock data), so that sets I create persist across browser refreshes.

## Implementation Decisions

### Schema additions

Two new tables added to the DDL in `schema/queries.py`:

**`function_set`** — registry table following the dual-id pattern (Principle 1):
- `set_id` UUID PK (uuid4 surrogate — the only value maps reference)
- `content_hash_id` UUID NOT NULL UNIQUE (uuid5; contributing field: `set_name` only)
- `set_name` VARCHAR NOT NULL
- `set_description` VARCHAR (nullable)

**`function_set_map`** — relational map table (written directly, no pydantic object):
- `set_map_id` UUID PK — uuid5 derived from `(set_id, function_id)`, so the same pair never produces two rows (enforces no-duplicate-member rule structurally)
- `set_id` UUID NOT NULL — FK → `function_set.set_id`
- `function_id` UUID NOT NULL — FK → `function_registry.function_id`
- `position` INTEGER NOT NULL — 0-based ordering

Both tables are added to the single `DDL` string; `init_db()` creates them alongside all existing tables.

### Workflow layer

New module `workflow/function_sets.py` exposes:

- `create_function_set(conn, set_name, set_description, members)` → `set_id` | `FailedRegistryEntry`
  - Writes `function_set` row + all `function_set_map` rows in **one transaction**.
  - `members` is an ordered list of `function_id` strings; positions assigned 0, 1, 2, …
  - `content_hash_id` collision on `set_name` → reject (surface as failure, Principle 1).

- `list_function_sets(conn)` → list of set summaries (id, name, description, member count, `has_inactive` flag)
  - `has_inactive` is true when any member function has `is_active = false`.

- `get_function_set(conn, set_id)` → full detail (id, name, description, ordered member list with `function_name`, `function_type`, `is_active`) | None

- `update_function_set(conn, set_id, set_name?, set_description?, members?)` → ok | `FailedRegistryEntry`
  - If `members` is provided: delete all existing `function_set_map` rows for `set_id` and reinsert the new list — replace-members semantics.
  - Registry field updates + member replacement in **one transaction**.
  - `content_hash_id` recomputed when `set_name` changes; collision check at write boundary.

- `delete_function_set(conn, set_id)` → ok | None (404 if not found)
  - Deletes `function_set_map` rows first, then `function_set` row, in one transaction.
  - Member functions in `function_registry` are untouched.

### API routes

New router in `api/function_sets.py`, mounted in `main.py` at `/function-sets`:

- `GET /function-sets` → list of set summaries
- `POST /function-sets` body: `{set_name, set_description?, members: [function_id, …]}` → created set detail | structured failure (not 500)
- `GET /function-sets/{id}` → full set detail | 404
- `PATCH /function-sets/{id}` body: `{set_name?, set_description?, members?}` → updated detail | structured failure | 404
- `DELETE /function-sets/{id}` → 204 | 404

`api/` calls `workflow/function_sets.py` only — no direct schema/validation imports (module boundary rule).

### Frontend

`screen-modules.jsx` gains a **Sets tab** alongside the existing Functions tab. Tab state is local (`useState`).

**Functions tab** — existing `ScreenModules` content, unchanged.

**Sets tab** — two views:

1. **List view** — grid of set cards. Each card: set name, description, member count ("N functions"), warning chip ("N unavailable") when `has_inactive` is true. "New Set" button opens the editor in create mode.

2. **Editor view** (create or edit) — two-panel layout:
   - Left panel: filterable list of all registered functions (name, type badge, inactive muted). Clicking a function adds it to the right panel if not already present.
   - Right panel: ordered member list. Drag handle to reorder. Click (×) to remove. Name + description inputs at top. Save / Cancel buttons.
   - On save: `POST /function-sets` (create) or `PATCH /function-sets/{id}` (edit) with the full current member list (replace-members).

`data.jsx` — the `FUNCTION_SETS` placeholder is removed entirely; the Sets tab fetches from `GET /function-sets` directly.

### Identity and ordering invariants

- `set_map_id` is deterministic: `uuid5(namespace, set_id || function_id)`. Adding the same function twice is structurally prevented at the DB layer.
- `position` is 0-based, gap-free, assigned by the frontend's current order at save time.
- On read, members are always returned `ORDER BY position ASC`.
- `content_hash_id` for `function_set` uses the `function_set` table namespace (two-level uuid5, §2) with `set_name` as the sole contributing field.

## Testing Decisions

**What makes a good test here:** test the workflow layer's behavioral guarantees against a real DuckDB sandbox — not implementation internals. Mirror the pattern in `tests/test_migration.py` (workflow layer) and `tests/test_api_migration.py` (API layer via FastAPI `TestClient`).

**Workflow tests** (`tests/test_function_sets.py`):
- Create a set → rows exist in both tables, positions correct.
- Create with duplicate `set_name` → `FailedRegistryEntry` returned, no partial write.
- List sets → `has_inactive` flag is true when a member function has `is_active = false`.
- Get set detail → members returned in position order.
- Update with new members list → old `function_set_map` rows gone, new ones in; registry fields updated; all in one transaction (partial failure rolls back).
- Rename to a colliding `set_name` → `FailedRegistryEntry`, original row unchanged.
- Delete set → `function_set` and `function_set_map` rows gone; `function_registry` rows intact.
- Add same function twice to a set (attempt) → structurally rejected (uuid5 PK collision).

**API tests** (`tests/test_api_function_sets.py`):
- `POST /function-sets` happy path → 200, set_id returned.
- `POST /function-sets` duplicate name → structured failure payload, not 500.
- `GET /function-sets` → list with correct member counts and `has_inactive`.
- `GET /function-sets/{id}` not found → 404.
- `PATCH /function-sets/{id}` replace members → members updated.
- `DELETE /function-sets/{id}` → 204; member functions still in `GET /functions`.

Prior art: `tests/test_migration.py` (sandbox fixture, behavioral guarantee assertions), `tests/test_api_migration.py` (TestClient + `Depends` override pattern).

## Out of Scope

- Attaching a function set to a source — that is Phase E1 (`feat/phase-e1-function-attach`).
- Executing a function set — that is Phase E2.
- Showing which sources a set is attached to in the set detail view — deferred to Phase E1.
- Reordering via a dedicated reorder endpoint — the replace-members PATCH is sufficient.
- Per-set scalar parameter overrides — deferred to Phase F3 (v2 scalar persistence).

## Further Notes

- The `FUNCTION_SETS` mock in `data.jsx` has the wrong shape (it is source-attached, belonging to Phase E, not D2). It is removed entirely in this phase; no migration of mock shape is needed.
- `screen-modules.jsx` is currently named for "modules" (the old CLI concept). It remains unchanged in name to avoid a rename churn; any rename is tracked in REFACTOR_PLAN.md.
- The `ScreenModules` component is currently exported as `window.__ScreenModules__`. The Sets tab lives inside this same component under a tab switcher — no new top-level screen component is needed.
- Inactive functions in a set are never auto-removed — they stay in the `function_set_map` and in the editor's member list (muted), consistent with how `is_active = false` works on `function_registry` (the function is never deleted, only flagged).
