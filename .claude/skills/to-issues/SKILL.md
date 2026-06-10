---
name: to-issues
description: Break a plan, spec, or PRD into independently-grabbable issues on the project issue tracker using tracer-bullet vertical slices. Use when user wants to convert a plan into issues, create implementation tickets, or break down work into issues.
---

# To Issues

Break a plan into independently-grabbable issues using vertical slices (tracer bullets).

## Process

### 1. Gather context

Work from whatever is already in the conversation context. If the user passes a local PRD path or issue reference as an argument, read it in full. The PRD is the source of truth for design decisions — each issue should carry the relevant decisions for its slice, not just a pointer to the PRD.

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so to understand the current state of the code. Issue titles and descriptions should use the project's domain glossary vocabulary, and respect ADRs in the area you're touching.

### 3. Draft vertical slices

Break the plan into **tracer bullet** issues. Each issue is a thin vertical slice that cuts through ALL integration layers end-to-end, NOT a horizontal slice of one layer.

Slices may be 'HITL' or 'AFK'. HITL slices require human interaction, such as an architectural decision or a design review. AFK slices can be implemented and merged without human interaction. Prefer AFK over HITL where possible.

<vertical-slice-rules>
- Each slice delivers a narrow but COMPLETE path through every layer (schema, API, UI, tests)
- A completed slice is demoable or verifiable on its own
- Prefer many thin slices over few thick ones

- **Three-layer minimum — non-negotiable:** every slice MUST touch all three integration layers — **Data Layer** (workflow/schema/DuckDB), **Business Layer** (API/routes), and **UI Layer** (frontend). A slice missing any layer is a horizontal slice and must be rejected. Do not present it to the user. Instead, expand it to cover the missing layer before presenting. Tests count as part of whichever layer they exercise, not as a separate layer.

  **How to expand a thin slice:** if a proposed slice only touches one or two layers, look at what the missing layer would minimally need to contribute. For a backend-only slice, ask: what is the thinnest UI that makes this backend change observable? For a frontend-only slice, ask: does this UI change require a new API contract or schema field? Add that to the slice. A walking skeleton (stub endpoint + wired UI) counts — it does not need to be production-quality, just end-to-end.

  **Vertical over parallel — always:** a set of sequential vertical slices is always preferable to a set of parallel horizontal slices. Do not trade verticality for parallelism. Parallelism is a secondary optimisation only considered after every slice is already vertical.

- **Context load check:** before presenting a slice, estimate whether a single agent can hold all its changes in context without making trade-offs. If a slice touches more than ~4 files across layers, consider splitting it into a thinner slice that still hits all three layers (e.g. a walking skeleton first, then enrich with detail in a follow-on slice).

- **File-overlap check:** before marking two slices as parallel, list the files each will touch. If any file appears in both lists, those slices are NOT safe to run in parallel — add a dependency between them even if there is no logical blocker. Parallel execution on overlapping files causes merge conflicts that require manual resolution. Remember: sequential vertical slices are always preferred over parallel horizontal ones — only mark slices parallel when they are both vertical AND have no file overlap.
</vertical-slice-rules>

### 4. Handle design-gated slices

Some slices cannot be fully spec'd without visual design decisions — modal layouts, component interactions, new UI primitives. These are **design-gated** (HITL). Flag them with `[Design-gated]` in the title and label them `blocked-on-design`.

**What makes a slice design-gated:** it introduces a new multi-step modal, a new interactive primitive (toggle, segmented control, drag behaviour), or a new screen layout — anything where the "correct" spec depends on visual composition that prose alone cannot resolve.

**The issue body is the handoff to Claude Design.** The human takes the issue to Claude Design; Claude Design makes all visual and UX decisions (layout, component anatomy, interaction model, transitions). I do not make design decisions — my job is to write the issue with enough domain context that Claude Design can work from it cold.

The issue body must include:

- **`## Context for the Design agent`** — everything Claude Design needs:
  - Which screen file and component this lives in, and what triggers it
  - The domain model — data shapes, field names, semantics — using canonical names from `CONTEXT.md`
  - The existing patterns the new UI must fit (modal chrome, drag payload format, component names from `ui.jsx`)
  - **What to produce** — a numbered list of specific decisions Claude Design must resolve (layout, component breakdown, interaction behaviour, transitions)
  - **Out of scope** — anything explicitly deferred

- **Acceptance criteria** — checkboxes an implementing agent can verify. Start with `Design assets / spec attached to this issue.`

**Label and blocker:** publish with the `blocked-on-design` label — NOT `ready-for-agent`. Set `## Blocked by` to `- Claude Design pass (this issue)` plus any implementation prerequisites.

