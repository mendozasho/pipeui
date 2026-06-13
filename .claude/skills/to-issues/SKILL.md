---
name: to-issues
description: Projects the frozen slice ledger onto GitHub — one parent issue per slice, sub-issues per work unit, blocked-by edges from depends_on and shared_surfaces — and persists the ticket map (.claude/tickets/<feature_slug>.json). Use after slicing is frozen. Does NOT slice, re-scope, or write code; re-runs reconcile, never duplicate. Hands off to to-code.
---

This skill is the **projector**: it fuses the frozen slice ledger and the PRD onto a GitHub issue tree a coding agent can execute. Slices come from the slice ledger; prose context comes from the PRD. Inherit both verbatim and make no scope, slice, or design decisions. If you find yourself deriving or re-cutting a slice here, that is a `to-slices` decision leaking into Phase 4 — stop and kick back. (`grill-discovery` decides what the feature is, `to-prd` writes the prose, `to-slices` cuts the slices and decides their serialization — including file overlap — and this skill projects them.)

## Inputs

**Load always:**
- `.claude/slices/<feature_slug>.json` — the frozen slice ledger. `decomposition[]` is the **only source of slices**: `slice_id`, `slice_name`, `intent`, `layers`, `scope`, `acceptance`, `depends_on`, `touched_files`, and `shared_surfaces`. The `shared_surfaces` already include file-level `modifies`/`modifies` entries (the `surface` is a file path) for any `touched_files` write-overlap `to-slices` serialized — wire them like any other surface; do not re-derive overlap here.
- `.claude/prds/<feature_slug>.md` — the PRD. Source of **prose** for issue bodies (Problem/Solution, User Stories, Implementation Decisions, Testing Decisions). Carries no slices.
- `.claude/CONTEXT.md` (or `.claude/CONTEXT-MAP.md`) — the glossary. Use its vocabulary in every title and body; coin nothing.
- `.claude/tickets/<feature_slug>.json` — this skill's own ticket map, if present. Read it **first** and reconcile against it.

**Consult on demand:**
- `.claude/CLAUDE.md` — for two things only: the branch convention, and pointers to area docs for the modules a slice touches. Do not read the whole map.
- `.claude/discovery/<feature_slug>.json` — only if a body needs glossary-adjacent context (`affected_components`, ADR pointers). Not a source of slices.
- ADRs in the touched area — link from the relevant issue; do not restate.

**Stop conditions.** Ledger missing or `slice_status` ≠ `frozen` → tell the user to run/complete `to-slices`. PRD missing → tell them to run `to-prd`. Never improvise issues from chat or a half-finished pipeline.

**On entry, check for a resumable plan.** If `.claude/tickets/<feature_slug>.plan.json` exists and its `ledger_hash` matches the current frozen ledger, resume from it: if `approved`, skip drafting and go straight to write; if not, re-present it (cheap — it's loaded, not re-drafted). If the hash is stale, discard it and plan fresh.

Phase 4 does not re-validate the frozen decomposition — `to-slices` owns that, including file-overlap serialization. The only structural property it checks is acyclicity of `depends_on`, at the write step (it needs it to order edges).

## Issue anatomy (the projection)

One parent issue per slice; sub-issues per work unit. Every issue field has a fixed source — never write content that isn't traceable to one:

| Issue field | Source |
|---|---|
| Parent title | slice `slice_name`/`intent`, in glossary vocabulary |
| Parent body — scope/intent | slice `scope` + `intent` |
| Parent body — context | PRD Problem/Solution summary + the Implementation Decisions relevant to this slice, plus the PRD's repo path (`.claude/prds/<feature_slug>.md`) for the rest — not the full section pasted verbatim |
| Parent body — acceptance checklist | slice `acceptance`, verbatim |
| Parent body — dependency summary + branch | `depends_on` + `feature/<feature_slug>-<slice_id>` (per `.claude/conventions.md`) |
| Sub-issue title/scope | a work unit cut from slice `scope` across its `layers` |
| Sub-issue body | the work unit's one-or-two-line scope and its owned acceptance — **nothing else**. Context lives on the parent and in the PRD (the coding agent's checkout has both); never repeat the parent's context, scope, or PRD excerpts into sub-issues — every repeated line is paid for on every read *and* on every MCP checkbox rewrite (read-modify-write of the whole body) |
| Sub-issue acceptance | the clause(s) of slice `acceptance` it owns, framed as **testable assertions**: each clause states the observable behavior a test must assert and names the PRD test seam it traces to — the execution agent authors its red-green tests directly from this section |
| Labels | `feature:<feature_slug>`, `layer:<data\|business_logic\|api\|ui\|docs>`, + type-fallback label if needed. **Never `slice:` labels** — slice↔issue correlation lives in the ticket map; structure on GitHub is expressed through native relationships (sub-issue parenting, blocked-by), not label encoding |
| Blocked-by edges | `depends_on` + `creates`→`reads` and `modifies`/`modifies` from `shared_surfaces` — the latter covering both logical surfaces and file-level surfaces from `touched_files` overlap |

