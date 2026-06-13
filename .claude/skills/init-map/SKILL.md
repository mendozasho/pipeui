---
name: init-map
description: Generates (or restructures into) the router-style .claude/CLAUDE.md the pipeline expects — a short map of the codebase with pointers to area docs, the glossary (.claude/CONTEXT.md), conventions (.claude/conventions.md), and ADRs. Use on a codebase that has no CLAUDE.md, or when discovery grounding reports the map is missing or not router-shaped. On an ez-skills adoption it also flags skills that shadow a pipeline phase (muting them on your confirmation) and references in your existing docs that the adoption left stale, for you to reconcile. It points; it never holds detail.
---

Every phase grounds on the project's `CLAUDE.md` as a **router**: a short map that says what lives where and points to the doc that holds the detail, so phases read precisely instead of re-exploring the repo. This skill produces that router on a codebase that doesn't have one — the missing-prerequisite generator that `grill-discovery`'s grounding step offers when its map-first read finds nothing to follow.

**Location:** write the router at `.claude/CLAUDE.md`, not the repo root. Claude Code auto-loads project instructions from either location, and the pipeline keeps all of its files under `.claude/` so the repo root stays clean for consumers. The glossary the router points to lives beside it at `.claude/CONTEXT.md`.

## Survey (read wide, shallow)

Build the map from structure, not deep reads:
- `README*`, `docs/**`, existing `.claude/CONTEXT.md`/`.claude/CONTEXT-MAP.md` (or legacy root copies), `docs/adr/`, any existing `CLAUDE.md` (root or `.claude/`);
- manifests (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, …) for the stack, entry points, and scripts (test/lint/build commands);
- the top one or two directory levels, enough to give each major module a one-line purpose.

Do **not** deep-read source files; a router built from skimming is honest — gaps get marked, not papered over.

## Adoption pass (when a seeded conventions.md is present)

When the repo being mapped already holds a seeded `.claude/conventions.md` — the installer ships one into adopting repos — the survey carries one extra duty: note where the repo's observed practices diverge from those conventions, using only what the shallow survey already sees. Recent commit subjects against the commit style, branch names against the branch convention, and whatever else is visible at the surface — never deep reads, never an audit.

Doctrine is unchanged: the router wins, divergences are noted, nothing is auto-rewritten. Report each divergence as one line — the convention, the observed practice, an example — and leave both the conventions file and the repo's habits exactly as found. Reconciling them is the adopter's call, made downstream; this skill points at the gap, it does not close it.

## Skill-shadow audit (adoption pass)

The same adoption signal carries a second duty. With the seven phases now installed (`grill-discovery`, `to-prd`, `to-slices`, `to-issues`, `to-code`, `harvest-hubs`, `init-map`), any *other* skill the adopter already had under `.claude/skills/` is an **orphan** — and an orphan can **shadow** a phase: a project's own grilling skill competing with `grill-discovery`, a wave-runner with `to-code`. Two skills advertising the same intent let Claude Code's auto-selection fire the wrong one.

Judge overlap, don't guess it — a meaning call, which is why it lives here and not in the copy-only installer (see [ADR-0003](./ADR-0003.md)):
- List `.claude/skills/`; the seven names above are the phases, the rest are orphans. Read each orphan's frontmatter `description` (skim the body only if it's ambiguous).
- For each orphan, decide whether it **genuinely** competes with a phase for the same trigger — would the model plausibly pick it when the user means the phase, or vice versa? Decide on intent, not shared words: a skill that merely mentions "plan" or "implement" does not overlap, and most orphans (a code-review or architecture skill) shadow nothing. Leave those untouched; just name them as clear.

Propose; the adopter disposes:
- Report each **genuine** shadow as one line — the orphan, the phase it shadows, the intent they collide on — and recommend muting the **orphan** (the phase is the canonical pipeline path; never mute a phase). Change nothing without a yes.
- On confirmation, mute by adding `disable-model-invocation: true` to the orphan's `SKILL.md` frontmatter: Claude stops auto-selecting it, it stays runnable via `/<name>`, and the change is one reversible line. Write atomically per `.claude/conventions.md`; touch nothing else in the file.
- Never mute a non-overlapping orphan. A muted skill that the adopter's `CLAUDE.md` still names in its workflow is itself a stale reference — surface it under the stale-reference duty below, never by rewriting the router.

