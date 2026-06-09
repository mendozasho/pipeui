---
created: 2026-06-09
phase: E1
status: approved
---

# Phase E1 — Function Attach

## Problem Statement

Users have registered sources (with ingested data) and registered functions (with typed parameters), but there is no way to connect the two. The Builder screen is a placeholder. Users cannot assemble a pipeline, cannot specify which columns feed which function parameters, and cannot see what functions are attached to a source. The gap between "I have data" and "I can run analysis on it" is unbridged.

## Solution

Wire the Builder screen end-to-end. Users drag individual functions or named function sets from a palette onto a source to attach them. For each non-scalar function parameter, users bind one or more source columns via type-enforced drop zones. The backend auto-suggests bindings based on prior attachments on other sources that share the same columns. Once all required parameters are bound, the user saves the attachment. The source's pipeline — its ordered list of attached functions with their bindings — becomes queryable and renderable in the Builder.

## User Stories

1. As a user, I want to open the Builder screen and select a source, so that I can see its current pipeline and available columns.
2. As a user, I want to see all registered functions and function sets in a palette on the Builder screen, so that I can choose which ones to attach to my source.
3. As a user, I want to drag a function from the palette onto a source's pipeline, so that I can add it as a step.
4. As a user, I want to drag a function set from the palette onto a source's pipeline, so that I can attach all its functions at once in their defined order.
5. As a user, I want to see the function name and docstring when I drag a function into the pipeline, so that I know what the function does before configuring it.
6. As a user, I want each function parameter to show its name and type in the pipeline step, so that I know what bindings I need to provide.
7. As a user, I want `scalar` parameters to show a free text box, so that I can provide a value directly or leave it empty to use the Python default.
8. As a user, I want `column_backed` and `pd.Series` parameters to show a multi-column drop zone, so that I can drag one or more source columns into it.
9. As a user, I want `pd.DataFrame` parameters to require no binding input, so that I know the function automatically receives the full source table.
10. As a user, I want column bindings to be pre-filled with suggestions when I drag a function onto a source, so that I don't have to manually configure bindings I've already set up on another source with matching columns.
11. As a user, I want to be able to override any pre-filled suggestion, so that I have full control over the final binding.
12. As a user, I want the Save button to be disabled until all `column_backed` and `pd.Series` parameters have at least one column bound, so that I cannot accidentally save an incomplete attachment.
13. As a user, I want to receive a clear error message if I try to save an attachment with unbound required parameters, so that I know exactly what is missing.
14. As a user, I want to bind multiple columns to a single `pd.Series` or `column_backed` parameter, so that the function runs once per bound column.
15. As a user, I want to remove a column from a multi-bind drop zone, so that I can correct a mistaken binding.
16. As a user, I want to remove a function step from the pipeline, so that I can detach a function I no longer need.
17. As a user, I want the pipeline to show functions in their attachment order, so that I can reason about execution sequence.
18. As a user, I want to see which sources a function is attached to from the Functions screen detail drawer, so that I have visibility into where each function is in use.
19. As a user, I want attaching a single function (not from a named set) to feel the same as attaching a set, so that I don't need to create a set just to attach one function.
20. As a user, I want the Builder screen to reflect the current saved pipeline state when I navigate to it, so that my previous attachments are always visible.

## Implementation Decisions

### Schema change: `source_function_map`

`source_function_map` is altered to replace `function_id` with `set_id`. Every attachment — whether the user dragged a named set or a single function — is stored as a set reference. The ordering of functions within a pipeline step is delegated to `function_set_map.position`.

Updated shape:
- `source_function_map(source_function_map_id, source_id, set_id)`

When a user attaches a single function directly, the backend auto-creates a `function_set` row using the function's `function_name` as `set_name`. This auto-set is invisible in the Sets UI. On detach, if no other `source_function_map` rows reference that set, the auto-set and its `function_set_map` rows are deleted. If the set has other references, it is left untouched.

### `alias_map` — unchanged

`alias_map(alias_map_id, column_id, parameter_id, source_id)` is not modified. It remains the binding between a parameter and a column on a source. The set/function-set indirection does not affect this table.

