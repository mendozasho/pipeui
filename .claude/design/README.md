# Design artifacts — mirror of the claude.ai/design `pipeui` project

This directory mirrors the visual-design briefs and specs authored in
**claude.ai/design**. It is the design intent the ez-skills pipeline reads when
a slice is design-gated (`blocked-on-design` → attach brief → `ready-for-agent`).

## Source & sync direction

| | |
|---|---|
| Source project | `pipeui` (claude.ai/design), owner **the maintainer** |
| projectId | `0e2383c3-f7eb-461b-b581-3c22afb5a1b3` |
| Project type | `PROJECT_TYPE_PROJECT` (a *regular* project, not a design-system project) |
| Sync direction | **pull-only** — Design → repo, via the `DesignSync` tool (`get_file`) |

Because the source is a regular project (and that type is immutable at
creation), **DesignSync can read it but not push back**. Edits made in
claude.ai/design are pulled down with `/design-sync` / the `DesignSync` tool;
repo edits are **not** reflected upstream. To get bidirectional sync you'd have
to stand up a new *design-system* project and migrate content into it — a
separate, deliberate effort (see #211 discussion).

The `.brief.html` files are **live, self-contained** design briefs — open any of
them in a browser to see the rendered mockups + spec. The `.md` files are the
text-only implementation references.

## Contents → tickets

| File | Covers | Ticket |
|---|---|---|
| `builder-step-cards-redesign.brief.html` | Builder canvas step-card redesign | **#209** |
| `join-source-picker-modal.brief.html` | Two-step join source picker modal | #152 (shipped) |
| `scalar-input-ui-polish.brief.html` | Scalar param input polish | #153 |
| `function-drawer-redesign.brief.html` | Function drawer redesign | — |
| `results-empty-state.brief.html` | Results screen empty state | #121 |
| `data-screen-layout-spec-session-2.brief.html` | Data screen source-list redesign (live) | #112 |
| `data-screen-layout-spec.md` | Data screen source-list redesign (text) | #112 |
| `ui-primitives-spec-session-1.brief.html` | Spinner / LoadingState / InlineError (live) | — |
| `ui-primitives-spec.md` | Spinner / LoadingState / InlineError (text) | — |
| `session-1-migration-checklist.brief.html` | Rollout of the Session-1 primitives across screens | — |

## Re-syncing

To refresh from the Design project, pull the changed file(s) with the
`DesignSync` tool (`get_file` on projectId above) and overwrite the local copy
on a branch + ticket. Prototype `frontend/` JSX and screenshots in the Design
project are intentionally **not** mirrored — the repo's `src/pipeui/frontend/`
is the code source of truth.