Rules:
- **Sub-issue type** is `Task` (`Bug` only when the slice is itself a fix).
- **Single-work-unit slice → no sub-issue.** When a slice's scope is one work unit, the parent alone is the ticket: it already carries the full acceptance checklist, the ticket map records an empty `sub_issues` array (the parent owns every acceptance index), and the execution agent checks criteria off on the parent. A sub-issue that near-duplicates its parent is pure token tax on every downstream read.
- **Coverage:** the union of a slice's sub-issues must cover every clause of its `acceptance` (vacuously satisfied by the parent when a slice has no sub-issues). An unowned clause means add a sub-issue — the plan is incomplete without it.
- **Testability is projection-checked:** if a clause cannot be framed as an assertion a test could verify (no observable behavior to assert), do not paper over it with vague wording — that clause will block its ticket in Phase 5. Kick back to `to-slices` to re-phrase the frozen acceptance now, while it is cheap.
- **Edges:** `reads`/`reads` surfaces get no edge — they are the parallelizable front. Every `modifies`/`modifies` surface gets a serialize edge, whether the `surface` is a logical name or a file path; a shared written file is not parallelizable.
- **Branch per slice:** record `feature/<feature_slug>-<slice_id>` on the parent (slice number as the suffix, per `.claude/conventions.md`); do not create it (the execution phase does). Slice branches carry the `feature/` prefix and PR into the accumulator (`release/<feature_slug>`); never slash-nest refs under a branch name git already holds.
- **Out of scope → no issue.** Anything not owned by a frozen slice is not a ticket.

## Plan, then confirm (the only interaction)

Build the tree in memory first — parents, sub-issues, labels, blocked-by edges, branch names. Before prompting, write it to `.claude/tickets/<feature_slug>.plan.json` (atomically, per the file-write discipline in `.claude/conventions.md`; with `approved: false` and a `ledger_hash` of the frozen ledger) so a dropped session doesn't lose the drafted bodies — the most expensive artifact, and the one the ticket map doesn't yet protect. Then present it as a dry-run preview. In harnesses where the confirmation renders as an option-box question (web/remote), present the preview as plain **chat text** before asking, and keep the question itself to a bare approve/adjust choice — a tree of any real size will not fit inside the question UI. This confirms **write architecture only**. It must not reopen scope, slices, decisions, or acceptance. If the user wants any of those changed, kick back to the phase that owns them (`to-slices` for slices/acceptance/serialization, `grill-discovery` for scope, `to-prd` for prose). On approval, flip `approved: true` in the plan file, then write.

## Write + reconcile (never blind-create)

Requires `gh` v2.94.0+; when `gh` is unavailable (remote/web environments), use the GitHub MCP equivalents per `.claude/conventions.md` — the reconcile rules are identical. For each planned item, consult the ticket map and `gh issue list`:
- missing → create it;
- present but content-changed → `gh issue edit`;
- present and unchanged → skip.

