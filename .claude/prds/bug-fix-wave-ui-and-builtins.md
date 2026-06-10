---
created: 2026-06-10
status: approved
---

# Bug-Fix Wave — UI Polish, Built-in Steps, and Scalar Persistence

## Problem Statement

Several usability gaps and bugs have accumulated since v1 shipped:

- Built-in steps (join, pivot) exist in the backend schema but are orphaned in the UI: the Built-ins tab lives on the Functions screen with the wrong layout, and the Builder's RightPalette has no Built-ins tab at all. Filter is missing entirely.
- The source detail drawer shows Details and Data as always-expanded walls of content, making it hard to focus on the relevant section. Only Columns is collapsible.
- On the Functions screen, module files with many functions produce unscrollable walls of cards; there is no way to collapse a module group.
- Single-function sets are exposed in the Sets tab and the Builder palette, leaking a backend implementation detail (the backend auto-creates a set when a single function is dragged) that means nothing to the user.
- The Settings nav item looks like any other nav page, sitting inline with Data / Functions / Builder / Results instead of being visually anchored to the bottom of the sidebar.
- The Builder RightPalette is drag-only: there is no way to inspect a function or set's details before committing to dragging it onto a pipeline.
- Scalar parameters (`int`, `float`, `bool`) are invisible in the parameter mapping modal. The backend's dry-run excludes them and the attach path does not persist their values, so users have no way to configure or change a function's scalar inputs — they are silently locked to the Python default.
- All pipeline steps on the Builder canvas are delete-only: once attached, the user cannot update a step's column bindings or scalar values without removing and re-adding it.

## Solution

Ship a focused wave of fixes and features that closes each gap:

1. **Built-in steps fully wired**: `builtin_registry` seeded at DB init for join, pivot, and filter. Functions screen Built-ins tab renders cards and drawers matching the Functions tab layout. Builder RightPalette gains a Built-ins tab with draggable cards. Join's two-step modal lets the user pick the right-hand source (raw or transformed) before configuring the join columns.
2. **Collapsible source drawer sections**: Details (expanded by default), Columns (already collapsible, no change), Data (collapsed by default).
3. **Collapsible module groups**: each file group on the Functions screen is expanded by default and togglable.
4. **Single-function sets hidden**: the frontend filters them from every surface.
5. **Settings pinned to the sidebar bottom**.
6. **Builder palette click-to-drawer**: clicking (not dragging) a function or set card opens its detail drawer.
7. **Scalar params visible, persistent, and editable**: dry-run returns scalar params; `source_scalar_map` persists overrides; all pipeline steps have an edit affordance.

## User Stories

### Built-in steps

1. As an analyst, I want a Built-ins tab in the Builder's right palette, so that I can see what built-in steps are available and drag them onto my pipeline.
2. As an analyst, I want join, pivot, and filter to be the three built-in steps, so that I have the core data-shaping operations without writing code.
3. As an analyst, I want built-in cards in the Builder palette to be draggable onto the pipeline canvas, so that I can add them to my pipeline the same way I add function sets.
4. As an analyst, I want a Built-ins tab on the Functions screen, so that I can browse and understand each built-in step in the same place I browse registered functions.
5. As an analyst, I want each built-in card on the Functions screen to match the layout of function cards (name, type badge, description), so that the screen is visually consistent.
6. As an analyst, I want to click a built-in card on the Functions screen to open a detail drawer, so that I can read the full description and parameter schema before using it.
7. As an analyst, I want the detail drawer for a built-in to show its name, description, and the configuration fields it accepts, so that I understand how to use it before dragging it onto a pipeline.
8. As an analyst, I want dragging a join step onto a pipeline to open a two-step modal, so that I can first select the right-hand source, then configure the join columns.
9. As an analyst, I want the join source picker to list all registered sources except the current one, so that I can choose which report to join against.
10. As an analyst, I want sources that have transform steps in their pipeline to show a "Use transformed output" toggle in the join source picker, so that I can join against either their raw data or their pipeline's output.
11. As an analyst, I want the "Use transformed output" toggle to default to off (raw data), so that joining against raw data is the safe default and opting into pipeline output is an explicit choice.
12. As an analyst, I want the column picker in the join modal to show columns from both the left and right sources combined, so that I can configure left_on and right_on using any available column.
13. As an analyst, I want the column picker to reflect the right-hand source's post-pipeline columns when "Use transformed output" is on, so that I'm mapping to the actual output columns, not the raw ones.
14. As an analyst, I want dragging a pivot step to open a single-step modal, so that I can configure index columns, pivot column, value columns, and aggregation functions.
15. As an analyst, I want dragging a filter step to open a single-step modal, so that I can choose a column, an operator, and a value.
16. As an analyst, I want the filter operator options to include: eq, neq, gt, gte, lt, lte, contains, not_contains, is_null, is_not_null, so that I can express common row-level predicates.
17. As an analyst, I want the filter column picker to use the effective column set at the step's pipeline position, so that I can filter on any column available at that point including those added by a preceding join.
18. As an analyst, I want the dry-run response to include the effective column set at the current pipeline position, so that the parameter mapping modal always shows the right columns regardless of preceding join steps.

