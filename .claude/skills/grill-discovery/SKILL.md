---
name: grill-discovery
description: Interrogates a new feature one question at a time against the codebase and glossary, then freezes the outcome into the discovery ledger (.claude/discovery/<feature_slug>.json) and projects sharpened terms into .claude/CONTEXT.md. Use when a feature, architecture change, or plan needs stress-testing before any PRD or code. Does NOT slice or ticket. Hands off to to-prd.
---

## Intent
Activate when the user introduces a new feature request, an architecture change, or says "Grill this feature" / "Run discovery."

## Objective
Act as a pragmatic, skeptical Principal Architect. Interrogate the plan to expose gaps and sharpen domain language. Freeze the result into the discovery ledger — the single machine-readable source of truth for the agent-to-agent handoff to `to-prd`. This phase settles *what the feature is*; it does **not** decompose it into slices — that is `to-slices`' job, run after the PRD exists.

## 0. Workspace Grounding (Before the First Question)
- **Resume check**: Derive `feature_slug` (kebab-case). Before minting a new slug, list `.claude/discovery/*.json`; if an existing ledger plausibly describes this same feature under a different phrasing, confirm with the user whether to resume it instead — a near-duplicate slug orphans the old ledger and splits the artifact chain. If `.claude/discovery/<feature_slug>.json` exists, load it. If `discovery_status` is `frozen` or `needs_human`, tell the user and ask whether to reopen (set `active`) or start fresh — do not clobber. If `active`, resume from it; the file, not this chat, is your memory.
- **Branch grounding**: Before the first write, ensure you are on this feature's branch per the branch convention in `.claude/conventions.md` (`release/<feature_slug>`), checking it out from `main` if needed. The ledger and glossary commit there. If the harness has pinned the session to a generated branch (e.g. `claude/<generated>`), do not silently adopt it: ask the user for explicit permission to create `release/<feature_slug>` per the session-branch clause in the conventions, and fall back to the session branch — logging the override in `assumptions` — only if declined.
- **Map-first grounding**: Read `.claude/CLAUDE.md` first — it is the project router/map, not a knowledge dump. Follow its pointers to the area or reference docs covering the modules this feature touches, then read raw source only where those docs are thin or the feature reaches code they don't cover. Do not re-explore the whole repo. If `.claude/CLAUDE.md` is missing or is not structured as a router (a fresh or unadopted codebase), offer to run `init-map` to generate it first; if declined, fall back to a *targeted* exploration of the components this feature plausibly touches and proceed — do not block on it. Set `stack_context` from what you find.
- **Glossary load**: Read `.claude/CONTEXT.md` (the domain glossary `.claude/CLAUDE.md` points to; or `.claude/CONTEXT-MAP.md` if multiple contexts exist). The terms defined there are canonical for the rest of the session.
- **Provocative restatement**: Restate the feature in one sentence with a concrete guess at what is IN vs. OUT of scope. Force the user to correct a proposition rather than answer an open-ended prompt.
- **Grounding sweep**: Immediately after the restatement, sweep every ledger in `.claude/discovery/*.json` — `resolved_decisions` questions and `explicitly_out_of_scope` entries — for the restatement's domain terms, and surface scope-level collisions with past features, including things a prior feature *deliberately scoped out*, before the first question. Exclude the current feature's own ledger from this sweep (and from all retrieval below): it is this session's memory, not prior art. A collision here is exactly what the kill switch exists for — better at minute one than after a full questioning session.

## 1. Questioning Protocol
- **One question at a time.** Provide your recommended answer with brief rationale, then wait for confirmation or correction before the next question. Never batch.
- **Recommendation is not agreement.** When the user's stated approach is weaker than the alternative, recommend the alternative and say why. Challenge vague non-functional terms ("fast", "scalable", "secure") and force engineering definitions — latency targets, throughput bounds, auth patterns, concrete limits.
- **Make the recommendation falsifiable, and invite dissent where context can overturn it.** Phrase each recommendation as a claim the user's context could disprove ("I recommend X because I found no Y — if Y exists in your domain, X is wrong"), never as a default to ratify. Option-box harnesses make the recommended option frictionless to rubber-stamp and dissent expensive — backwards for a grilling phase whose value is provoked correction — so on questions where user knowledge could overturn the analysis (scope, domain priorities), explicitly invite the free-text "Other" path. Terminal sessions don't carry this bias; web sessions do.
- **Walk the tree depth-first.** Resolve dependencies one branch at a time; do not ask a question whose answer depends on an unresolved one.
- **Codebase-first.** If a question can be answered by reading the code, read it instead of asking.
- When the user ratifies a recommendation, log it to `resolved_decisions` as `{ question, resolution }` with the *why* in the resolution — that rationale is the context downstream agents inherit.

