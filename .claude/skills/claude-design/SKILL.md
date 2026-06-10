---
name: claude-design
description: Produce an interactive HTML design brief for a UI issue, rendered live with the project's real design tokens and primitives. Use when a GitHub issue is marked [Design-gated] and needs a spec before implementation can start.
---

# Claude Design

Produce a self-contained interactive HTML design brief that an implementing agent can read directly and code from. The brief renders all mocks live in the browser using the project's real tokens and components — no static screenshots, no placeholder colours.

## When to invoke

Issues marked `[Design-gated]` on GitHub. The brief unblocks implementation: once it is attached to the issue, the issue transitions from HITL to AFK.

Pass the issue number as the argument: `/claude-design #152`

---

## Process

### 1. Read the issue

Fetch the full issue body (`mcp__github__issue_read`). Extract:
- The required outputs (what the user needs to configure / see)
- The acceptance criteria (map 1:1 onto Spec sections and the acceptance table)
- Any domain constraints called out (deferred work, out-of-scope items)

### 2. Read the five context sources

Read **all five** before writing a single line of HTML. They are not optional.

**a) A prior design brief HTML** — lifts the house chrome verbatim.
- Look in the user's uploaded files, or search the repo for `*Design Brief*.html` or `*design-brief*.html`.
- Extract the full doc structure: `<style>` block, `Eyebrow / SubHead / Card / Section / SectionHead / M / Diff / PropsTable / Note / TokenChip / GoodBad` components, the masthead pattern, constraints banner pattern, Spec section pattern, component breakdown table, and acceptance criteria table.
- The new brief must use this chrome identically — same component names, same inline-style patterns, same `--radius`, same font stack. Do not invent new chrome.

**b) `src/pipeui/frontend/index.html`** — the `:root` token block.
- Copy the exact token names and values. Every colour in the brief must be a token from this file — no hex literals, no new values.
- Note the font stack: Geist + Geist Mono (CDN).

**c) `src/pipeui/frontend/ui.jsx`** — existing shared primitives and `ICONS`.
- Note every `ICONS` entry (name + SVG path). New icons the brief introduces must be listed explicitly with their SVG path spec so the implementing agent can add them.
- Note every exported component (`Btn`, `Icon`, `SourceBadge`, `KindTag`, `StatusPill`, `DataTable`, `Switch` if present, `Modal` if present). Mocks in the brief must use these exactly where they exist, and must only introduce new primitives when none of the existing ones fit.

**d) The target screen file(s)** — the `screen-*.jsx` files the issue touches.
- Read the relevant screen(s) to understand existing patterns: existing modal chrome (if any), existing card shells, existing drag/drop data keys, existing state variable names.
- Any new component must extend or refactor existing patterns, not duplicate them.

**e) `CONTEXT.md`** — canonical domain data shapes.
- Read the sections relevant to the issue (e.g. `source_builtin_map` config shape, dry-run response shape, `alias_map` mechanics).
- Every mock that shows a data payload (config blobs, PATCH bodies, API response shapes) must use the canonical field names from `CONTEXT.md`, not invented ones.

### 3. Draft the brief structure

Every brief has the same top-level structure (in order):

1. **`<head>`** — CDN scripts (React 18.3.1, ReactDOM 18.3.1, Babel standalone 7.29.0), Geist + Geist Mono fonts, `:root` tokens block copied from `index.html`, house `<style>` block from the prior brief (scrollbar, `.mono`, `body::before` gradient, animation keyframes, shared input focus style).

2. **Masthead** — pipeui logo mark, screen file path + branch tag, `Eyebrow` "Design Brief · Issue #N · Claude Design pass", `h1` title, description paragraph, feature-chip row (what this brief ships).

3. **Constraints banner** — one paragraph listing: inline-styles only, no new colour values (all existing `:root` tokens), files changed, any explicitly deferred items (copy from the issue's Out of Scope section).

4. **Spec sections** (one per major decision) — `<Section id="...">` + `<SectionHead kicker="Spec · N" title="...">`, then `<Card>` with live mocks, `<Note>` annotations, `<Diff>` snippets where the code change matters.

5. **Component breakdown** — `<PropsTable>` for each new or modified component: prop name, type, default, notes. State explicitly which file each component lives in.

6. **The change as a diff** — illustrative `<Diff>` showing the shape of the edit (not a working implementation, just the decision-rich parts).

7. **Acceptance criteria table** — two-column: criterion text (matching the issue's acceptance criteria exactly) → how this brief satisfies it.

8. **Tokens reference** — `<TokenChip>` for every token used in the brief. Confirm no new values.

### 4. Write the brief

**Live mocks are mandatory.** Every Spec section must have at least one interactive mock rendered from real components with real tokens. The reader should be able to click/type/toggle to understand the intended behaviour — not just read prose about it.

Rules:
- Inline styles only — no `<style>` class blocks in the mock code (`:root` tokens and the house `<style>` block are the only exceptions, both in `<head>`).
- Every colour is a CSS variable from `:root`. If you find yourself writing a hex value, go back and find the right token.
- Mocks use the exact component names from `ui.jsx`. If `Icon`, `Btn`, `SourceBadge` exist, use them — don't re-implement them.
- New components introduced by the brief are also implemented in the brief (so the mock actually works), then listed in the component breakdown for the implementing agent to copy.
- `Diff` blocks are illustrative: show the decision-encoding parts of the change, not a complete file diff. Label the file and the function.
- Open design decisions (things the brief intentionally leaves configurable) go in a `TweaksPanel` — a floating panel the user can toggle live in the brief. Each open decision gets a `TweakRadio` or `TweakColor` entry.

### 5. Save and attach to the issue

Save the brief as a `.html` file in `.claude/design-briefs/` with a descriptive name:
```
.claude/design-briefs/<issue-number>-<short-slug>.html
```

Commit and push to main:
```
git add .claude/design-briefs/
git commit -m "design: brief for #<N> — <short title>"
git push origin main
```

Post a comment on the GitHub issue summarising the spec decisions (the same content that would unblock an implementing agent), using `mcp__github__add_issue_comment`. The comment must be self-contained — the implementing agent reads the comment, not the HTML file (the HTML is the interactive version for human review).

The comment structure:
- `## Design brief received — issue unblocked`
- `## What to build` — one paragraph
- `## Spec decisions` — one `###` subsection per Spec section, with the decisions written as prose + code blocks (not as "see spec 3")
- `## New / modified components` — markdown table
- `## Tokens used (no new values)` — inline list
- `## Acceptance criteria mapping` — markdown table

---

## Output quality checklist

Before posting the comment, verify:

- [ ] Every mock renders from real tokens — no hex literals in mock code
- [ ] Every icon used in the brief is either in the existing `ICONS` or listed as a new entry with its SVG path
- [ ] Every data shape (config blobs, API bodies) uses field names from `CONTEXT.md`
- [ ] Every new component is listed in the component breakdown with correct file assignment
- [ ] The acceptance criteria table covers every criterion from the issue
- [ ] Open design decisions are exposed as live Tweaks (not silently resolved)
- [ ] The constraints banner explicitly lists all out-of-scope items from the issue
- [ ] No session URLs appear in commit messages