### Source drawer collapsibility

19. As an analyst, I want the Details section in the source detail drawer to be expanded by default, so that I see the key metadata immediately when I open the drawer.
20. As an analyst, I want to collapse the Details section by clicking its header, so that I can hide it when I want to focus on columns or data.
21. As an analyst, I want the Data section (row preview table) in the source detail drawer to be collapsed by default, so that opening the drawer is fast and not cluttered by a large table.
22. As an analyst, I want to expand the Data section by clicking its header, so that I can see the preview rows when I need them.

### Module groups collapsibility

23. As an analyst, I want each module file on the Functions screen to be expanded by default, so that I see all functions immediately.
24. As an analyst, I want to collapse a module group by clicking its file header, so that I can hide modules I'm not currently working with and reduce visual noise.
25. As an analyst, I want each module group to maintain its own collapsed/expanded state independently, so that collapsing one file does not affect others.
26. As an analyst, I want the function count to remain visible in the module group header when the group is collapsed, so that I know how many functions are hidden.

### Single-function sets hidden

27. As an analyst, I want the Sets tab on the Functions screen to hide sets with only one member, so that I only see sets that represent meaningful groupings I've created.
28. As an analyst, I want the Builder palette Sets tab to hide single-member sets, so that the palette is not cluttered with auto-generated sets.
29. As an analyst, I want an empty state on the Sets tab when all sets are single-member, so that the screen does not appear broken.

### Settings nav placement

30. As a user, I want the Settings icon to appear at the bottom of the left sidebar, so that it is always visually distinct from the main navigation items and easy to find.

### Builder palette click-to-drawer

31. As an analyst, I want to click a function card in the Builder palette to open that function's detail drawer, so that I can review its signature, docstring, and parameter types before deciding to use it.
32. As an analyst, I want to click a set card in the Builder palette to open a detail drawer showing the set's member functions, so that I can confirm the set contains what I expect before dragging it.
33. As an analyst, I want the set detail drawer to show the ordered list of member functions with type badges and active/inactive status, so that I can assess the set's coverage at a glance.
34. As an analyst, I want the set detail drawer to warn me if any member function is inactive, so that I know the set may produce incomplete results.
35. As an analyst, I want dragging a card to still work even after I've clicked it to open the drawer, so that inspecting a function doesn't interrupt my workflow.

### Scalar params — visibility, persistence, editability

36. As an analyst, I want scalar parameters (`int`, `float`, `bool`) to appear in the parameter mapping modal when I drag a function onto a pipeline, so that I can set their values at attach time.
37. As an analyst, I want each scalar input to display its parameter type (e.g. `int`, `float`), so that I know what kind of value to enter.
38. As an analyst, I want a hint on scalar inputs explaining that a non-matching type may cause a runtime error, so that I understand the risk before entering a value.
39. As an analyst, I want to leave a scalar input blank to use the function's Python default, so that I don't have to specify values for parameters I'm happy with.
40. As an analyst, I want scalar values I enter at attach time to be persisted per source per parameter, so that they survive page refreshes and session restarts.
41. As an analyst, I want scalar overrides to be used automatically at run time, so that I don't have to re-enter them every time I run the pipeline.
42. As an analyst, I want an edit icon on each pipeline step card in the Builder, so that I can update the step's column bindings and scalar values without removing and re-adding it.
43. As an analyst, I want the edit modal for a step to open pre-populated with the step's current column bindings and scalar values, so that I can see what is currently configured and change only what I need.
44. As an analyst, I want saving an edited step to update the bindings in `alias_map` and the scalar values in `source_scalar_map`, so that the changes take effect on the next run.
45. As an analyst, I want the edit modal to behave identically to the initial attach modal, so that the experience is consistent regardless of whether I'm configuring a new or existing step.

## Implementation Decisions

### New table: `builtin_registry`

A catalog table seeded at DB init time. Mirrors `function_registry` in purpose. Columns: `builtin_id UUID PRIMARY KEY`, `builtin_type VARCHAR UNIQUE NOT NULL` (the internal key: `join`, `pivot`, `filter`), `display_name VARCHAR NOT NULL`, `description TEXT`, `config_schema JSON` (the parameter shape for this built-in type). No surrogate/content-hash-id pattern is needed — `builtin_type` is the stable identifier. Seeded once at `create_schema` time; never modified by user action.

