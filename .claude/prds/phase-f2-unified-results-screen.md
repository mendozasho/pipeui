---
created: 2026-06-10
phase: F2
status: approved
---

# PRD — Phase F2: Unified Results Screen with Cards

## Problem Statement

After running validations and transforms, the analyst has no coherent place to
review, compare, or export their results. The current Results screen (F1) uses
a dropdown + run-button model scoped entirely to validations — it is the wrong
pattern for a read-only results viewer, it mixes trigger and display concerns on
the same screen, and it leaves transforms with no UI at all. The analyst wants a
single place to see every run result, understand what happened, and get the
deliverable out.

---

## Solution

Replace the F1 Results screen with a unified card grid. Every run — whether
triggered from the Data page, the Functions page, or the Builder — appends a
result card to the Results screen. Cards are tagged `validation` or `transform`
to distinguish them at a glance. Each card shows a summary on its face and
expands to reveal detail. Cards are selectable for mass export; individual cards
also have their own export button. The Results screen itself has no run triggers.

Run triggers move to the pages where the user already has context:

- **Data page** — "Run" button on a source; runs all functions attached to
  that source (validations + transforms).
- **Functions page** — "Run" button on a function or function set; fans out
  across all attached sources, producing one card per run.
- **Builder** — existing run buttons are removed (functionality now lives on
  the Data and Functions pages). Result tags on pipeline canvas cards remain
  and deep-link to the relevant card on the Results screen.

---

## User Stories

1. As an analyst, I want to run all functions attached to a source from the
   Data page, so that I can kick off a full pipeline run without switching to
   the Builder.
2. As an analyst, I want to run a specific function or function set from the
   Functions page, so that I can re-run a single check across all its attached
   sources without navigating away from my function list.
3. As an analyst, I want every completed run to appear as a card on the Results
   screen, so that I have one place to review all my run history for the session.
4. As an analyst, I want each card to be tagged as `validation` or `transform`,
   so that I can tell at a glance what kind of result I am looking at.
5. As an analyst, I want to see a run timestamp on each card, so that I can
   distinguish two runs of the same source.
6. As an analyst, I want cards ordered most-recent-first, so that my latest run
   is always at the top.
7. As an analyst, I want each new run to append a new card rather than
   overwriting the previous one, so that I can compare results across runs
   within a session.
8. As an analyst, I want a validation card to show me a summary of total rows
   passed, total rows failed, and overall pass rate, so that I can assess
   quality at a glance without expanding the card.
9. As an analyst, I want to expand a validation card to see the per-function
   pass/fail breakdown and the capped preview of failing rows, so that I can
   drill into which checks failed and why.
10. As an analyst, I want a transform card to show me the row count and column
    list of the resulting table, so that I can confirm the transform ran
    correctly before exporting.
11. As an analyst, I want to expand a transform card to see a preview of the
    transformed table, so that I can spot-check the output.
12. As an analyst, I want a checkbox on each card so that I can select multiple
    results, so that I can export several runs in one action.
13. As an analyst, I want selected cards to have a visible highlighted state,
    so that I always know which cards I have chosen.
14. As an analyst, I want an "Export Selected" button that appears when one or
    more cards are selected, so that I can export multiple results at once.
15. As an analyst, I want each card to have its own inline Export button, so
    that I can export a single result without selecting it first.
16. As an analyst, I want to export a validation result as CSV or Excel (.xlsx),
    so that I can deliver the failing rows in the format my stakeholder prefers.
17. As an analyst, I want to export a transform result as CSV or Excel (.xlsx)
    containing the full post-transform table, so that I can deliver the cleaned
    or enriched dataset.
18. As an analyst, I want exported files to be named descriptively (source name
    + timestamp + type), so that I can identify the file without opening it.
19. As an analyst, I want clicking a result tag on a Builder pipeline canvas
    card to navigate me to the Results screen and scroll/highlight the relevant
    card, so that I can move directly from "something failed" to the detail
    without hunting.
20. As an analyst, I want the Results screen to show an empty state when no runs
    have happened yet in this session, so that I understand why the screen is
    blank.
21. As an analyst, I want a function-scoped run from the Functions page to
    produce one card per triggered run (with source + function columns in the
    detail), so that I can see which sources passed and which failed in a single
    card.
22. As an analyst, I want each card to carry a unique `run_id`, so that two
    runs of the same source and function are always treated as distinct cards
    and never merged.
23. As an analyst, I want the run trigger on the Data page to provide a loading
    indicator while the pipeline is running, so that I know the run is in
    progress.
24. As an analyst, I want the run trigger on the Functions page to provide a
    loading indicator while the run fans out across sources, so that I know
    the run is in progress.
25. As an analyst, I want a failed step within a run to be surfaced on the card
    (not silently dropped), so that I know something went wrong and can
    investigate.

---

## Implementation Decisions

### Run trigger locations

- **Data page**: the source detail drawer (or source card row) gains a "Run"
  button. Clicking it calls `POST /pipelines/{source_id}/run` with
  `run_type=all` (new value — see API section). On completion the frontend
  appends a result card to the Results screen state and navigates there.
- **Functions page**: each function card and each function-set card gains a
  "Run" button. For a single function, the frontend calls
  `POST /validations/run?function_id={id}` (existing endpoint). For a function
  set, a new endpoint `POST /pipelines/run-set?set_id={id}` fans out the set
  run across all sources it is attached to. On completion a card is appended.
- **Builder**: the "Run Validations" and "Run Transforms" buttons are removed.
  Before removal an agent audits whether any other component shares their
  click handler — if shared logic exists it is extracted into a shared helper
  before deletion. Result tags on pipeline canvas cards remain and deep-link
  to the Results screen.

### run_id and card identity