Order: create parents first, then sub-issues with `--parent`, then wire `--blocked-by` in topological order over `depends_on` once both endpoints exist. The `--blocked-by` set is the union of `depends_on` and every `modifies`/`modifies` serialization in `shared_surfaces` (logical and file-level). If `depends_on` contains a cycle, **stop** and kick back to `to-slices` — Phase 4 cannot order a cyclic `--blocked-by` graph and the ready-front would be empty. Commands and the org-level type-fallback rule: see [gh-reference.md](./gh-reference.md).

## Persist

Write `.claude/tickets/<feature_slug>.json`, keyed to the same `feature_slug` so discovery ↔ PRD ↔ slices ↔ issues all correlate. Write it atomically, per the file-write discipline in `.claude/conventions.md`. This map is what makes the GitHub state a refreshable projection rather than a one-shot dump. Pin the shape — don't improvise it per run, or reconciliation read-back drifts:

```json
{
  "feature_slug": "string",
  "last_projected_at": "ISO-8601 timestamp",
  "tickets": {
    "<slice_id>": {
      "parent_issue": 101,
      "parent_hash": "sha256 over the slice fields that determine the parent body (intent, scope, acceptance, depends_on, branch)",
      "sub_issues": [
        {
          "issue": 102,
          "owns_acceptance": [0, 2],
          "hash": "sha256 over this work unit's title + body inputs"
        }
      ]
    }
  }
}
```

`owns_acceptance` holds the **indices** into the slice's `acceptance` array this sub-issue covers (indices, not clause text — stable against rewording). The union of a slice's `owns_acceptance` must equal its full acceptance index set; that union *is* the coverage check, now verifiable on every reconciliation. An empty `sub_issues` array means the parent owns every index (single-work-unit slice). The per-item `hash` is the create/edit/skip discriminator; edges and labels reconcile separately via `gh issue list` read-back.

Then commit and push the map to `release/<feature_slug>`, per the commit conventions in `.claude/conventions.md`. Once committed, delete the now-superseded `.plan.json`.

## Guardrails
- **No code, no slicing, no re-scoping, no overlap analysis** — `to-slices` owns serialization including file overlap; the ticket map and the GitHub issues are the only artifacts this skill writes.
- **Single source of truth** — the frozen slice ledger wins; reconcile GitHub toward it, never the reverse.
- **Idempotent** — re-runs reconcile against the ticket map + `gh issue list`; they never duplicate. A re-frozen ledger (e.g. after a Phase-5 feedback-loop re-cut in `to-slices`) reconciles cleanly: new serialize edges become new `--blocked-by` edges, changed bodies get `gh issue edit`, unchanged items skip.
- **Kick back, don't patch** — broken decomposition → `to-slices`; wrong scope or prose → `grill-discovery`/`to-prd`.
- **Do not publish.**

## Exit & handoff

Done when every slice has a parent issue, every parent's sub-issues cover that slice's `acceptance` (or the parent owns it all, for a single-work-unit slice), all `depends_on` and serialization edges (logical and file-level) are wired as `--blocked-by`, and the ticket map is committed to the release branch.

Report the resulting tree and call out the **ready-to-start front**: the parents with no open `blocked-by`, workable in parallel. Then hand off to execution (`to-code`): a coding agent picks an unblocked parent, checks out `feature/<feature_slug>-<slice_id>` off the accumulator tip, implements its sub-issues, and the integrator closes each sub-issue when its acceptance checklist passes. A slice is done when all its sub-issues are closed AND their union satisfies the slice's `acceptance` (for a no-sub-issue slice, the build ledger's `integrated` status is the completion signal — there is nothing to close, and the parent stays open by doctrine). **Parent issues are never closed by the pipeline** — they stay open until the user merges the final accumulator→`main` PR, whose `Closes #<parent>` lines close them on merge.
