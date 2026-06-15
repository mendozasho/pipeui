# Retro — render-builtin-steps / #209 (second full ez-skills run)

Context: ran the full ez-skills pipeline (grill-discovery → to-prd → to-slices → to-issues →
to-code) on #209 (render placed built-in steps on the Builder canvas). Shipped via PR #213
into `release/v0.0.1-beta`; manual testing then surfaced a related bug (#214) which was fixed
and folded into the same PR. Two follow-ups filed (#214 fixed, #215 design-gated). Captured
here to feed the ez-skills package and record project-environment gotchas.

## Failure modes / friction observed

1. **`to-slices` cut the feature horizontally (backend slice + frontend slice).**
   The first decomposition was slice 1 = "extend `get_pipeline`" (all api/business) and slice 2
   = "render the card" (all ui) — a horizontal layer split, exactly what vertical slicing forbids.
   The user caught it at the slice-map confirmation. The `to-slices` rule said "do not decompose
   by horizontal layer" but only illustrated the data/API case; it never named the
   frontend/backend split or gave a "tell," so the mistake reached confirmation.
   - **Fix for ez-skills:** **shipped** as `mendozasho/ez-skills#49` — sharpened the `to-slices`
     step-3 rule (a slice carries frontend **and** backend together; a single-layer slice is the
     anti-pattern unless its other layers already exist; a PRD that can only be sliced by layer is
     a PRD defect → kick to `to-prd`) and added a "Vertical slice (seam)" glossary entry
     distinguishing a slice from a test seam and from the horizontal front/back line.

2. **`.claude/` write-permission friction under auto mode.**
   The user required asking before any `.claude/` write. The auto-mode classifier then hard-blocked
   writes to top-level `.claude/*.md` (CONTEXT.md) *even after* the user said "go ahead", including
   the atomic `.tmp` variant — so the prescribed write discipline tripped the guard. Resolved by the
   user clearing it and retrying.
   - **Note (not an ez-skills bug):** when a session sets a "ask before X" rule, the conversational
     grant and the classifier are separate gates; expect to surface the block and let the user clear
     it rather than routing around it.

3. **iCloud `node_modules` eviction broke vitest mid-session (environment, not pipeline).**
   The repo lives in iCloud Drive; "Optimize Storage" evicted `node_modules` to dataless
   placeholders. Reads of partial files made vitest fail at *environment load* with
   `URLSearchParams.install is not a function` (jsdom/whatwg-url) then `render is not a function`
   (@testing-library exports undefined) — masquerading as 45 test failures. Recovered with `npm ci`;
   the `uvicorn --reload` dev server recursing into `node_modules` accelerated the churn.
   - **Durable fix (project):** move the repo out of iCloud, or exclude it from "Optimize Storage"
     — same iCloud-path root cause as the backend's `PYTHONPATH=src` workaround. Recorded to memory.

## What improved since the scalar-params/#186 retro

The recurring root cause there was **unverified provenance** (base, merge state, target assumed).
This run checked them, and several of that retro's "fixes" held:

- **Base provenance verified.** `feat/render-builtin-steps` was cut from `release/v0.0.1-beta` and
  confirmed an ancestor before any work; the user's explicit "base off the release branch, not main"
  was honored throughout. (Addresses scalar-params retro #1 and #5.)
- **Existing issue reconciled, not duplicated.** #209 already described the whole feature; `to-issues`
  made it the parent (appended the canonical acceptance, added labels) instead of minting a duplicate.
  (Addresses scalar-params retro #2.)
- **Integrator independently re-ran every acceptance test** (74 pytest + 45 vitest) rather than trusting
  the build agent's self-reported greens; the lone `test_worker` setrlimit failure was proven
  pre-existing/macOS-only before acceptance. Parent #209 left open (closes on the beta→main promotion).
  (Addresses scalar-params retro #4.)
- **Edit-pre-fill risk was flagged in discovery and caught in testing.** The PRD named "edit pre-fill
  fidelity (the modal round-trips its own config)" as the primary edit-path risk; manual testing then
  found the Back→Next wipe (#214, a pre-existing #152 `goNext` bug). Fixed test-driven and folded in.

## Net
A clean run whose one real defect was a **horizontal cut**, caught by the human gate and turned into a
durable ez-skills doctrine fix (#49) rather than a silent patch. The branch/provenance discipline that
derailed the #186 run held this time. The only unplanned cost was the iCloud `node_modules` eviction —
an environment issue, now documented, with a durable recommendation to get the repo off iCloud.
