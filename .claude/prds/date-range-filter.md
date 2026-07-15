---
feature_slug: date-range-filter
source_discovery_hash: 28c6e4447d80ff9f70caaf26f93933eb12b225b35026397ce6094836c356db84
---

## Problem Statement

A user producing a report from a source's pipeline has no way to restrict the final
output to a date window. The transformed output always carries every row, so a user
who needs "Q1 policies, or anything renewing in 2025" must export everything and
filter by hand outside the app. The existing filter built-in cannot express this:
it holds exactly one condition, has no range operator, and has no OR — and nothing
in the Builder understands that a date column deserves a calendar, not a text box.

## Solution

A new **date_range step** on the Builder palette. The user drops it onto a source's
pipeline and a modal opens where they build the date filter one **range condition**
at a time: pick a date-typed column from a date-only picker, then pick inclusive
start and end dates from the browser's native calendar popups (either bound may be
left open). Conditions added together form a **filter group** (ANDed); "+ Add OR
group" starts an alternative branch (groups are ORed). The step is a singleton that
always runs at the end of the pipeline — in the **pinned tail**, just before rename —
so the transformed report, its exports, and every deliverable built from it contain
only the rows that match. Dropping the palette card again reopens the existing step
for editing, restoring exactly what was saved.

## User Stories

1. As a report producer, I want to filter the final report to rows whose date column falls in a calendar-picked start/end range, so that the exported report covers exactly the period I'm asked for.
2. As a report producer, I want to add multiple range conditions one at a time on different (or the same) date columns, so that I can constrain several dates — effective date, ship date, renewal date — in one step.
3. As a report producer, I want to combine conditions with both AND and OR — conditions inside a group all holding, with alternative groups as OR branches — so that I can express filters like "(effective in Q1 AND shipped by June) OR renewing in 2025" without ambiguity about what binds tighter.
4. As a report producer, I want to leave a range one-sided (only a start, or only an end), so that I can express "on or after X" / "on or before Y" without inventing a fake far-off bound.
5. As a report producer, I want the column picker to offer only date-typed columns, so that I cannot build a filter that fails at runtime against a text or numeric column.
6. As a report producer, I want the date filter to always apply to the final table regardless of where the card sits, so that no later pipeline step can reintroduce rows I filtered out.
7. As a report producer, I want rows stamped late on the range's end day (e.g. 23:59) to be included, so that the filter never silently drops the last day of a reporting period.
8. As a report producer, I want at most one date filter per source, with the palette drop reopening it for editing, so that there is exactly one place that owns the report's date window.
9. As a report producer, I want reopening the filter to show my groups and conditions exactly as I saved them, so that editing one bound never silently loses or reorders the rest.
10. As a report producer, I want an invalid filter (no conditions, a condition with no bounds, start after end) rejected when I try to save it, so that a broken filter can never be attached and fail at run time.

## Implementation Decisions

All decisions below are frozen in the discovery ledger; rationale is carried in spirit.

- **New builtin type, not a filter extension.** `date_range` registers as its own
  `BuiltinSpec` (validate + execute) in the builtin registry, with a seeded catalog
  row. The existing filter built-in is untouched — its flat `{column, operator,
  value}` config shares no operator vocabulary, value shape, or validation with
  grouped range conditions, and extending it would migrate persisted configs to
  serve a date-only feature. The builtin canvas card is already generic over
  `builtin_type`, so the step renders without card work. Revisit trigger: a future
  grouped filter for non-date columns is its own discovery.
- **Config shape: one-level DNF.** `{groups: [{conditions: [{column, start, end}]}]}`
  stored as the step's `builtin_config` JSON in the per-source builtin map table.
  Conditions inside a group are ANDed; groups are ORed. No chained combinators
  (invisible precedence), no nested groups (recursive config and UI for unasked-for
  depth). The same column may appear in any number of conditions — the OR use case
  requires it.
- **Range condition semantics.** Inclusive on both ends. One-sided allowed:
  start-only = on-or-after, end-only = on-or-before; both bounds empty is invalid.
  TIMESTAMP/TIMESTAMPTZ columns are cast to DATE before comparison (a row stamped
  Mar 31 23:59 is inside a range ending Mar 31). A NULL date fails its condition
  (standard SQL); the row may still pass via another group. TIMESTAMPTZ casts use
  DuckDB's session-default timezone.
