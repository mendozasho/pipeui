---
created: 2026-06-10
purpose: >
  Backlog of cleanup, QoL, and v2 feature requests to plan in a future session.
  Not yet broken into phases or vertical slices — that planning happens when a
  session picks this file up. Items tagged [UI], [Backend], or [Both].
---

# FEATURE_REQUESTS.md

## v1 Cleanup — Code Correctness & Debt

These close out known gaps in v1 before v2 work begins.

- **[Backend]** `migration.py` — replace inline `_content_hash_id()` calls with a proper `ColumnRegistryUpdate` object at the write boundary (REFACTOR_PLAN.md §3 compliance).
- **[Backend]** Move `AppSettings` from `api/settings.py` to `validation/settings.py` to fix the module boundary violation (api/ must not own validation/schema objects).
- **[Backend]** Reconcile return-type vocabulary: `vector`/`matrix` in prose vs `pd.series`/`pd.dataframe` in `function_class`. Pick one and make the codebase consistent (CLAUDE.md Active Deferred Work).
- **[Backend]** PK uniqueness enforcement — decide whether to validate that the assumed PK column is actually unique at registration time, or formally defer to v2.
- **[Backend]** Rename `pipeui/duckdb.py` → `db.py` to avoid shadowing the third-party `duckdb` package (low priority, readability only).
- **[Backend]** Quirk-encoding fixture builder for tests — CSV/xlsx fixtures with mixed-type columns, ambiguous-type columns, and VARCHAR-fallback columns (owed from REFACTOR_PLAN.md §13 build debt).

---

## v1 QoL — UI Polish & UX Improvements

Candidate items — to be refined with Claude Design before implementation.

- **[UI]** Results screen: empty state copy and visual treatment when no cards exist yet.
- **[UI]** Loading indicators — consistency audit across all screens (Data, Functions, Builder, Results); some flows lack spinners or disable states during async operations.
- **[UI]** Error surfacing — inline error messages vs flash toasts are inconsistent; audit and standardise.
- **[UI]** Builder screen — pipeline step reordering UX (drag handles are present but may need polish).
- **[UI]** Functions screen — Run button placement and flow (moved into drawer in F2; verify the full happy path feels right end-to-end).
- **[UI]** Data page — source card row density and information hierarchy (source name, row count, last ingested date).
- **[UI]** Export flow — file naming preview before download so the analyst knows what they're getting.
- **[UI]** Results cards — timestamp formatting (relative "2 minutes ago" vs absolute ISO).
- **[UI]** Mobile / narrow viewport — the app is desktop-only today; decide if responsive is in scope.

---

## v2 Features — New Capability (Phase naming TBD, prefix "2")

These are net-new features beyond v1 scope. Phase names (2A1, 2B2, etc.) to be assigned in a planning session.

- **[Backend]** v2 scalar persistence — per-source scalar argument override store so UI overrides survive across runs (CLAUDE.md Active Deferred Work).
- **[Both]** Persistent staging tables — transform results written to a named DuckDB table that survives session refresh (currently session-only ephemeral).
- **[Both]** Cross-source join UI — combine two sources in the Results/Summary layer via a DuckDB direct join (no ATTACH needed); deferred from F2.
- **[Both]** Run history persistence — result cards written to DuckDB so history survives refresh (currently session-only).
- **[UI]** Results screen — filter/search cards by source, function, date, or type.
- **[Both]** Scheduled / watched runs — re-run a pipeline automatically when a source file changes on disk.