When the human brings back the Claude Design brief, I extract the spec decisions and update the issue body directly. The label then changes to `ready-for-agent`. Do not start implementation until that happens.

### 5. Quiz the user

Present the proposed breakdown as a numbered list. For each slice, show:

- **Title**: short descriptive name
- **Type**: HITL / AFK
- **Layers touched**: always list all three — `Data Layer | Business Layer | UI Layer` — plus `tests`. Call out explicitly what each layer contributes. If a layer has nothing to contribute for this slice, that is a red flag — challenge the slice design.
- **Blocked by**: which other slices (if any) must complete first
- **User stories covered**: which user stories this addresses (if the source material has them)

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the dependency relationships correct?
- Should any slices be merged or split further?
- Are the correct slices marked as HITL and AFK?

Iterate until the user approves the breakdown.

### 6. Publish the issues to the issue tracker

For each approved slice, publish a new issue to the issue tracker. Use the issue body template below.

- **AFK slices** — label `ready-for-agent`.
- **Design-gated slices** — label `blocked-on-design`. Do NOT add `ready-for-agent` until Claude Design has delivered the spec and the issue body has been updated.

Publish issues in dependency order (blockers first) so you can reference real issue identifiers in the "Blocked by" field.

**After publishing, identify which slices can be implemented in parallel and which are blocked.** Two conditions must BOTH be true for slices to run in parallel:
1. No blocker relationship between them (one does not depend on the other's output)
2. No file overlap — they do not modify any of the same files

If either condition fails, the slices must run sequentially. Present a concise execution plan:

- Truly parallel slices (no blocker AND no file overlap): launch as parallel agents immediately (one Agent call per issue in a single message).
- Sequential or blocked slices: surface them explicitly and **wait for user approval** before starting each one. Do not start a blocked slice until all its blockers are merged and the user has confirmed the next step.

**Each issue must be self-contained.** The issue body carries the design decisions relevant to that slice directly — not a pointer to a separate PRD issue. The reviewer should be able to understand what was decided and why by reading the issue alone.

<issue-template>
## Branch

`feat/<short-slug>` — the branch the implementing agent creates and pushes to.

## What to build

A concise description of this vertical slice. Describe the end-to-end behavior, not layer-by-layer implementation.

Avoid specific file paths or code snippets — they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it here and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## Design decisions for this slice

The relevant design choices from the PRD that apply to this slice — schema decisions, API contracts, classification rules, UI behaviour, transaction boundaries, etc. Carry enough context that a reviewer reading only this issue can understand what was decided and why, without needing to look elsewhere.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Blocked by

- A reference to the blocking ticket (if any)

Or "None - can start immediately" if no blockers.

## Implementation notes for the agent

- Work on the branch listed above.
- **Before creating your branch, pull the latest main:** `git fetch origin main && git checkout main && git pull origin main`, then `git checkout -b <branch>`. This ensures you are building on top of all previously merged work.
- **After creating your branch, rebase onto the latest main before writing any code:** `git fetch origin main && git rebase origin/main`. This is critical when your slice runs after parallel predecessors — other branches may have merged into main between when this issue was created and when you start work. Skipping this step causes merge conflicts that require manual resolution.
- Use `git commit -m "subject" -m "body"` with repeated `-m` flags for multi-paragraph messages. Do NOT use heredoc (`<<'EOF'`) syntax — it does not evaluate correctly in all shell contexts and will corrupt the commit message.
- **Before opening a PR, go through every acceptance criterion in this issue one by one and verify each is met.** For each criterion: state what you built that satisfies it, and confirm it works (run the relevant test, or describe the observable behaviour). Do not open the PR until every criterion is checked off. If a criterion cannot be met, surface it explicitly rather than skipping it.
- **Check off each acceptance criterion on the GitHub issue as you confirm it.** Use the `mcp__github__issue_write` tool with `method: "update"` to update the issue body, replacing `- [ ]` with `- [x]` for each criterion you have verified. Do this before opening the PR so the issue reflects the confirmed state.
- When all acceptance criteria are met and tests pass, open a pull request with `Closes #<this-issue-number>` in the PR body so the issue is auto-closed on merge.
- **After opening the PR, verify it is actually open.** Use `mcp__github__list_pull_requests` with `state: "open"` and confirm your PR appears in the list. A force-push or history rewrite can silently auto-close a PR — if your PR is missing or closed, re-open it before ending your turn. Do not end your turn without confirming the PR is visible and open.

</issue-template>

Do NOT create a separate parent PRD issue on GitHub.
