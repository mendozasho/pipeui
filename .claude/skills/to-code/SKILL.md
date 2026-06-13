---
name: to-code
description: The execution + integration engine. Dispatches coding agents against the unblocked front of the GitHub issue tree in isolated checkouts off the accumulator branch — each agent works test-driven, red-green-refactor per acceptance criterion — then sequentially integrates each slice branch and opens exactly one human-review PR. Use after to-issues has projected the frozen slices. Does NOT slice or analyze overlap; kicks conflicts back to to-slices, and blocks (never implements around) tickets that cannot be tested.
---

This skill is the **executor + integrator**. Phases 1-4 settled scope (`grill-discovery`), wrote the PRD (`to-prd`), cut vertical slices (`to-slices`), and projected them onto a GitHub issue tree with `--blocked-by` edges (`to-issues`). This skill turns that tree into merged code on the Accumulator Branch and one pull request. It makes **no** scope, slice, design, or overlap decision — those are upstream. If the graph is wrong, kick back to the phase that owns it.

It is built to defeat two failure modes:
- **Branch proliferation** — slice branches are ephemeral: cut off the accumulator tip, deleted the instant they integrate. The only durable branch is the accumulator.
- **Merge conflicts** — parallelism is constrained entirely by the blocked-by graph, which Phases 3-4 build to serialize both logical coupling (`shared_surfaces`) and physical coupling (`touched_files`). Phase 5 does no overlap analysis of its own. Integration is **sequential and rebase-onto-tip**, so the accumulator is always green and any conflict is a 2-way against a known-good base — never an N-way pile-up, and never the silent "two green PRs merge a red accumulator" race.

## Inputs
- `.claude/slices/<feature_slug>.json` — the frozen slice ledger. The **only** source of slices, `acceptance`, `depends_on`, `shared_surfaces`, and `touched_files`. Stop if missing or `slice_status` != `frozen` -> run/complete `to-slices`.
- `.claude/tickets/<feature_slug>.json` — the ticket map from Phase 4. Maps each `slice_id` to its parent issue, sub-issues, and the `acceptance` indices each sub-issue owns. Stop if missing -> run `to-issues`.
- `gh issue list` — the live tracker state; the `--blocked-by` graph is the ready-front authority.
- `.claude/CONTEXT.md` — the glossary. Use its vocabulary in branch slugs, commit subjects, and PR prose.
- `.claude/conventions.md` — branch convention, commit/file-write discipline, and the gh-vs-MCP tooling fallback. The `gh` commands below assume the CLI; without it, use the MCP equivalents listed there.
- `.claude/CLAUDE.md` — pointers to the area docs an agent must read for the modules its slice touches. If missing or thin, agents fall back to the PRD and the issue body.
- `.claude/runs/<feature_slug>.json` — this skill's own build ledger, if present. Read it **first** and resume.

**Stop condition — cycle.** If `depends_on` (or the blocked-by graph) contains a cycle, **stop** and kick back to `to-slices`. A cyclic graph has no ready front and cannot be ordered.

## Build ledger (resumability)

