# Retro — scalar-params / #186 remediation (first real ez-skills run)

Context: ran the ez-skills pipeline to remediate the #186 bug cluster (#188–#192), which
are bugs found while testing the scalar-params + step-edit feature PR (#157). The run
shipped, but manual verification exposed that the feature didn't work end-to-end, and the
work had been built on the wrong base. Captured here to feed back into the ez-skills package.

## Failure modes observed

1. **Accumulator built on a base that lacked the code being remediated.**
   `release/scalar-params` was cut from `release/v0.0.1-beta`, which does **not** contain
   #157 (the feature being remediated — its real code lives at commit `f7abeae`, an open PR
   against `release/v0.0.1-beta`). With no scalar-params code in the base, `/to-code`
   silently turned "remediation" into a from-scratch **re-implementation** that *regressed*
   below #157 (lost the StepCard edit button and attach-time scalar persistence).
   - **Fix for ez-skills:** for any remediation/back-fill feature that references an existing
     PR/issue, `grill-discovery` (and Phase-1 branch grounding) must **verify the accumulator
     base actually contains that PR's code** (e.g. `git merge-base --is-ancestor <feature-tip>
     <accumulator>`). Discovery here *assumed* #157 was "shipped" when it was an unmerged PR
     on a different branch — that assumption should be a checked precondition, not prose.

2. **No model for "subset / bug-batch of an existing feature."**
   #186 is a bug cluster *under* feature #157, not a standalone feature. The pipeline minted
   a standalone "scalar-params" feature around #186, didn't ingest the existing parent(#186)/
   sub-issue(#188–#192) tree, and didn't recognize #186 ⊂ #157 (so it couldn't reason about
   what #157 already had vs what the bugs needed).
   - **Fix:** discovery should detect when the target is an existing issue with a parent and/or
     sub-issues, and treat the existing tree as authoritative rather than re-deriving one.

3. **Acceptance criteria too shallow (seam-only).**
   #191 acceptance was "API dry-run/PATCH round-trip"; #192 was "renders a free-text input."
   Both were satisfiable while the feature was non-functional end-to-end: the StepCard edit
   button was never wired, and scalar values were dropped on initial attach. The seam tests
   passed; the UI flow didn't exist.
   - **Fix:** for UI features, `to-slices` acceptance must include an end-to-end behavioral
     assertion (the user-visible flow), not just a backend seam or a render-state check.

4. **Tickets closed on an unmerged / unverified integration.**
   `/to-code` closed #188–#192 when it integrated the (regressed, never-merged-to-main) fork.
   The fixes weren't correct, weren't on the canonical target, and some weren't implemented at
   all — yet the tickets read "closed."
   - **Fix:** sub-issue closure should be gated on the fix being (a) verified per-criterion by
     the integrator's own re-run and (b) merged to the canonical target — never inferred from
     an agent's self-reported greens, and never on a non-default-branch merge.

5. **Branch target/flow not validated against project convention.**
   The intended flow (per conventions.md) is: feature/slice branches → `release/<feature_slug>`
   accumulator → **one** `release/<feature_slug> → main` PR that the human reviews and merges.
   The original feature (#157) targeted `release/v0.0.1-beta` (a staging line ahead of main).
   The remediation PRs ended up pointed straight at `main`, bypassing both the accumulator
   pattern and the project's staging line.
   - **Fix:** Phase-1 grounding should confirm the integration target (does the project stage
     on a `release/x` line before main?) and keep the single-PR-to-the-agreed-target invariant.

## Net
The feature was finished correctly only after manually catching the base-provenance error and
rebasing the work onto #157 (`f7abeae`). The recurring root cause across 1, 4, and 5 is
**unverified provenance** — base, merge state, and target were assumed rather than checked.
Adding explicit provenance checks at Phase 1 / discovery would have prevented the whole detour.
