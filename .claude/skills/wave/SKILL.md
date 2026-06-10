---
name: wave
description: Orchestrate a wave of parallel implementation agents — one per issue — then merge all feature branches into a single wave/N branch and open one PR to main. Use when the user wants to kick off a batch of ready-for-agent issues as a wave.
---

# Wave

Orchestrate a batch of `ready-for-agent` issues as a wave: parallel implementation agents → single integrator → one PR.

## Invocation

```
/wave #123 #124 #125
```

Pass the issue numbers to implement in parallel. Issues that have a "Blocked by" dependency on each other must NOT be passed together — run the blocker wave first, merge it, then run the dependent wave.

## Process

### 1. Pre-flight checks

For each issue number passed as an argument:

- Fetch the issue body (`mcp__github__issue_read`). Confirm it has a `## Branch` section and is labelled `ready-for-agent`.
- Extract the branch name from the `## Branch` section.
- Identify all files each issue will touch (scan the "What to build" and "Implementation notes" sections).
- **File-overlap check:** if any two issues modify the same file, they cannot run in parallel — surface this to the user and ask which should run first. Do not proceed past a file-overlap conflict without user confirmation.
- If all issues are safe to run in parallel, proceed.

### 2. Launch parallel implementation agents

Send a **single message** with one `Agent` tool call per issue. Every agent must use `isolation: "worktree"` — without it, parallel agents share the working directory and will corrupt each other's branches.

Each agent prompt must include:
- The issue number and branch name.
- An instruction to read the full issue body before writing any code.
- An instruction to pull latest main and rebase before starting.
- The commit format rule: `git commit -m "subject" -m "body"` with repeated `-m` flags — no heredoc syntax, no session URLs.
- An instruction to check off acceptance criteria on the GitHub issue before opening a PR.
- An instruction to open a PR with `Closes #N` in the body, then verify it is open.
- **Critically:** the agent must push its branch and open a PR, but must NOT merge to main.

Wait for all agents to complete before proceeding to step 3.

### 3. Verify all branches exist

For each feature branch, confirm the branch was pushed:

```bash
git fetch origin
git branch -r | grep feat/...
```

If any branch is missing, surface the failure to the user before continuing.

### 4. Launch the integrator agent

The integrator agent also uses `isolation: "worktree"`. Its job:

1. Determine the next wave number: check existing `wave/N` branches and increment (`git branch -r | grep wave/`).
2. Create `wave/N` from the latest main:
   ```bash
   git fetch origin main
   git checkout -b wave/N origin/main
   ```
3. Merge each feature branch in dependency order (blockers first). For each:
   ```bash
   git merge --no-ff origin/feat/<branch> -m "Merge feat/<branch> into wave/N"
   ```
4. **Conflict resolution rule:** include ALL additions from ALL branches — never drop a component added by any branch. Conflicts in the same file are resolved by taking both sets of changes. If conflict intent is unclear, read the relevant issue bodies to understand what each branch intended, then apply both.
5. Run the test suite: `pytest` (or the project's test command — check `pyproject.toml` or `Makefile`).
6. If tests fail, diagnose and fix on the `wave/N` branch. Do not open the PR until tests pass.
7. Push `wave/N` and open one PR: `wave/N → main`. PR title: `wave/N: <comma-separated issue titles>`. PR body: list each closed issue with `Closes #N`, plus a summary of what the wave delivers.
8. Verify the PR is open.

### 5. Report back

Return a summary to the user:
- Which feature branches were merged into the wave branch.
- Whether tests passed.
- The PR URL.
- Any issues that need the user's attention (failed tests, unresolved conflicts that require design decisions, etc.).

## Rules

- **Never merge to main directly.** Implementation agents open PRs from their feature branch; the integrator opens a PR from `wave/N`. Only the user merges to main.
- **Always use `isolation: "worktree"` for every agent that commits or pushes.** Read-only research agents do not need it.
- **Session URLs must never appear in commit messages.** Strip or omit them.
- **Sequential slices are not waves.** If the issues you were given have a dependency chain (A blocks B blocks C), they are not a wave — they are a sequential pipeline. Run them one at a time, merging each before starting the next, or ask the user how they want to proceed.
- **Design-gated issues are not wave candidates.** Issues marked `[Design-gated]` require a human design pass before implementation. Skip them and surface a reminder to the user.

## Adapting to project conventions

Before launching agents, check for a `CLAUDE.md` or `CLAUDE_REFERENCE.md` in the repo root. If present, include a pointer to the relevant sections in each agent's prompt (e.g. "read CLAUDE_REFERENCE.md §1 for schema conventions before touching queries.py"). This prevents agents from violating project-specific module boundaries or naming conventions.