## 2. Session Behaviors (Run Throughout)
- **Challenge against the glossary**: If a term conflicts with `.claude/CONTEXT.md`, surface it immediately. "Your glossary defines 'cancellation' as X; you seem to mean Y — which is it?"
- **Sharpen fuzzy language**: For vague or overloaded terms, propose a precise canonical term. "You said 'account' — Customer or User? Those differ."
- **Surface prior art**: Before accepting any new behaviour (comparison, mapping, transform, validation, data structure), search for code that already does something similar. If found: "There's already an X in Y — extend it, or is this genuinely different?" And before resolving any question, search past discovery ledgers' settled questions exactly the same way: "a prior feature already resolved X — adopt, or consciously diverge?" (read discipline below). Never let a session produce a second implementation of an existing concept or invisibly re-litigate a settled question; the user decides extend vs. new and adopt vs. diverge, but must be made aware of the choice.
- **Stress-test with concrete scenarios**: Probe boundaries between concepts with specific invented edge cases.

### Ledger retrieval discipline (grounding sweep & prior-art lens)
Past ledgers are the project's decision memory — retrieval over what already exists, not a registry. Reads are staged to keep that memory from becoming a context tax. Stage 1 extracts *only* the short `question` strings and `explicitly_out_of_scope` entries across every ledger, operating on the JSON structure — never line-oriented grep, since the file-write discipline validates JSON but does not mandate pretty-printing (a minified ledger must yield identical results). Run exactly:

```sh
jq -r '. as $l | ($l.resolved_decisions[]?.question, $l.explicitly_out_of_scope[]?) | "\($l.feature_slug) [\($l.discovery_status)]: \(.)"' .claude/discovery/*.json
```

Stage 2 reads a full `resolution` only for plausible hits — the questions *are* the index; never bulk-read resolutions. Revisit trigger: if question-stage output becomes noisy at scale (many dozens of ledgers), add a derived pointer index per [ADR-0002.md](./ADR-0002.md) — observed noise is the trigger, not a file count picked in advance.

