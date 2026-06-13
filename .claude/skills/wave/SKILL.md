---
name: wave
description: Orchestrate a wave of parallel implementation agents — one per issue — then merge all feature branches into a single release/<short-slug> branch and open one PR to main. Use when the user wants to kick off a batch of ready-for-agent issues as a wave.
disable-model-invocation: true
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
- An instruction to check off acceptance criteria on the GitHub issue before finishing.
- **An explicit instruction to push the feature branch ONLY — do NOT open a PR.** The integrator opens the single PR. Agents that open their own PRs create noise the reviewer should not have to close.
- An instruction to verify the branch was pushed successfully before ending.

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

1. Derive a short slug from the issue titles (e.g. `pr163-bug-fixes`, `results-screen-polish`). Never use positional names like `wave-1` or `wave-2`.
2. Create `release/<short-slug>` from the latest main:
   ```bash
   git fetch origin main
   git checkout -b release/<short-slug> origin/main
   ```
3. Merge each feature branch in dependency order (blockers first). Use the branch's actual prefix (`fix/` or `feat/`) as defined in the issue's `## Branch` section. For each:
   ```bash
   git merge --no-ff origin/<fix-or-feat>/<branch> -m "Merge <fix-or-feat>/<branch> into release/<short-slug>"
   ```
4. **Conflict resolution rule:** include ALL additions from ALL branches — never drop a component added by any branch. Conflicts in the same file are resolved by taking both sets of changes. If conflict intent is unclear, read the relevant issue bodies to understand what each branch intended, then apply both.
5. Run the test suite: `pytest` (or the project's test command — check `pyproject.toml` or `Makefile`).
6. If pytest fails, diagnose and fix on the `release/<short-slug>` branch before continuing.
7. **Smoke-test the release branch end-to-end** — pytest covers Python-layer correctness, but it does not catch frontend↔backend integration failures (wrong API shape, NaN serialization, missing fields). The integrator must verify the vertical works as a running app before opening the PR:
   a. Start a fresh server on a known port (e.g. `18888`) with a clean DB:
      ```bash
      rm -f pipeui.db && python -m pipeui init
      python -m uvicorn pipeui.main:app --host 127.0.0.1 --port 18888 --no-access-log &
      sleep 3
      curl -s http://127.0.0.1:18888/sources  # must return []
      ```
   b. For each merged issue, read its acceptance criteria and derive curl-based checks that exercise the endpoint(s) it touches. Register any required test fixtures (sources, functions) via the API before running checks that depend on them.
   c. Verify each check returns the expected HTTP status and response shape. A non-2xx response or a missing key in the response body is a failure.
   d. Kill the server after tests: `pkill -f "uvicorn pipeui.main" || true`
   e. If any smoke test fails, diagnose and fix on the release branch, then re-run from step 5. Do not open the PR until all smoke tests pass.
9. Push `release/<short-slug>` and open one PR: `release/<short-slug> → main`. PR title: `<short-slug>: <short description>`. PR body: list each closed issue with `Closes #N`, plus a summary of what the release delivers.
10. Verify the PR is open.
11. **Delete each feature branch from the remote** now that it is captured in `release/<short-slug>`:
   ```bash
   git push origin --delete <feature-branch-1>
   git push origin --delete <feature-branch-2>
   ```
   Do this after confirming the PR is open. If a delete fails, note it in the report but do not block the PR.

### 5. Report back

Return a summary to the user:
- Which feature branches were merged into the release branch.
- Whether tests passed.
- The PR URL.
- Any issues that need the user's attention (failed tests, unresolved conflicts that require design decisions, etc.).

## Rules

- **Never merge to main directly.** Implementation agents push feature branches only — no PRs. The integrator opens the single PR from `release/<short-slug>`. Only the user merges to main.
- **Always use `isolation: "worktree"` for every agent that commits or pushes.** Read-only research agents do not need it.
- **Session URLs must never appear in commit messages.** Strip or omit them.
- **Sequential slices are not waves.** If the issues you were given have a dependency chain (A blocks B blocks C), they are not a wave — they are a sequential pipeline. Run them one at a time, merging each before starting the next, or ask the user how they want to proceed.
- **Design-gated issues are not wave candidates.** Issues marked `[Design-gated]` require a human design pass before implementation. Skip them and surface a reminder to the user.

## Adapting to project conventions

Before launching agents, check for a `CLAUDE.md` or `CLAUDE_REFERENCE.md` in the repo root. If present, include a pointer to the relevant sections in each agent's prompt (e.g. "read CLAUDE_REFERENCE.md §1 for schema conventions before touching queries.py"). This prevents agents from violating project-specific module boundaries or naming conventions.
