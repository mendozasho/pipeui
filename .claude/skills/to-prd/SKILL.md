---
name: to-prd
description: Turns the frozen discovery ledger and glossary into one shareable PRD (.claude/prds/<feature_slug>.md), establishing the feature-level test seams. Use after a discovery session is frozen. Does NOT interview, re-decide, or slice. Hands off to to-slices.
---

This skill is Phase 2. It reads the **frozen** discovery ledger and the project glossary and synthesizes one PRD for the release. Its only job is faithful, refined translation — not discovery, not decomposition, not ticketing. Do NOT interview the user, and do NOT re-derive anything the ledger already settled.

## Inputs (authoritative)
- `.claude/discovery/<feature_slug>.json` — the frozen ledger. This is the source of truth; the chat log is not.
- `.claude/CLAUDE.md` — the project router/map. Follow its pointers to the glossary and the area/reference docs for the modules you touch, rather than exploring the repo blind. It points; it does not hold detail. If missing or thin, fall back to a targeted read of the ledger's `affected_components` and note the gap (`init-map` can generate the router).
- `.claude/CONTEXT.md` (or `.claude/CONTEXT-MAP.md`) — the project glossary. Use its vocabulary throughout; never coin competing terms.
- Any ADRs in the touched area — respect them.

If the ledger is missing or its `discovery_status` is not `frozen`, **stop** and tell the user to run or complete `grill-discovery` first. Do not improvise a PRD from conversation.

## Process

1. **Load, don't re-derive.** Read the ledger and glossary. Inherit `stack_context`, `affected_components`, and `dependencies`, and use them to target a focused read: follow `.claude/CLAUDE.md`'s pointers to the area/reference docs for those modules, and read raw source only where the docs are thin — never re-explore the repo from scratch. The ledger's `resolved_decisions` (each carrying its *why*), `intent`, `mvp_scope`, and `explicitly_out_of_scope` are settled — your job is to project them into PRD prose, not reopen them. If you believe a frozen decision is actually wrong, do NOT silently override it in the PRD: surface it to the user and kick it back to `grill-discovery` to reopen the ledger.

   The ledger carries **no** `decomposition` — slicing happens *after* this PRD, in `to-slices`. The PRD describes the feature as one whole; do not slice or introduce `slice_id`s here.

2. **Sketch the test seams.** Identify the seams at which the feature will be tested. Prefer existing seams to new ones; use the highest seam possible; if new seams are needed, propose them at the highest point you can. Base *what must be true* on the ledger's `definition_of_done` and the behaviour implied by `mvp_scope` — not on a decomposition (there is none yet). These feature-level seams are the foundation that the slicing phase (`to-slices`) grounds each slice's acceptance criteria on, so get them right here.

   Check the seams with the user. **This is the only allowed interaction**, and it must not reopen scope or decisions, nor introduce slicing — it confirms test architecture only.

3. **Write the PRD** using the template below, in glossary vocabulary throughout. Save it to `.claude/prds/<feature_slug>.md`, keyed to the **same `feature_slug`** as the ledger so the ledger ↔ PRD ↔ (later) slices ↔ tickets all correlate. Open it with the template's frontmatter block, setting `source_discovery_hash` to the sha256 of the frozen discovery ledger file as read (`sha256sum .claude/discovery/<feature_slug>.json`). This extends the hash chain Phases 4–5 already use (`ledger_hash`) upstream: if discovery is ever reopened and re-frozen, downstream can detect that this PRD predates the re-freeze instead of trusting slug + status alone.

4. **Persist on the release branch.** Commit and push the PRD to this feature's branch (`release/<feature_slug>`) — the same branch the ledger lives on — so it survives across sessions, per the commit conventions in `.claude/conventions.md`. Then instruct the user to run `to-slices`, which decomposes this PRD into vertical slices and runs the `mvp_scope` coverage check before any tickets exist.

### Section sourcing (where each template section comes from)
- **Problem Statement** ← `intent`, framed from the user's perspective.
- **Solution** ← `mvp_scope` + `intent`, from the user's perspective.
- **User Stories** ← synthesized fresh from `intent` and `mvp_scope`. This is the PRD's own value-add; the ledger has no user stories.
- **Implementation Decisions** ← *project* `resolved_decisions` (use the `resolution` rationale verbatim in spirit), plus `affected_components`, `dependencies`, and any ADRs. Do not invent decisions the ledger didn't settle.
- **Testing Decisions** ← the seams from step 2, grounded in `definition_of_done`. These are feature-level; per-slice acceptance is derived later by `to-slices` from these seams.
- **Out of Scope** ← `explicitly_out_of_scope`.
- **Further Notes** ← `assumptions`, plus anything else worth carrying.

<prd-template>

---
feature_slug: <feature_slug>
source_discovery_hash: <sha256 of the frozen discovery ledger file this PRD was written from>
---

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

The list must be **comprehensive but non-redundant**: every `mvp_scope` clause and every user-visible resolved decision is represented by at least one story, and no story merely rephrases another. Coverage is the goal, not length — downstream phases (slicing, ticketing, coding agents) read every story, so padding costs tokens at every later phase and dilutes the signal.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- The feature-level test seams (the seams from step 2)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this PRD.

## Further Notes

Any further notes about the feature.

</prd-template>