Each run result carries a `run_id` (UUID4) generated on the frontend at the
moment the run response is received. The `run_id` is the card's identity for
the session. It is never persisted to DuckDB. The backend does not generate or
return a `run_id`; the frontend is responsible for assigning one.

### Card data shape

Two card variants:

**Validation card** (produced by source-scoped or function-scoped validation run):

```
{
  run_id: string,          // UUID4, frontend-assigned
  card_type: "validation",
  trigger: "source" | "function",
  source_id: string | null,    // set for source-scoped runs
  source_name: string | null,
  function_id: string | null,  // set for function-scoped runs
  function_name: string | null,
  run_at: ISO8601 timestamp,
  summary: {
    rows_passed: number,
    rows_failed: number,
    pass_rate: number | null,
  },
  steps: [...],  // per-function result rows as returned by the pipeline run API
}
```

**Transform card** (produced by source-scoped transform run):

```
{
  run_id: string,
  card_type: "transform",
  trigger: "source",
  source_id: string,
  source_name: string,
  run_at: ISO8601 timestamp,
  summary: {
    rows_affected: number,
    columns: string[],
  },
  steps: [...],  // per-step result rows
}
```

When a source run includes both validations and transforms (run_type=all), the
frontend splits the response into two cards — one validation card and one
transform card — both sharing the same logical run but with distinct `run_id`
values.

### API changes

- `POST /pipelines/{source_id}/run` gains `run_type=all` as a valid value.
  With `run_type=all` the backend executes all steps regardless of type and
  returns both validation steps and transform steps together in the `steps`
  array, each step tagged with its `function_type`.
- New endpoint: `POST /pipelines/run-set?set_id={id}` — finds all sources the
  set is attached to, calls `run_pipeline` for each source with the relevant
  run type, and returns a list of per-source results. Returns 404 if the set
  does not exist or is not attached to any source.
- Existing `POST /validations/run?function_id={id}` is unchanged.

### Export format

A thin export-format module with two named exporters: `csv` and `xlsx`. Each
exporter receives a list of row dicts and a filename stem and returns a blob /
triggers a download. Adding a new format (e.g. JSON, Parquet) means adding one
branch to this module. The format selector on each card or the "Export Selected"
dialog offers CSV and Excel as the two options.

For validation exports: failing rows only (same as F1 behavior).
For transform exports: full post-transform table rows.

Filename pattern: `{source_name}_{run_at_date}_{card_type}.csv` /
`{source_name}_{run_at_date}_{card_type}.xlsx`.

### Results screen restructure

- F1's "By Source" and "By Function" sub-tabs are retired.
- The screen becomes a flat scrollable card grid (most-recent-first).
- Cards are the only UI element — no dropdowns, no run buttons.
- Empty state: shown when the in-session card list is empty.
- Deep-link: `resultsContext` prop on `ScreenResults` gains a `run_id` field;
  when set, the card with that `run_id` is highlighted and scrolled into view.
- Result tags on Builder canvas cards continue to set `resultsContext` and
  navigate to Results; they now carry a `run_id` reference instead of just
  `source_id`.

### Card result state

Cards live in a top-level React state array (appended to, never replaced).
State is session-only — lost on page refresh, consistent with the existing
ephemeral model established in F1.

---

## Testing Decisions

**What makes a good test here:** test observable API behavior, not React
component internals. Backend tests verify that the new `run_type=all` and
`POST /pipelines/run-set` endpoints return correct structured payloads. Frontend
behavior (card append, selection, export trigger) is exercised manually since
the project has no frontend test harness.

**Backend tests to write:**

- `POST /pipelines/{source_id}/run?run_type=all` with a source that has both
  validation and transform steps attached: assert the response contains steps
  of both `function_type` values.
- `POST /pipelines/{source_id}/run?run_type=all` with a source that has only
  validation steps: assert only validation steps are returned; no 500.
- `POST /pipelines/run-set?set_id={id}` with a set attached to two sources:
  assert a per-source result list with two entries.
- `POST /pipelines/run-set?set_id={id}` with an unattached set: assert 404.
- A crashing step within a `run_type=all` run: assert the failed step is
  present in the response and subsequent steps still run.

Prior art: `tests/test_api_migration.py` for route-level tests with real DuckDB
sandbox fixtures; `tests/test_ingestion.py` for workflow-level guarantees.
The behavioral-guarantee pattern in §13 applies: one test per guarantee above.

---

## Out of Scope

- Persistent run history — cards are session-only in v1; they are not written
  to DuckDB and are lost on refresh.
- Cross-source join UI — deferred to v2 (CLAUDE.md Active Deferred Work:
  Results & Summary layer).
- Persistent staging tables — staging tables remain session-only DuckDB tables
  (not promoted to the main DB) as established in Phase E2.
- v2 scalar persistence (Phase F3) — per-source scalar overrides are not
  stored; this remains an open deferred item.
- Builder result-tag wiring to a specific `run_id` when the Builder itself no
  longer has run buttons — the result tag will deep-link to the most recent
  card for that source on the Results screen as a pragmatic v1 fallback.

---

## Further Notes

- The `run_type=all` API extension is the minimal change needed to support
  a one-click "run everything" trigger on the Data page. The split into two
  cards (validation + transform) happens entirely on the frontend — the backend
  does not need to know about cards.
- The Builder run-button removal should be done by a dedicated agent that reads
  `screen-builder.jsx` first and checks whether the run handler is shared with
  any other component before deleting it.
- CONTEXT.md should be updated at the start of implementation to add `run_id`
  and `result card` as glossary terms.
- The export-format module should be implemented as a plain JS object/map keyed
  by format name so that adding a new format is a one-line registration, not a
  conditional chain.