## Stale skill references (adoption pass)

Adoption can also change what an existing skill *means*. The installer overwrites a colliding skill in place — a project's own `to-prd`/`to-issues` become the pipeline phases, which read frozen ledgers and hand off differently than the originals did. The name in the adopter's docs is unchanged; the behavior under it is not. So a router, README, or documented workflow that still describes the old behavior — or chains skills in an order that no longer composes (a homegrown grilling step → the new ledger-reading `to-prd`) — is now stale, and nothing in the copy-only installer can know it (it leaves no manifest; this is the same detection-vs-judgment split as the shadow audit, see [ADR-0003](./ADR-0003.md)).

Catch it by contract, not by history: for every skill the adopter's `CLAUDE.md` / workflow docs reference by name, compare how the doc *uses* it against the installed `SKILL.md`'s actual `description`. Flag each mismatch — the doc says `/X` does A, the installed `/X` does B — plus any reference to a now-muted orphan, and any documented step-chain the phases' real hand-offs no longer support. Surface text only; no deep audit.

Surface, don't fix — same doctrine as the divergence and shadow duties: report each stale reference as one line (the doc and step, what it claims, what the skill now does) and let the adopter decide how to reconcile their `CLAUDE.md`. Never auto-rewrite the router; the adopter owns their workflow doc, and this skill only shows them what the adoption changed under it.

## Respect an existing CLAUDE.md

If a `CLAUDE.md` already exists — at the repo root or at `.claude/CLAUDE.md` — do not clobber it. Diagnose what's missing against the template below (usually the Map and Pointers sections), show the user what you'd add or restructure, and confirm before rewriting. Existing prose that is genuinely routing information is preserved; knowledge-dump content is offered a move into an area doc, not silently deleted. A root `CLAUDE.md` is offered a **move** into `.claude/CLAUDE.md` (with the user's confirmation): Claude Code loads both locations, so leaving both in place duplicates the context every session pays for.

## Template

```md
# <Project name>

<One or two sentences: what this project is. Stack: <languages/frameworks>.>

Build/test/lint: `<commands from the manifests>`

## Map

- `<dir or module>/` — <one-line purpose>. Details: <link to area doc, or "no area doc yet">
- ...

## Pointers

- Domain glossary: [.claude/CONTEXT.md](./CONTEXT.md) — canonical terms; read before naming anything
- Conventions: [.claude/conventions.md](./conventions.md) — branches, file-write discipline, commits
- ADRs: [docs/adr/](../docs/adr/) — decisions with rationale (if present)
- PRDs: `.claude/prds/` — one per feature, keyed by feature_slug (if present)
```

(Link targets are relative to the router's own location, `.claude/CLAUDE.md` — hence `./CONTEXT.md` for a sibling and `../` to reach the repo root.)

Rules:
- **Router only.** One line per module; detail belongs in area docs, glossary terms in `.claude/CONTEXT.md`, decisions in ADRs. Target well under ~80 lines.
- **"No area doc yet" is a valid entry.** Mark thin coverage honestly and report the list of unmapped/undocumented modules at the end — do not generate area docs here (that's real documentation work, out of this skill's scope).
- **Point only at things that exist**, except the glossary and conventions pointers, which the pipeline will create/ship: keep those even if the files aren't there yet, marked "(created by the pipeline)".

## Write & exit

Write atomically per the file-write discipline in `.claude/conventions.md`, commit on the current branch per its commit conventions (subject like `docs(map): add router CLAUDE.md`). Idempotent: re-runs refresh the Map against the current tree (new modules added, dead pointers fixed) and never duplicate sections.

Report what was mapped, what has no area doc, and — if this was invoked from a `grill-discovery` grounding gap — tell the user to resume discovery, which can now follow the map.

When the adoption pass ran, the report also lists the divergences it found, any shadowing orphans (which were muted, which were left as clear), and any stale skill references the adoption left in the adopter's docs — and ends with the single next step: start the first `grill-discovery` session in this repo — the router is in place, the gaps are named, and discovery is where the adopter's first feature meets the pipeline.