Maintain `.claude/runs/<feature_slug>.json` — the build ledger, sibling of the per-slice build records in `.claude/runs/<feature_slug>/` — keyed to the same `feature_slug` as every upstream artifact, written atomically per `.claude/conventions.md`. GitHub is durable, but it cannot cleanly express *integration order*, *in-flight*, or *failed* — the ledger does, and a resumed run reconciles it against `gh issue list` (all of a slice's sub-issues closed = integrated; parent issues stay open by design). Disk, not the chat log, is memory.

```json
{
  "feature_slug": "string",
  "ledger_hash": "sha256 of the frozen slice ledger this run is bound to",
  "accumulator_branch": "release/<feature_slug>",
  "current_round": 0,
  "slices": {
    "<slice_id>": {
      "status": "pending | in_progress | pushed | integrating | integrated | failed | blocked",
      "branch": "feature/<feature_slug>-<slice_id>",
      "workspace": "isolated checkout path/handle or null",
      "pr": 0,
      "integrated_at": "ISO-8601 or null",
      "note": "free text — failure reason, conflict summary"
    }
  },
  "integration_order": ["<slice_id>"]
}
```

On entry: if `ledger_hash` matches the current frozen ledger, resume (skip integrated slices, recompute the ready front). If it is stale, the ledger changed under a partial run — **stop** and surface it; do not re-plan against new slices while old branches exist.

## The orchestration loop

Run until every **sub-issue** in the ticket map is closed and every slice is `integrated` (or explicitly `failed`/`blocked`). Parent issues stay open throughout — they close only when the user merges the final PR.

### 0. Environment preflight (once per run)
Settle the environment before any branch or dispatch, so its quirks become inputs to the run instead of per-agent discoveries:
- **Announce the GitHub mode** (`gh` vs MCP fallback) and what degrades, per `.claude/conventions.md` — the user decides *now*, not mid-round, whether to rerun this phase from a `gh`-capable machine.
- **Probe `.claude/runs/` writability**: create and remove a scratch file under `.claude/runs/<feature_slug>/` in this checkout. If the environment denies it, every agent's sandbox will too — pass "write the build record at the checkout root as `BUILD-RECORD-<slice_id>.md`" as a known environment quirk in every dispatch prompt (step 3's quirks slot), so the fallback fires by instruction once instead of being rediscovered per agent.
- **Probe remote-ref permissions**: step 1's accumulator push is the first natural probe; on a repo with branch protections, also confirm before round one that `feature/*` branches accept `--force-with-lease` pushes and PRs into `release/<feature_slug>` can squash-merge — a protection blocking either strands integration mid-round, far costlier than checking now. Confirm issue-write rights (labels, close) on first contact with the tracker in step 2.

A failed probe is an environment problem for the user — report it and stop before dispatching; never implement around it.

### 1. Prepare the Accumulator Branch
Ensure `release/<feature_slug>` exists; if not, branch it from `main` and push. If `main` has moved substantially since it was cut, rebase the accumulator onto `main` **now**, before any slice work. **All slices base on the accumulator, never on `main`.**

### 2. Synchronize the ready front
Query GitHub for open issues labelled `feature:<feature_slug>` with zero open `--blocked-by` edges, **excluding any issue labelled `blocked`** (a manual stop set when a ticket cannot be tested — see step 3; it stays out of every round until a human or an upstream re-freeze clears it). This is the ready front — trust it. Because Phases 3-4 wire blocked-by edges from `depends_on`, logical `shared_surfaces`, AND `touched_files` overlap, the front is already free of both logical and physical conflicts. Phase 5 does **not** re-analyze overlaps; that work belongs upstream and is done. A **dispatch round** is the ready front (or as much of it as parallel capacity allows).

Per the pipeline's standing bias, prefer sequential vertical slices; parallelism is a secondary optimization. A single-slice round is the normal, healthy case for a tightly-coupled feature — that is the graph telling you the slices genuinely serialize, not a failure.