- **Pinned tail, before rename.** The step executes after all positional steps and
  before the rename step, regardless of stored position: its conditions reference
  registered column names and rename relabels the output afterward. The current
  pinned-last mechanism is a magic string repeated at three sort sites (tracked as
  #83); this feature must generalize it into one ordered pinned-tail definition
  (e.g. ordering metadata on the registry/spec) consumed by all sites — not add a
  fourth magic-string site.
- **Singleton.** At most one date_range step per source, enforced by the existing
  generic `singleton` flag on the builtin spec (verified reusable — attach already
  enforces it for rename). Dropping the palette card when one exists opens the
  existing step for editing, rename's exact pattern.
- **Validation split across the module boundary.** The pure config validator (no DB
  connection, like every builtin validator) rejects structural invalidity: zero
  groups, an empty group, a condition missing a column, both bounds empty, start >
  end. Date-typed column eligibility (needs `column_registry`) is enforced at the
  attach/patch write boundary — the same boundary-owns-DB-checks pattern Principle 1
  uses for hash collisions.
- **Executor.** DuckDB SQL over the working frame like the other built-ins: temp
  view + one WHERE clause of OR-ed group predicates, each group an AND of range
  predicates, bounds bound as parameters. The reduced frame becomes the next staging
  table — existing row-reduction plumbing, no runner/dispatch changes expected.
- **Column picker: registered date columns only.** DATE / TIMESTAMP / TIMESTAMPTZ
  per `column_registry`, surfaced through the existing columns payload
  (`column_type` is already in every columns response). VARCHAR-held dates are not
  offered — the sanctioned fix is the existing column-type migration (Principle 6).
  Columns created mid-pipeline are consciously deferred (no end-of-pipeline schema
  machinery exists).
- **Modal UI: native date inputs, existing add-row pattern.** Two native
  `<input type="date">` per condition (browser calendar popup, zero dependencies,
  no design gate under the HITL rule). Conditions and groups accumulate via the
  established "+ Add another" modal pattern; per-row remove. A date-only column
  picker is net-new but is a type filter over the existing column list component.
- **Edit round-trip (Principle 7).** Edit-load returns groups and conditions in
  persisted form and order; saving without touching a field persists it unchanged.

## Testing Decisions

Good tests here assert external behavior at a seam — config in, rows/order/HTTP
status out — never internal helper structure. Realistic data is a standing project
rule: date matrices must include NULL dates, TIMESTAMP values late on boundary
days, and one-sided ranges (clean synthetic data has hidden real bugs before).

Feature-level seams (all existing; confirmed with the user):

1. **Builtin domain seam** — validator shape matrix (zero groups, empty group,
   both-bounds-empty, start > end) and executor semantics matrix on an in-memory
   DuckDB frame (inclusive bounds, one-sided, TIMESTAMP→DATE cast incl. the
   23:59-on-end-day row, NULL-fails-condition, AND within group, OR across groups).
   Registry membership and seeded catalog row (existing count assertions grow by
   one). Prior art: the builtin behavioral-guarantee suite.
2. **Attach/write-boundary seam** (HTTP) — singleton enforcement (second attach
   edits rather than duplicates), date-typed eligibility rejected at attach/patch
   with a clear message, persisted-config round-trip (PATCH → read back returns
   groups in persisted order — the Principle 7 test). Prior art: builtin
   attach/patch route tests.
3. **Unified-pipeline ordering seam** — the pinned tail sorts positional steps,
   then date_range, then rename, identically through the generalized mechanism at
   every consumer of the ordering (pipeline read, unified pipeline, run order).
   Prior art: the rename pinned-last tests.
4. **End-to-end run seam** — attach a date_range to a real ingested source, run
   the pipeline over HTTP, read staging back: only matching rows remain, and the
   transformed export reflects the reduction. Prior art: run→staging integration
   tests.
5. **Frontend component seam** (vitest + jsdom, named exports + UI stub) — the
   modal: date-only picker offers only date-typed columns, add/remove condition and
   OR group, validation states disable save, submitted config shape, and the
   open → save-untouched → identical-config round-trip; palette entry and the card
   config summary. Prior art: the existing builtin modal tests in the Builder
   screen suite.

Every behavioral guarantee stated in this PRD must have a corresponding test
(project rule 10) — the semantics matrix in seam 1 and the round-trips in seams 2
and 5 are the guarantees most at risk of being skipped.

## Out of Scope

- Filtering non-date columns through this UI — the existing single-condition filter
  built-in covers those; a grouped generic filter would be its own discovery.
- Nested boolean groups beyond one-level OR-of-AND-groups (DNF already expresses
  every mixed AND/OR combination).
- Time-of-day precision in the picker; TIMESTAMP columns compare at DATE
  granularity.
- Filtering on columns created mid-pipeline by transforms/joins (needs
  end-of-pipeline schema computation — consciously deferred).
- VARCHAR columns holding date strings (fix is the existing column-type migration).
- Rich custom range-picker UI (two-month spread, presets) — would design-gate the
  UI slice; native inputs chosen instead.
- Timezone-aware TIMESTAMPTZ semantics beyond DuckDB's session-default cast.

## Further Notes

- Grounding was verified against code at discovery time: the existing filter
  built-in is single-condition with no range operator ("between" is explicitly
  rejected); built-ins execute as DuckDB SQL over the working frame and the reduced
  frame becomes the next staging table; `column_type` already reaches the frontend
  in every columns payload.
- The `singleton` flag on the builtin spec was re-verified during PRD writing: it
  is generic and enforced in the attach path ("a future singleton type is one
  registration"), so no new enforcement mechanism is needed.
- The pinned-tail generalization deliberately pays down #83 (the triplicated
  "rename" magic string) as part of this feature rather than adding a fourth site.
- No date-picker primitive or type-filtered column picker exists in the frontend
  today; both are net-new but small (native inputs; a type filter over the existing
  column list).
- Glossary terms for this feature — **date_range step**, **range condition**,
  **filter group**, **pinned tail** — are canonical in `.claude/CONTEXT.md`; use
  them in all downstream phases.
