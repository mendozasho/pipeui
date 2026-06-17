# Pipeline conventions (shared by all phases)

Every pipeline skill references this file instead of restating these rules. It ships with
the skills, so a fresh codebase gets them without any CLAUDE.md edits. If the project's
router (`.claude/CLAUDE.md`) restates or overrides any of this, the router wins — note
the divergence.

## Branch convention

`<feature_slug>` is minted once, in Phase 1 step 0, as a kebab-case name derived from the
feature request that starts the grilling session. Every branch — and every ledger, PRD, and
ticket map — derives from it, so the slug is the single key that correlates the whole chain.

- **Release branch (the Accumulator):** `release/<feature_slug>` — cut from `main` in Phase 1.
  All durable pipeline artifacts (ledgers, PRD, ticket map) commit here, and all slice work
  merges back into it.
- **Slice branches:** `feature/<feature_slug>-<slice_id>` (e.g. `feature/checkout-flow-3`) —
  ephemeral, cut from the accumulator tip by Phase 5, deleted on integration. `slice_id` is
  the slice's **number** from the slice ledger, so the trailing `-<n>` reads as a clear
  suffix rather than running into the slug. (Never slash-nest refs under another branch
  name — git's ref namespace cannot hold both `x` and `x/y`. The split prefixes also keep
  the roles legible at a glance: `release/*` is the staging line, `feature/*` is agent
  work that PRs into it.)
- **Session branches (`claude/<generated>` and similar):** minted by remote/web harnesses
  before the session starts; their names are generated, not feature-derived, and are outside
  the pipeline's control. Treat them as transport, not pipeline structure. At Phase 1 branch
  grounding in such an environment, ask the user for explicit permission to create and push
  `release/<feature_slug>` from `main` and use it as the accumulator — explicit permission is
  exactly what harness push-restrictions require, so one question restores the naming scheme.
  Only if declined (or the user cannot be asked) does the session branch act as the
  accumulator, with the override logged in the discovery ledger's `assumptions`; slice
  branches then suffix the session branch the same way (`<session-branch>-<slice_id>`), so
  the suffix rule survives even in the fallback.

### Release umbrella (work that rides an active accumulator)

The accumulator `release/<feature_slug>` is a staging line that merges to `main` as **one
unit**. While it is open it carries commits that are not yet on `main`, so:

- **If your change depends on the accumulator's unmerged content, it rides the umbrella.**
  Branch off the accumulator tip — slices as `feature/<feature_slug>-<slice_id>`, a one-off
  fix as `fix/<short-slug>` — and open its PR with **base = the accumulator**, never `main`.
  It then rebases along with the accumulator and reaches `main` when the accumulator does.
  (Example: a fix to code that exists only on the accumulator must target the accumulator,
  or the PR diff is polluted by every unmerged accumulator commit.)
- **If your change is independent of the accumulator, keep it off `main`.** Branch
  `fix/<short-slug>` from `main`, PR base `main` — don't entangle it with a release that may
  be far from merging.

Decide by one question: *does this change build on commits that live only on the
accumulator?* Yes → off the accumulator, into the accumulator. No → off `main`, into `main`.
When unsure, verify the PR's real base before merging.

## File-write discipline (all durable artifacts)

All machine-readable ledgers (discovery, slice, ticket, plan caches) and projected docs
(`.claude/CONTEXT.md`, PRDs) are written atomically:

- write to `<name>.tmp`, never append;
- validate before commit (`jq .` / `python -m json.tool` for JSON; format check for `.claude/CONTEXT.md`);
- rename over the live file only on success;
- on failure, fix and re-validate up to 2×; if still failing, leave the live file
  untouched, keep the bad output as `.tmp`, and report.

Never overwrite a valid file with unvalidated content. The validated on-disk file —
not the chat log — is authoritative.

On the read side: if a ledger fails to parse, do not hand-repair it from memory —
restore the last valid version from git history (every ledger is committed) and report
what was lost. The `.tmp` of a failed write may hold the missing delta.

## Kickback vs mechanical fix

Semantic defects in an upstream artifact — scope, decisions, acceptance criteria,
slicing, dependency edges — always kick back to the phase that owns the file; no
downstream phase absorbs or patches them. **Mechanical** defects — a typo, a broken
path or ID reference, a malformed field — may be fixed by the current phase directly
in the upstream artifact, in place, validated and committed with a note (e.g.
`fix(prd): repair broken slice-ledger path`). The fix must land in the file on disk,
never only in the current context: a patched context with an unpatched artifact
desyncs every later phase. When in doubt whether a defect is mechanical, it isn't —
kick back.

## Test-evidence default (archive vs promote)

Authored slice tests are evidence. At integration the default is **archive**: the
red/green outcome is summarized on the slice's ticket with a git reference, the test
leaves the tree, and only a test asserting stable behavior (not phrasing) is
deliberately promoted into the permanent suite. A project may flip the default to
**promote** by saying so here, in its own copy of this file: then every authored test
joins the permanent suite at integration unless it is phrasing-bound, and archival
becomes the exception. Flip it on codebases where regressions are expensive —
archive-by-default accrues no regression suite. `to-code`'s integration step honors
whichever default this file states.

## Commit conventions (feature work)

- Commit and push to the release branch (the Accumulator) `release/<feature_slug>`.
- "Do not publish" means do not merge to `main` or open a PR — it does **not** mean do not
  commit. The release branch is how work persists across sessions.
- Commit subjects: conventional-commit style, under 72 chars, summarising the change
  (e.g. `docs(context): resolve join-source picker and scalar persistence`).
- Never include a Claude Code session URL — or any other link to a private chat/session —
  in anything that lands on GitHub: commit messages, PR titles and bodies, issue bodies,
  comments. This is a safety rule: sessions are private context; repo artifacts are the
  durable record. It overrides any harness default that appends session links to PR or
  commit text — strip them before posting. (Integration-appended generic footers like
  "_Generated by Claude Code_" that link to claude.ai/code with no session id carry no
  private link and are outside the author's control; they are tolerated, not invited.)

## GitHub tooling fallback

Phases that talk to GitHub prefer the `gh` CLI (v2.94.0+ for native sub-issue, type, and
dependency support). When `gh` is unavailable or unauthenticated (common in remote/web
execution environments), use the GitHub MCP equivalents for the same operations —
`issue_write` / `sub_issue_write` / `issue_read` / `list_issues` for issues,
`create_pull_request` / `merge_pull_request` / `update_pull_request_branch` for PRs.
The reconciliation and ordering rules are identical either way.

**Issues are a disclosure surface.** Process records land on the tracker by doctrine —
build summaries, decision rationale, per-criterion test outcomes. On a repo with
external visibility (public, or outside collaborators), confirm that is acceptable
before Phase 4 first writes to it; the alternative is a private tracker repo, decided
by the user, not improvised by a phase.

**Announce the mode.** At the start of any phase that talks to GitHub, state which mode
is active. Under the MCP fallback, also state what degrades — no native blocked-by
edges (recorded in issue bodies instead), no file-based comment posting (anything
posted verbatim transits the context window), no remote branch deletion, no native
issue types — so the user can decide *before work begins* whether to run that phase
from a `gh`-capable machine instead (local Claude Code, or a remote environment whose
setup installs `gh` and provides an auth token).