### `source_builtin_map` update

The existing `builtin_type` column is a `VARCHAR`. The DDL comment and any validation that restricts it to `"join" | "pivot"` must be widened to include `"filter"`. This is additive — no recreation needed.

### New table: `source_scalar_map`

Columns: `scalar_map_id UUID PRIMARY KEY`, `source_id UUID NOT NULL` (references `source_registry`), `param_id UUID NOT NULL` (references `parameter`), `value VARCHAR NOT NULL`. Unique constraint on `(source_id, param_id)`. Written via UPSERT so re-attaching or editing a step overwrites the previous value. `value` stored as VARCHAR; cast to `param_type` at run time by the execution layer. If no row exists for a (source, param) pair, the Python default is used.

### `suggest_bindings` — scalar params included via separate path

`_SUGGEST_TYPES` is not modified — it governs column-binding suggestion logic only. Scalar params (`int`, `float`, `bool`) are collected from the same raw param query and appended to the dry-run response under a separate field or as entries with `param_kind: "scalar"` so the frontend can distinguish them. The response shape becomes:

```
{
  "params": [
    {
      "param_id": "...",
      "param_name": "...",
      "param_type": "...",       // "str", "pd.Series", "int", "float", "bool", etc.
      "param_kind": "scalar" | "column",  // new field
      "suggested_columns": [...],          // empty list for scalar params
      "current_scalar_value": "..." | null // from source_scalar_map if a row exists
    }
  ],
  "available_columns": [
    { "column_id": "...", "column_name": "...", "column_type": "..." }
  ]
}
```

`available_columns` replaces the frontend's reliance on `pipeline.source.columns`. It is computed server-side from the source's column registry plus any columns introduced by join steps already in the pipeline (in position order), so it is always correct at the current pipeline depth.

### `PatchStepIn` extended

