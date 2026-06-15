---
feature_slug: render-builtin-steps
source_discovery_hash: a0d42b76d928beb82b8a394755800ee1a1d63b7ce2fe4ba1e9c0e6641a3aff3e
---

## Problem Statement

When a user drags the join built-in onto the Report Builder canvas and configures it
through the two-step join modal (shipped in #152), the step is saved to the pipeline —
but nothing appears on the canvas. The placed built-in step is invisible: the canvas
only ever renders function steps, so the user gets no confirmation the join exists, no
view of how it is configured, and no way to remove or change it short of editing the
database by hand. From the user's perspective, configuring a join silently does nothing.

## Solution

A placed built-in step renders as its own card on the Builder canvas, appearing the
moment the user saves it — no manual refresh. The built-in card is visually distinct
from function-step cards (it reuses the palette's built-in accent treatment and a
`built-in` tag) and shows a one-line summary of its configuration (e.g.
`Join · <right source> · inner · 2 keys`). Each placed built-in card carries a remove
control and an edit control: remove drops the step; edit re-opens the same join modal
pre-filled with the current configuration so the user can adjust it and save. Today only
the join built-in is configurable end-to-end, so it is the one exercised, but the card is
generic over any built-in type and orders correctly among the user's function steps.

## User Stories

1. As a Report Builder user, I want a placed join step to appear as a card on the canvas
   as soon as I save it in the join modal, so that I get immediate confirmation my join
   was added.
2. As a Report Builder user, I want a built-in step's card to look visibly different from
   function-step cards, so that I can tell at a glance which steps are built-ins.
3. As a Report Builder user, I want the built-in card to show a summary of its
   configuration (the join's right source, join type, and key-pair count), so that I can
   understand what the step does without opening it.
4. As a Report Builder user, I want the built-in card to sit in the correct position
   relative to my function steps, so that the canvas reflects the true execution order of
   my pipeline.
5. As a Report Builder user, I want to remove a placed built-in step from the canvas, so
   that I can undo a join I no longer want.
6. As a Report Builder user, I want to edit a placed built-in step by re-opening its
   configuration pre-filled, so that I can change the join without deleting and recreating
   it.

## Implementation Decisions

- **Canvas endpoint — extend `get_pipeline`, don't switch.** The pipeline-fetch workflow
  behind `GET /pipelines/{source_id}` is extended so its `steps` list includes built-in
  steps alongside function steps, each tagged with a `step_type` discriminator
  (`"function"` | `"builtin"`) and ordered by position. This is the minimal change: the
  canvas already reads this endpoint, and its function steps carry the rich nested
  `functions[]` payload the function-step card depends on. The alternative — repointing the
  canvas at the separate unified-pipeline endpoint (`GET /sources/{source_id}/pipeline`) —
  was rejected because that endpoint returns *thin* function steps without `functions[]`,
  so switching would regress the function cards. Built-in steps are sourced by reusing the
  existing `get_builtin_steps` helper; each carries `builtin_type` and `builtin_config`.
  Function steps gain `step_type:"function"` and keep `functions[]` unchanged.
- **Frontend renders by `step_type`.** The canvas dispatches on the discriminator: a
  built-in step renders a distinct built-in card variant (reusing the palette built-in
  accent + `built-in` tag); a function step renders the existing card. For backward safety,
  a step with no `step_type` is treated as a function step.
- **Built-in identity and actions use the built-in routes.** A built-in step is identified
  by `step_id` (not `source_function_map_id`), so the canvas keys, removes, and edits it via
  the built-in endpoints: remove → `DELETE /sources/{source_id}/attach-builtin/{step_id}`;
  edit → `PATCH /sources/{source_id}/attach-builtin/{step_id}`.
- **Config summary derived from `builtin_config`.** The built-in card composes its one-line
  summary from the config blob (for join: right-source name, join type, number of key
  pairs).
- **Edit re-uses the #152 join modal in an update branch.** The built-in card's edit control
  re-opens the join modal seeded with the current `builtin_config`; on save the modal issues
  a `PATCH` (update) instead of the `POST` (attach) it uses for a new step. This extends the
  modal — currently POST-only — to a create/update branch. Immediate appearance after both
  create and edit falls out of the existing `loadPipeline()` re-fetch the modal already
  calls on success.
- **No new schema.** Builds on #152 (join modal, shipped) and #117 (built-in steps backend:
  `source_builtin_map`, attach/detach/patch, `get_builtin_steps` — shipped). No new tables
  or migrations.

## Testing Decisions

- **Good tests assert external behavior, not internals** — the JSON shape the endpoint
  returns and the rendered/interactive behavior of the cards, never private component state
  or raw SQL.
- **Seam 1 — API level.** FastAPI `TestClient` on `GET /pipelines/{source_id}`, following the
  behavioral-guarantee style and fixtures of `tests/test_api_pipelines.py`. Guarantees: a
  placed built-in step appears in `steps[]` with `step_type:"builtin"`, carrying
  `builtin_type` + `builtin_config`, positioned correctly among function steps; function
  steps carry `step_type:"function"` and still include `functions[]` (no regression). A lower
  companion unit exercises `get_pipeline()` directly (in the `tests/test_attach.py` style) for
  pure ordering/shape.
- **Seam 2 — frontend component level.** vitest + @testing-library/react against the named
  exports of `screen-builder.jsx` (`StepCard`, `JoinModal`), following
  `src/pipeui/frontend/screen-builder.test.jsx` (which already tests `StepCard` and a
  `JoinModal` harness, stubbing `window.__UI__` and mocking `fetch`). Guarantees: the built-in
  `StepCard` variant renders distinctly (the `built-in` tag + config summary); remove calls
  `DELETE /sources/{id}/attach-builtin/{step_id}`; edit opens `JoinModal` pre-filled from
  `builtin_config`; `JoinModal` submit in edit mode calls
  `PATCH /sources/{id}/attach-builtin/{step_id}`.
- **Modules tested:** `workflow/attach.py` (`get_pipeline`) and the `/pipelines` route;
  `screen-builder.jsx` (the built-in `StepCard` variant and `JoinModal` edit mode).
- **Prior art:** `tests/test_api_pipelines.py`, `tests/test_attach.py`, `tests/test_builtins.py`
  (pytest); `src/pipeui/frontend/screen-builder.test.jsx` (vitest).

## Out of Scope

- **Drag-reordering built-in cards.** Built-in cards are non-draggable in this slice.
  Function-step drag *across* a built-in keeps its existing PATCH-by-`sfm_id` behavior.
  Position ties between `source_function_map` and `source_builtin_map` are a pre-existing
  edge not addressed here.
- **Dedicated filter/pivot step cards.** The built-in card is generic over `builtin_type`,
  but only join is wired end-to-end (modal + attach + execute), so only join is exercised and
  tested.
- **The OrderBadge/Checkbox StepCard interaction redesign** (the `builder-step-cards-redesign`
  brief) — already shipped in `ui.jsx`; not part of this feature.
- **Consolidating the canvas onto the unified `GET /sources/{source_id}/pipeline` endpoint.**

## Further Notes

- `get_pipeline` gains `step_type` on function steps too; the frontend treats a missing
  `step_type` as a function step for backward safety.
- Edit reuses the exact #152 join modal; `builtin_config` must round-trip back into the
  modal's state for pre-fill — the primary implementation risk on the edit path.
- Built-in steps render interleaved by position but are not draggable in this slice.
- **Design note:** #209 has no outstanding visual-design dependency. The design README maps
  the `builder-step-cards-redesign` brief to #209, but that brief is the OrderBadge/Checkbox
  redesign (already shipped); the built-in card's distinct look comes from the existing
  palette built-in treatment (`PaletteBuiltinCard`).
