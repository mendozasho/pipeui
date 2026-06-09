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
- **Three-layer minimum:** every slice must touch all three integration layers — **Data Layer** (workflow/schema/DuckDB), **Business Layer** (API/routes), and **UI Layer** (frontend). A slice missing any of these three is a horizontal slice, not a vertical one — challenge it and expand it before presenting. Tests count as part of whichever layer they exercise, not as a separate layer.
- **Context load check:** before presenting a slice, estimate whether a single agent can hold all its changes in context without making trade-offs. If a slice touches more than ~4 files across layers, consider splitting it into a thinner slice that still hits all three layers (e.g. a walking skeleton first, then enrich with detail in a follow-on slice).
- **File-overlap check:** before marking two slices as parallel, list the files each will touch. If any file appears in both lists, those slices are NOT safe to run in parallel — add a dependency between them even if there is no logical blocker. Parallel execution on overlapping files causes merge conflicts that require manual resolution.
</vertical-slice-rules>

### 4. Quiz the user

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

### 5. Publish the issues to the issue tracker

For each approved slice, publish a new issue to the issue tracker. Use the issue body template below. These issues are considered ready for AFK agents, so publish them with the `ready-for-agent` label.

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
- Use `git commit -m "subject" -m "body"` with repeated `-m` flags for multi-paragraph messages. Do NOT use heredoc (`<<'EOF'`) syntax — it does not evaluate correctly in all shell contexts and will corrupt the commit message.
- **Before opening a PR, go through every acceptance criterion in this issue one by one and verify each is met.** For each criterion: state what you built that satisfies it, and confirm it works (run the relevant test, or describe the observable behaviour). Do not open the PR until every criterion is checked off. If a criterion cannot be met, surface it explicitly rather than skipping it.
- When all acceptance criteria are met and tests pass, open a pull request with `Closes #<this-issue-number>` in the PR body so the issue is auto-closed on merge.

</issue-template>

Do NOT create a separate parent PRD issue on GitHub.