The PATCH step body gains two optional fields: `bindings` (same shape as the attach body's `bindings`) and `scalar_values` (map of `param_id → value string`). When `bindings` is present, the workflow layer replaces all `alias_map` rows for that step in a transaction. When `scalar_values` is present, it upserts into `source_scalar_map`. `position` and `output_mode` remain optional as before.

### New API endpoint: `GET /builtins`

Returns all rows from `builtin_registry` as a list. No filtering. Used by both the Functions screen Built-ins tab and the Builder RightPalette Built-ins tab.

### `GET /function-sets` filtering

The endpoint itself does not change. The frontend filters the response client-side: any set whose `function_count == 1` (or whose detail response contains exactly one member) is suppressed from all UI surfaces.

### Built-in attach modal — two-step for join, one-step for pivot/filter

The frontend detects the `builtin_type` from the dragged card's data. For `join`: render a source picker step first (all sources except current, with optional "Use transformed output" toggle for sources that have pipeline steps), then on source selection fire a dry-run-like request to resolve column sets from both sources. For `pivot` and `filter`: go directly to the config step. The join source picker and its card design go through a Claude Design pass before implementation.

### Scalar input UI in attach/edit modal

Each scalar param renders a free-text input. A type badge next to the input shows the expected type (`int`, `float`, `bool`). A small inline hint reads: "Leave blank to use the Python default. Entering a value of the wrong type may cause a runtime error." The scalar input UI detail goes through a Claude Design pass before implementation.

### Builder palette click behaviour

`PaletteFunctionCard`: `onClick` opens the existing `FunctionDrawer` (fetches `GET /functions/{id}`). Drag handlers are unchanged — the `draggable` attribute and `onDragStart` remain on the card root.

`PaletteSetCard`: `onClick` opens a new `SetDetailDrawer` (fetches `GET /function-sets/{set_id}`). The drawer is read-only: set name, description, ordered member function list with type badges and active/inactive status, and a warning if any member is inactive. No edit/delete/run controls.

### Settings nav placement

`marginTop: "auto"` on the Settings nav item in the `NavRail` component. No other structural changes.

### Source drawer collapsibility

Two new collapsed-state variables in `SourceDrawer`: `detailsExpanded` (default `true`) and `dataExpanded` (default `false`). The `Section` component used for Details and Data is replaced with an inline collapsible pattern matching the existing Columns toggle (click header to toggle, `›` / `∨` indicator). Columns behaviour is unchanged.

### Module group collapsibility

`ScreenModules` gains a `collapsedModules` state (a `Set` of `module_path` strings, initially empty so all are expanded). Each module group header gets an `onClick` that adds/removes the path from the set. The `›` / `∨` indicator is added to the right of the function count in the header. When collapsed, only the header row renders.

## Testing Decisions

Good tests assert observable backend behaviour at the highest seam possible, not implementation details. For backend changes, the workflow-layer tests in `test_attach.py` and a new `test_builtins.py` are the primary seams. API-layer tests in `test_api_pipelines.py` cover the contract surface. Frontend-only changes have no automated tests and are verified by running the dev server.

### `test_builtins.py` (new)

- Assert `builtin_registry` is seeded with exactly three rows (`join`, `pivot`, `filter`) when `create_schema` runs on a fresh connection.
- Assert `GET /builtins` returns all three rows.
- Assert `source_builtin_map` accepts `builtin_type = "filter"` without error.
- Prior art: `test_attach.py` pattern for workflow-layer table assertions.

### `test_attach.py` extensions

- Assert `suggest_bindings` response includes scalar params (`int`, `float`, `bool`) with `param_kind: "scalar"` and empty `suggested_columns`.
- Assert `suggest_bindings` does not modify `_SUGGEST_TYPES` behaviour: column-backed suggestion still only fires for `str` and `pd.Series` params.
- Assert `suggest_bindings` response includes `available_columns` derived from the source's column registry.
- Assert `available_columns` includes columns from join steps already in the pipeline (integration: seed a source with a join step, assert the joined columns appear).
- Assert `source_scalar_map` row is created when `patch_pipeline_step` is called with `scalar_values`.
- Assert `source_scalar_map` row is upserted (not duplicated) on a second call with the same `(source_id, param_id)`.
- Assert `patch_pipeline_step` with `bindings` replaces existing `alias_map` rows atomically.
- Prior art: `test_scalar_param_exempt_from_binding`, `test_suggest_excludes_scalar_params` — these existing tests document the old exclusion behaviour and should be updated to reflect inclusion.

### `test_api_pipelines.py` extensions

- Assert `GET /function-sets` returns single-member sets (the filter is frontend-only — the API must not change).
- Assert dry-run response shape includes `available_columns` and `param_kind` fields.
- Assert PATCH step with `scalar_values` returns `{ ok: true }` and persists the value.
- Prior art: `test_dry_run_returns_suggestions_without_writing`, `test_dry_run_excludes_dataframe_params`.

### Frontend-only (no automated tests)

Settings nav pin, source drawer collapsibility, module group collapsibility, single-function set filtering, Builder palette click-to-drawer, scalar input UI — verified by running the dev server and exercising each feature manually.

## Out of Scope

- **Multi-condition filter**: the filter built-in supports a single condition (column + operator + value) in this wave. AND/OR compound filters are deferred.
- **SQL built-in step**: the `sql` built-in type remains in the deferred list.
- **Join result column selection UI**: the `keep_columns` config field exists in the schema but the column-picker UI for it (drag to select which joined columns to keep) goes through Claude Design; if the design is not ready, join attaches with `keep_columns: "all"` as default.
- **Transformed right-hand source execution**: the on-the-fly pipeline execution for "Use transformed output" join targets requires the execution layer to materialise a staging table for the right-hand source. The UI toggle and the config storage are in scope; the actual on-the-fly execution is deferred until the join execution path is implemented.
- **Scalar type coercion errors surfaced at run time**: the execution layer will attempt to cast `source_scalar_map.value` to `param_type`; runtime cast failures surface as step errors. The specific error message design is out of scope here.
- **v2 scalar persistence UI** (per-source scalar store browser): the `source_scalar_map` table ships in this wave, but a dedicated management screen for viewing/clearing scalar overrides across all sources is deferred.
- **Claude Design passes**: two flows require a Claude Design pass before implementation — the join source picker modal and the scalar input UI in the attach/edit modal. These are tracked as blockers for their respective implementation issues.

## Further Notes

- The `builtin_registry` seed is idempotent: `create_schema` uses `INSERT OR IGNORE` (or `ON CONFLICT DO NOTHING`) so re-running init on an existing database does not duplicate rows.
- The CLAUDE.md Active Deferred Work entry for "v2 scalar persistence" should be struck when this PRD is merged, since `source_scalar_map` is now shipping.
- `_SUGGEST_TYPES` in `attach.py` should gain a comment clarifying it governs column-binding suggestion only, so future readers do not conflate it with the dry-run param inclusion list.
- The join source picker requires the Builder to fetch the pipeline detail for the candidate right-hand source (to determine whether it has transform steps). This fetch happens client-side when the user opens the join modal, not at drag time.
- Single-function set suppression is purely a display filter: the backend creates these sets, stores them, and runs them as before. No backend change.