### Parameter binding rules

| param_type | alias_map rows written | execution model | Builder UI |
|---|---|---|---|
| `scalar` | none | one run; value from text box or Python default | free text box |
| `column_backed` | one per bound column | resolves each to `table[column_name]` → pd.Series; one run per column | multi-column drop zone |
| `pd.Series` | one per bound column | one run per column, full series passed | multi-column drop zone |
| `pd.DataFrame` | none | one run; full source table passed automatically | no binding UI |

`column_backed` and `pd.Series` support multi-bind (multiple alias_map rows for the same `parameter_id + source_id`). `pd.DataFrame` is implicitly bound — no alias_map row, no drop zone.

Validation rule: all `column_backed` and `pd.Series` parameters must have at least one alias_map binding before the attach is committed. `scalar` and `pd.DataFrame` params are exempt.

### Security boundary

User functions never write back to persisted instance tables. Results go to session-only staging (Phase E2 concern). This is a hard boundary enforced at the workflow layer, not a UI concern.

### API — `pipelines.py`

New module. Four routes:

**`POST /pipelines/{source_id}/steps?dry_run=true`**
Body: `{ "function_id": "..." }` or `{ "set_id": "..." }`
Returns: suggested bindings for each non-scalar parameter — `{ param_id, param_name, param_type, suggested_columns: [{ column_id, column_name }] }`. Does not write anything. Auto-suggest logic: for each `column_backed`/`pd.Series` param, find existing alias_map rows for that `parameter_id` on other sources where the bound `column_id` also exists in the target source's `source_column_map`. If multiple prior bindings exist for the same param, use the most-recently-created alias_map row as the tiebreaker.

**`POST /pipelines/{source_id}/steps`**
Body: `{ "function_id" | "set_id", "bindings": [{ "param_id": "...", "column_ids": ["..."] }] }`
Writes `source_function_map` + `alias_map` rows in one transaction. Rejects (structured failure, not 500) if any `column_backed`/`pd.Series` param has no binding. Auto-creates a `function_set` if a bare `function_id` is provided.

**`DELETE /pipelines/{source_id}/steps/{source_function_map_id}`**
Removes the `source_function_map` row and all associated `alias_map` rows for that source in one transaction. If the referenced set was auto-created and has no remaining `source_function_map` references, deletes the `function_set` and `function_set_map` rows.

**`GET /pipelines/{source_id}`**
Returns committed pipeline state:
```
{
  "source": { source_id, source_name, columns: [{ column_id, column_name, column_type }] },
  "steps": [
    {
      "source_function_map_id",
      "set_id", "set_name",
      "position",
      "functions": [
        {
          "function_id", "function_name", "function_doc", "function_type",
          "params": [
            { "param_id", "param_name", "param_type", "bindings": [{ column_id, column_name }] }
          ]
        }
      ]
    }
  ]
}
```
IDs are included for frontend wiring but are not surfaced in the UI. All committed steps have complete bindings — the GET response never contains unbound non-scalar params.

### `GET /functions/{id}` update

The `attached_sources` join in the functions workflow module is updated to a two-hop join: `source_function_map → function_set_map → function_id`. The drawer shows each source as connected regardless of whether the attachment came via a named set or an auto-created set. No labelling distinction.

### Frontend — Builder screen

`screen-builder.jsx` replaces the Phase E placeholder. Layout: source selector + function/set palette on the left; pipeline steps on the right. There is no "pipeline" as a user-facing asset — the source is the pipeline context.

**Drag-and-drop flow:**
1. User drags a function or set from the palette onto the source area
2. Frontend fires `POST /pipelines/{source_id}/steps?dry_run=true` to get suggestions
3. A step card appears with drop zones pre-filled from suggestions
4. User adjusts bindings; Save is disabled until all `column_backed`/`pd.Series` params have at least one column bound
5. User clicks Save → `POST /pipelines/{source_id}/steps` with confirmed bindings