Every hit is **advisory**: surface it with feature provenance and git-derived freeze order (when each ledger's freeze commit landed); on conflict, show **all** competing resolutions and recommend the most recent; flag hits from non-frozen ledgers as *provisional* — visible, but not settled. Never auto-adopt — the user adjudicates every hit, and frozen ledgers are never mutated (no supersession machinery; a stale surfaced decision costs one wasted interactive question).

When the user adopts or diverges, restate the outcome as a `resolved_decision` in the *current* feature's own ledger naming its source — "adopted from <feature_slug>: …", or naming what it diverges from and why. This is mandatory, not courtesy: `to-prd` reads only the current feature's ledger, so an unrestated adoption is invisible downstream.

## 3. Inquiry Lenses & Schema Mapping
Lead with Lens A; never open with Lens D. Move forward as gaps close, but return to an earlier lens whenever a later finding changes it (a Lens D risk often rewrites Lens A scope) and log the revision. The order governs what you ask first, not a one-way sequence.

- **Lens A — Intent & Boundaries**: Core value, the simplest path that delivers it, explicit non-goals for this phase. → `intent`, `mvp_scope`, `explicitly_out_of_scope`
- **Lens B — State & Data Flow**: Triggers, exact Definition of Done, data lifecycle (mutations, schema changes, deletions), definitive source of truth. → `definition_of_done`
- **Lens C — Architectural Coupling**: Existing code touched, replaced, or duplicated; structural breakage vectors; other features/components this requires that may not be built yet. → `affected_components`, `dependencies`
- **Lens D — Context-Appropriate Risk** (apply only the bucket fitting `stack_context`):
  - *Web/Distributed*: idempotency, API partial failures, sync lag, dropouts.
  - *Local/CLI/Data*: memory footprint, filesystem corruption, schema drift, bottlenecks.
  - *Frontend/UI*: client state desync, async race conditions, edge-case inputs.
  - → `open_risks_to_resolve`

### State transition rules
- Anything taken as true but unverified → add to `assumptions`.
- When an `open_risks_to_resolve` item or challenge is answered soundly → **remove it** from `open_risks_to_resolve` and append it to `resolved_decisions`. The open-risk list must shrink toward the exit condition; it is not append-only.

## 4. Continuous File Handoff (Deterministic Ledger)
Maintain `.claude/discovery/<feature_slug>.json`. Ensure `.claude/discovery/` exists before the first write.

### Write & validation protocol
Write the ledger atomically, per the file-write discipline in `.claude/conventions.md`. Treat the validated JSON on disk, not the chat log, as authoritative for every subsequent turn — the file, not this chat, is your memory.

### Ledger schema
```json
{
  "feature_slug": "string",
  "discovery_status": "active | ready_to_freeze | frozen | needs_human",
  "stack_context": "web | cli | data | frontend | mixed",
  "intent": "string",
  "definition_of_done": "string",
  "mvp_scope": "string",
  "explicitly_out_of_scope": ["string"],
  "affected_components": ["string"],
  "dependencies": ["string"],
  "terms_resolved": ["string"],
  "assumptions": ["string"],
  "resolved_decisions": [
    { "question": "string", "resolution": "string" }
  ],
  "open_risks_to_resolve": ["string"]
}
```

## 5. Durable Projections (Single Writer)
This skill is the only writer of these. Do not fan them out.
- **Glossary** (continuous write, commit at freeze): As terms are sharpened, project them into `.claude/CONTEXT.md` immediately, using the format in [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md) and the file-write discipline in `.claude/conventions.md` — other sessions read `.claude/CONTEXT.md` at grounding, so it must always be valid on disk. Record the term names in `terms_resolved` (definitions live in `.claude/CONTEXT.md`, never duplicated into the ledger). Writing continuously means a killed session does not lose resolved terms, mirroring the ledger's durability. Do *not* commit `.claude/CONTEXT.md` until freeze, alongside the resolved-decisions commit; if the feature is scrapped via the kill switch, the uncommitted edits stay in the working tree for the user to keep or revert. `.claude/CONTEXT.md` is a glossary only — no implementation detail — and stays in the repo rather than riding the release branch.
- **ADRs** (at freeze): Offer one *only* when all three hold — hard to reverse, surprising without context, the result of a real trade-off. Use the format in [ADR-FORMAT.md](./ADR-FORMAT.md). Skip otherwise; most decisions belong in `resolved_decisions`, not an ADR.

## Guardrails & Hard Rules
- **No implementation**: No source code, stub files, or solutions. The ledger, `.claude/CONTEXT.md`, and ADRs are the only files you write.
- **No slicing**: Do not decompose the feature into vertical slices, and do not introduce `slice_id`s. Decomposition is determined *after* the PRD, in `to-slices`. Settle scope, decisions, and risk here — nothing about how the work is partitioned for execution.
- **Kill switch**: If discovery reveals the feature is mis-shaped, splits into independent features, duplicates existing work, or belongs in another layer, say so, log it as a critical entry in `open_risks_to_resolve`, set `discovery_status` to `needs_human`, and halt for a scope decision.

## Exit & Handoff
1. When `open_risks_to_resolve` is empty AND `intent`, `definition_of_done`, and `mvp_scope` are populated, set `discovery_status` to `ready_to_freeze` and **ask the user**: "Discovery looks complete — proceed to PRD, or is there more?"
   - If the user raises a new point, set status back to `active` and continue.
2. On confirmation, set `discovery_status` to `frozen` and do the final validated ledger write.
3. Commit the frozen ledger **and** `.claude/CONTEXT.md` together, once, on the release branch, per the commit conventions in `.claude/conventions.md`. The commit subject summarises *what domain decisions were captured* (e.g. `docs(context): resolve join-source picker and scalar persistence`), under 72 chars.
4. **File the parking lot.** Out-of-scope items the user asked to track become `future-feature` issues, filed now, at freeze — one issue each, body in three sections: **the idea** (what was parked), **why it was parked, not adopted** (the decision rationale, pointing at the ledger), and **when picking this up** (the pick-up trigger). The pick-up trigger must state that resumption is **user-initiated only**: it describes the condition under which the *user* would start a new grilling session with the issue as the feature argument — it is never an instruction an agent acts on by itself. Items in `explicitly_out_of_scope` that the user did not ask to track get no issue.
5. Instruct the user to run `to-prd`, which reads this ledger by `feature_slug` and synthesizes the single release PRD as one whole. Slicing happens *after* the PRD: `to-slices` decomposes that PRD into independently testable vertical slices and runs the `mvp_scope` coverage check, then `to-issues` projects those slices onto GitHub. This phase neither slices nor tickets.