### 3. Dispatch agents (parallel, isolated checkouts)
For each slice in the round, give the agent its **own isolated checkout at the accumulator tip**, then launch it. Isolation is a hard requirement and is environment-dependent — pick the mechanism that fits the runtime, but never share a working directory between parallel agents (a shared checkout's `git checkout -b` switches the whole tree and lets agents clobber each other):
- **Co-located agents (same machine/filesystem):** a git worktree is cheapest — `git worktree add ../wt-<slice_id> -b feature/<feature_slug>-<slice_id> release/<feature_slug>` — it shares the object store and needs no re-clone.
- **Containerized / cloud / distributed agents:** a fresh `git clone` (or a per-agent container workspace) checked out to `feature/<feature_slug>-<slice_id>` off the accumulator tip. Fully portable; no shared filesystem assumed.

Record the workspace handle and set `status: in_progress`. When the round has more than one slice, include in each agent's prompt a **sibling manifest**: the predicted `touched_files` of every other in-flight slice (straight from the slice ledger) — it powers the plan gate below.

**Dispatch prompts are parameters only.** The agent contract below ships in the agent's checkout (this file, `.claude/skills/to-code/SKILL.md`); the dispatch prompt passes only: the issue number(s) and `slice_id`, the workspace path, the branch, the sibling manifest, any environment quirks learned earlier in the run (sandbox denials, signing workarounds), and the instruction to follow the agent contract in `.claude/skills/to-code/SKILL.md`. Never restate issue bodies, acceptance criteria, PRD content, or glossary entries into the prompt — the agent reads those from its ticket and checkout, and every restated line is paid for twice (once in the prompt, once when the agent reads the original anyway).

<agent-prompt>
You are an execution agent for GitHub Issue #<issue-number> (slice <slice_id>).

Work ONLY inside the isolated checkout you were given, on branch `feature/<feature_slug>-<slice_id>` based on the accumulator tip. Do not touch `main` or the accumulator branch.

Plan (BEFORE writing code):
- Write your build record at `.claude/runs/<feature_slug>/<slice_id>.md` using the template the skill defines. Fill in the **Plan** section: the approach you intend to take per layer, the existing utilities/constants/patterns you will reuse (from the issue's implementation notes), the **test command** you will run red-green cycles with (from `.claude/CLAUDE.md`'s build/test line or the manifests; a self-contained test file run directly is acceptable when no framework exists), and `planned_touched_files` — every file you expect to create or modify, test files included.
- Commit the build record first (`docs(run): plan slice <slice_id>`). This guarantees your intent is on the branch even if implementation is interrupted.
- **Plan gate (only when you were given a sibling manifest):** check `planned_touched_files` against it. If your plan needs to WRITE a file that an in-flight sibling's set claims, STOP: push the branch with just the plan commit, report the overlapping file(s) and sibling, and do not implement. The prediction that scheduled you in parallel was wrong; halting now is far cheaper than the merge conflict later. Read-only use of a sibling's file is fine.

Implementation (test-driven — MANDATORY):
- The issue body is your source of truth — domain context, decisions, acceptance criteria. Read your parent issue and each of your sub-issues **once**, at planning, and work from your build record afterwards — you never write to the tickets; the integrator owns checkbox updates and closures at merge. Your checkout also contains the full PRD (`.claude/prds/<feature_slug>.md`), the glossary (`.claude/CONTEXT.md`), and the slice ledger (`.claude/slices/<feature_slug>.json`); read the PRD sections relevant to your slice when the issue body's excerpt is not enough — it is the authoritative prose. Follow `.claude/CLAUDE.md` routing and `.claude/CONTEXT.md` naming.
- Work the slice's sub-issues in sequence, climbing its layers (data -> business -> api -> ui as the issue requires). Within each sub-issue, take its acceptance criteria **one at a time**, and for each criterion run a strict red-green-refactor cycle. Do not write implementation code for a criterion before its confirmed red:
  1. **Red.** Author a test asserting the observable behavior the criterion describes — never implementation internals. Run it and confirm it fails **for the expected reason** (the behavior is missing), not incidentally (typo, import error, missing fixture). Capture the actual error output; it goes in the build record.
  2. **Green.** Write the minimum implementation that makes the test pass. Re-run; debug until green. Capture the passing output.
  3. **Refactor — own diff ONLY.** With the tests green, clean up what you just wrote: naming, duplication you introduced, structure. Do NOT refactor surrounding code unless required to make your slice work — that prohibition is unchanged by this loop. Re-run the tests after.
- Reuse the utilities/constants/patterns named in the issue's implementation notes — do not reinvent them (this applies to test helpers too).
- **Cannot test? STOP — do not implement around it.** Two cases, same mechanics (commit the build record with what you have, push the branch, report) but different causes — name which one:
  - *Untestable criterion*: you cannot author a test because the criterion has no observable behavior to assert. The ticket is defective; the fix belongs upstream.
  - *Unexecutable test*: you authored the test but nothing in the checkout can run it (no runner, no toolchain target). Leave the authored test committed — it is the precise statement of intent for whoever fixes the infrastructure.

Record + verify (BEFORE pushing):
- Fill in the build record's **Actual** section: `actual_touched_files` (what you really created/modified), any deviation from the plan and why, and — critically — any file in `actual_touched_files` that was NOT in `planned_touched_files` or in the issue's `touched_files`. An unanticipated file is the signal that the upstream prediction was incomplete; flag it explicitly so the integrator can route it back to Phase 3.
- Run the **full set of tests you authored** one final time — a late criterion's implementation can break an early one's. All green is the bar.
- Record, per criterion, the test (file + case name), the red error excerpt, and the green output excerpt in the build record's **Acceptance** section. Do NOT touch the GitHub checkboxes — the integrator checks criteria off at merge against its own verification (your build record is your claim; the checkoff is the integrator's attestation). A criterion without a green authored test is not met — do not declare done; surface it.
- Commit the completed build record with your code (`git commit -m "subject" -m "body"`, repeated `-m`, no heredoc).
- If the environment denies writing under `.claude/runs/` (sandboxed checkouts do this), write the record instead at the **checkout root** as `BUILD-RECORD-<slice_id>.md`, commit it with your code, and say so in your final report — the integrator moves it to the canonical path before merge. Committed-but-misplaced beats transcribed: never deliver the record only in chat unless you cannot commit it at all.
- Push your branch: `git push origin feature/<feature_slug>-<slice_id>`.
- **STOP. Do NOT open a PR and do NOT merge.** The integrator opens the slice PR, lets CI run, rebases, and merges. A self-opened PR or self-merge races other agents and can land a broken accumulator.
</agent-prompt>