**Step card** shows: function name, docstring, per-parameter zones. `scalar` → text input. `column_backed`/`pd.Series` → multi-column drop zone (drag columns from the source's column list). `pd.DataFrame` → no zone (auto label only).

**Detach:** each step has a remove control; fires `DELETE /pipelines/{source_id}/steps/{source_function_map_id}`.

### ROADMAP / docs updates

- Mark Phase D2 complete in ROADMAP.md
- Record the no-write-back security boundary in CLAUDE.md Principle 5
- Add `pd.DataFrame` implicit binding rule and updated multi-bind model to CLAUDE_REFERENCE.md §12

## Testing Decisions

### What makes a good test here

Tests assert observable state — rows present or absent in `source_function_map`, `alias_map`, `function_set`, `function_set_map` — never internal call sequences. Each test guards one documented behavioral guarantee and is named for it (e.g. `test_attach_rejects_when_series_param_unbound`). Prefer real schema violations to trigger failures over monkeypatching.

### Modules to test

**`tests/test_api_pipelines.py`** (primary seam — API via FastAPI TestClient):
- `GET /pipelines/{source_id}` returns empty steps for a source with no attachments
- `POST /pipelines/{source_id}/steps?dry_run=true` returns suggestions without writing
- `POST /pipelines/{source_id}/steps` commits source_function_map + alias_map atomically
- `POST /pipelines/{source_id}/steps` rejects when a `pd.Series`/`column_backed` param is unbound
- `POST /pipelines/{source_id}/steps` with a bare `function_id` auto-creates a function_set
- `DELETE /pipelines/{source_id}/steps/{id}` removes map rows and alias_map rows atomically
- `DELETE` on an auto-created set with no remaining references cleans up the set
- `DELETE` on a set shared by another source_function_map does not delete the set

**`tests/test_attach.py`** (workflow seam — real DuckDB for cases hard to provoke via API):
- Auto-suggest tiebreaker: most-recent alias_map binding wins when multiple prior bindings exist for the same param
- Atomicity: a late alias_map write failure rolls back the entire attach (no source_function_map row left behind)

**`tests/test_schema.py`** (schema seam):
- `source_function_map` has `set_id` column, not `function_id`

### Prior art

- `test_api_migration.py` — API-level guarantee tests with structured failure assertions
- `test_ingestion.py` — atomicity pattern (snapshot before, inject failure, assert zero rows persisted)
- `conftest.py` — `db` fixture, `make_registered_source` seeding helper; extend with `make_registered_function` and `make_function_set` helpers as needed

## Out of Scope

- **Pipeline execution** — functions are attached and bindings configured, but no execution happens in E1. That is Phase E2.
- **Scalar value persistence** — scalar overrides entered in the text boxes are not persisted (v2 deferred work).
- **Drag-to-reorder steps** — pipeline step reordering in the Builder UI is deferred. Steps render in attachment order.
- **Write-back to instance tables** — user functions never write results back to persisted tables. Results go to session-only staging (Phase E2).
- **`pd.DataFrame` column selection** — `pd.DataFrame` params always receive the full source table; partial-table or multi-table DataFrame binding is out of scope.
- **Validation functions vs transform functions distinction in UI** — both are attached and configured the same way; any visual distinction between `function_type = validation` and `transform` in the Builder is deferred to Phase E2/F1.

## Further Notes

- The `source_function_map` DDL change (set_id replaces function_id) is a breaking schema change. The migration must be handled at schema initialisation time — either a DDL recreate or an `ALTER TABLE`. Because the table is currently empty in all real deployments (no attach workflow exists yet), a recreate is safe. The existing `test_schema.py` column assertions must be updated.
- Phase D2 (function sets) is fully implemented and shipped. The ROADMAP.md checkbox should be marked on the session branch.
- The `alias_map_id` is a UUID5 of `(parameter_id, column_id, source_id)` per §12 — this uniqueness constraint means a given parameter↔column↔source triple can only have one alias_map row. Multi-bind for `pd.Series`/`column_backed` is expressed as multiple rows with different `column_id` values, not repeated rows.