Set `status: pushed` when the agent returns. If it returns **halted at the plan gate**, set `status: failed` with a `plan_gate` note naming the overlapping files and sibling, leave its plan-only branch for inspection, and treat it exactly like a substantive integration conflict (step 4.4): kick the planned-vs-predicted diff back to `to-slices`. The sibling that holds the file keeps running — only the gated slice stops. The gate is an early exit, not a new analysis pass: the merge conflict in step 4 remains the backstop for whatever planning didn't foresee.

If it returns **halted because it cannot test** (step 3's stop rule), set `status: blocked` in the build ledger with the agent's reason, and apply the **`blocked` label** to the affected sub-issue(s) and parent so no future round picks them up (step 2 excludes them). Then route by cause:
- *Untestable criterion* → the defect is upstream: kick back to `to-slices` to re-phrase the frozen acceptance clause (or to `to-issues` if the ledger clause was fine and only the projection mangled it). After the re-freeze/re-projection, remove the `blocked` label.
- *Unexecutable test* → no phase owns the repo's toolchain: report it to the user with the attempted command and leave the ticket blocked until a human resolves the infrastructure. The authored test on the branch is their starting point.

#### Build record (planned vs. actual)
Each agent commits a build record at `.claude/runs/<feature_slug>/<slice_id>.md` on its branch. Because it is committed with the code, it squash-merges into the accumulator and survives the slice branch's deletion — so after the run, `.claude/runs/<feature_slug>/` holds one record per slice: a complete, reviewable build log the final PR reviewer reads alongside the diff. The path is deterministic, so the ledger stores nothing extra to find it. The directory is **transient by doctrine**: it stays in the tree only until `harvest-hubs` folds the records into the hub registry; harvest then archives each record to its slice's ticket and removes the directory, leaving only the build ledger (`.claude/runs/<feature_slug>.json`) at rest. Never archive or delete records by hand — harvest always runs before archival.

```markdown
# Slice <slice_id> — <slice_name>
Issue: #<issue-number>   Branch: feature/<feature_slug>-<slice_id>

## Plan (written before coding)
- Approach per layer (data / business / api / ui):
- Utilities/patterns reused (file + symbol):
- Test command (for red-green cycles):
- planned_touched_files: [ ... ]

## Actual (written before push)
- What was built (1-3 sentences):
- actual_touched_files: [ ... ]
- Deviations from plan (and why):
- Unanticipated files (in actual but not planned or issue touched_files): [ ... ]   ← Phase-3 feedback

## Acceptance (one entry per criterion — red-green evidence)
- [criterion] -> test: <file :: case> | red: <error excerpt, failing for the expected reason> | green: <passing output excerpt>

## Follow-ups / debt
- ...
```

The `actual_touched_files` list and the **Unanticipated files** line are the correction signal for Phase 3: they are the ground truth that the predicted `touched_files` is measured against, whether or not a conflict ever occurs.

**Fallback — agent could not write under `.claude/runs/`.** Primary path: the agent committed the record at its checkout root as `BUILD-RECORD-<slice_id>.md`; the **integrator** `git mv`s it to the canonical `.claude/runs/<feature_slug>/<slice_id>.md` in a follow-up commit on the slice branch — one transit, no transcription — and **independently re-runs the slice's full authored test suite** before accepting the work (agent-reported greens are never accepted without that re-run). Only when the agent could not commit the record at all does the transcription protocol apply: the integrator writes the file from the agent's verbatim report, adds a `Note:` line in the Actual section marking the substitution, and never fills gaps from its own judgment.

### 4. Integrate (sequential — the correctness invariant)
The integrator is the single writer to the accumulator. It overlaps CI but serializes merges:

1. **Fan CI out.** For every pushed branch, open a PR against the accumulator (`gh pr create --base release/<feature_slug> --head feature/<feature_slug>-<slice_id> --body "Slice <slice_id>: #<parent-issue>"`). Reference the issue for traceability, but do not rely on `Closes #` here — closing keywords never fire on a non-default-branch merge, and parent issues must stay open regardless. Opening all of them triggers CI in parallel. Record the PR number.
2. **Merge one at a time**, in dependency order. For each slice:
   - **Fast path** — if the graph records no edge (logical or `touched_files`) between this slice and any already-integrated sibling in this round, they are disjoint by construction and the slice's existing green CI still holds against the tip. Squash-merge it. (This reads the graph; it is not Phase-5 analysis.)
   - **Otherwise** — rebase the branch onto the current accumulator tip (`git rebase origin/release/<feature_slug>`), force-push (`--force-with-lease`), let CI re-run against the rebased commit, and squash-merge only when green. Re-running here catches a sibling that passed in isolation but breaks once merged.
   - Where the repo has **no CI on PRs**, the integrator runs the slice's `acceptance` + the feature-level smoke seams locally against the rebased tree as the gate.
3. On merge: verify the build record's **Acceptance** section first — every criterion needs its authored test named with red and green evidence (the slice PR's CI re-running those committed tests is the backstop a fabricated green cannot survive). Then **check off and close**: on each sub-issue (the parent, for a no-sub-issue slice), tick exactly the criteria whose authored tests passed the integrator's own re-run/CI — the checkoff is the integrator's attestation, done once per issue at close, never by agents mid-run — and **explicitly close the slice's sub-issues** (`gh issue close` / `mcp__github__issue_write`) — GitHub's `Closes #` keywords only fire on merges into the *default* branch, so nothing auto-closes here. **Never close the parent issue**: the parent is the user's review surface for the whole slice and closes only when the user merges the final accumulator→`main` PR (whose `Closes #<parent>` lines fire then, because that PR targets the default branch). Then **archive the slice's test evidence to its ticket**: post one short comment on the owning sub-issue (the parent, for a no-sub-issue slice) carrying (a) what was built and **why it was built that way** — 2–3 sentences drawn from the build record's Plan/Actual, so the ticket explains its own implementation; (b) the per-criterion red/green outcome and final pass count; and (c) a **git reference** — the merge commit SHA plus the test file's path at that commit — where the full test text lives forever in history. **Never paste the test body into the comment**: the red-green workflow having run (and the integrator having re-run it) is the evidence; the pass count and the reference reproduce everything else on demand. Then delete the file from the accumulator in an immediate follow-up commit (`chore(evidence): archive slice <slice_id> tests to #<issue>`). Authored tests are slice-scoped evidence, not automatically a permanent suite — the archive-vs-promote default comes from `.claude/conventions.md`'s test-evidence section. Under the archive default, a test worth re-running indefinitely (it asserts stable behavior, not phrasing) is instead **promoted** into the repo's permanent test suite in that same commit; under a project's promote-by-default, the polarity flips — promote unless phrasing-bound, archive as the exception. Either way the choice is named in the build record's Follow-ups. Then **delete the branch and tear down the workspace** (`gh pr merge --squash --delete-branch`; remove the worktree/clone). If the environment denies remote branch deletion (e.g. a git-proxy 403), record it in the slice's ledger `note` and continue — never block integration on teardown — but the lingering branch is the **integrator's debt**, not the user's: retry every denied deletion at step 6, and whatever still cannot be deleted must be named in the final report **and** in the single human PR's body as one paste-ready cleanup line (`git push origin --delete <branch> <branch> ...`). Append to `integration_order`, set `status: integrated`, write the ledger.
4. **Conflict at integration = the graph was wrong.** A clean graph should make rebases conflict-free; a conflict means the upstream `touched_files` prediction missed a real overlap.
   - *Trivial/textual* (e.g. two slices appended to the same manifest or registry): the integrator resolves it mechanically — that is integration housekeeping, not a product decision.
   - *Substantive* (overlapping logic, divergent edits to the same function): do NOT hand-merge product logic. Set `status: failed` with a conflict note, leave the branch for inspection, and **kick back to `to-slices`** with the two slices' `actual_touched_files` (from their build records) versus their predicted `touched_files` — that diff is the precise fix the cut needs so the graph serializes this pair next time. Patching it silently in Phase 5 would hide a recurring cut error.

Integration is **never parallel**. Serializing the merge step is what prevents two independently-green PRs from landing a red accumulator — the line that separates this design from a self-merging swarm.

### 5. Re-evaluate
After integrating, recompute the ready front — slices whose `depends_on` is now fully integrated become available. Return to step 3. A slice whose blocker `failed` stays `blocked`; report it rather than starting it. Rebasing happens only at the integration boundary in step 4, so running agents are never disturbed (no mid-flight interrupts).

### 6. Open the single human PR
When every slice is `integrated` (or explicitly `failed`/`blocked` and acknowledged), rebase the accumulator onto the latest `main` once more, then open exactly **one** PR: `release/<feature_slug>` -> `main`, with a `Closes #<parent>` line per integrated slice — the parent issues are still open by design, and the user's merge of this PR is what closes them. The body carries a **`## How to verify`** section: a checkbox list unioning every slice's `acceptance`, each item a concrete action a reviewer takes in the running app. Then confirm the PR is actually open (`gh pr list --state open`) — a force-push during integration can silently auto-close it. Re-open if missing. Do not end the turn without one open PR.

## Guardrails
- **No slicing, no re-scoping, no overlap analysis.** Phase 5 executes; it does not analyze. It writes code (via agents), the build ledger, and one human PR. What runs in parallel is dictated by the graph, not re-derived here.
- **Parent issues belong to the user.** The pipeline closes sub-issues only; parents close via the final PR's `Closes #` lines when the user merges it.
- **Test-driven, never test-skipped.** Every acceptance criterion gets an authored test with recorded red and green runs. A ticket that cannot be tested is `blocked` and routed — it is never implemented around, and description-only verification does not exist. Authored tests are slice-scoped evidence: archived or promoted at integration per the test-evidence default in `.claude/conventions.md`.
- **Single source of truth.** The frozen slice ledger + ticket map govern; reconcile GitHub toward them, never the reverse.
- **Integration is sequential, always.** Parallelize execution and CI, never the merge. No self-merging agents.
- **Trust the graph for scheduling; never blind-merge.** The blocked-by graph decides the ready front. Merges are still rebased and verified one at a time — trust governs *what runs together*, not *whether to check before merging*.
- **Isolated checkouts, ephemeral branches.** Every parallel agent gets its own checkout (worktree if co-located, clone/container if distributed); never a shared working directory. Branches are deleted on integrate; the accumulator is the only durable branch.
- **Kick back, don't patch.** Substantive integration conflict -> `to-slices` (fix `touched_files`/cut). Wrong acceptance -> `to-slices`. Wrong scope/prose -> `grill-discovery`/`to-prd`.
- **Idempotent / resumable.** Re-runs read the build ledger + `gh` state and skip integrated slices; they never re-branch or re-merge an integrated slice.

## Exit & handoff
Done when every sub-issue in the ticket map is closed, every slice is `integrated` (or explicitly `failed`/`blocked` and acknowledged), the accumulator is green against the feature-level seams, no slice branch or workspace remains (denied deletions retried here; any survivor named in the report and PR body with its paste-ready `git push origin --delete` line), and exactly one open PR (`release/<feature_slug>` -> `main`) carries the unioned `## How to verify` list plus the `Closes #<parent>` lines that will close the still-open parent issues when the user merges. Report the integration order and any `failed`/`blocked` slices with notes. Hand off to human review of the single PR, and tell the user to run `harvest-hubs` — the merged build records in `.claude/runs/<feature_slug>/` are its input, and folding them into the hub registry is what makes the *next* feature's `touched_files` predictions smarter. After the fold, `harvest-hubs` archives the records to their slice tickets and removes the directory; the fold must come first, so leave the records in place for it.

A conflict kicked back to `to-slices` carries the affected slices' `actual_touched_files` (from their build records) against their predicted `touched_files`. `to-slices` owns that correction loop; Phase 5 only reports the diff and does not re-cut.
