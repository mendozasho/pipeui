---
name: harvest-hubs
description: Maintenance, run between features, never inside one. Folds the build records of integrated features into the hub-file registry (.claude/hub-registry.json) that to-slices reads to pre-seed touched_files predictions, then archives the folded records to their slice tickets and removes .claude/runs/<feature_slug>/ — fold always precedes archival. Single writer of the registry; pure accumulator, no decay. Use after to-code completes a feature, or any time unharvested build records exist.
---

This skill is the **single writer** of `.claude/hub-registry.json` — the advisory, learned prior over which files get written often and by which layers. `to-slices` reads it to pre-seed `touched_files` with known hub files *before* a conflict reveals them; this skill is how the registry learns. It is an optimization, never a correctness mechanism — correctness lives in `to-code`'s sequential integration and `to-slices`' reactive correction loop. The full rationale, including the deliberately rejected "improvements" (decay, tight matching), is [ADR-0001](../to-slices/ADR-0001.md); read it before changing anything here.

## Inputs
- `.claude/runs/<feature_slug>/<slice_id>.md` — the build records, merged into the accumulator (and eventually `main`) by `to-code`. Each carries `actual_touched_files`: the ground truth of what that slice wrote. The records exist in the tree only between integration and this skill's archive step — they are this skill's input, then they live on tickets.
- `.claude/slices/<feature_slug>.json` — the slice ledger for each harvested feature; source of each slice's `layers` (build records do not carry them).
- `.claude/hub-registry.json` — this skill's own output. Load it first if present; the `harvested_records` list is what makes re-runs idempotent.

Nothing to harvest (no build records, or all already in `harvested_records`) → say so and stop. This skill never blocks anything downstream.

## Process

1. **Find unharvested records.** Enumerate `.claude/runs/*/*.md` and drop every `<feature_slug>/<slice_id>` already present in the registry's `harvested_records`.

2. **Fold — increment only.** For each new record, read its `actual_touched_files` and the slice's `layers` from that feature's slice ledger. For each file:
   - increment `slice_count` by one;
   - union the slice's `layers` into the file's `layers`;
   - add the `feature_slug` to the file's `features` set.
   Append the record's `<feature_slug>/<slice_id>` to `harvested_records`.

   The fold **only ever adds**: no decay, no eviction, no count decrements (per ADR-0001 — hubs are structural, staleness costs only mild over-serialization, and a pure accumulator keeps this skill trivial and the slicer a pure reader). If a harvested file no longer exists in the repo (moved/deleted), keep the entry but set `"missing": true` so `to-slices` can skip it without losing the history.

   **Exclude never-shareable paths from the fold:** anything under `.claude/runs/` (each slice's own build record — unique to its slice by construction) and authored-test files that were archived to the ticket at integration (named in the record's Acceptance section, absent from the tree). These can never cross `hub_threshold` and are pure noise in the registry; a test deliberately *promoted* into the permanent suite is a real file and folds normally.

3. **Write atomically** per the file-write discipline in `.claude/conventions.md`, and commit to the current branch (`main` or the release branch you ran on, per its commit conventions — subject like `chore(hubs): harvest <feature_slug>`).

4. **Archive the folded records — fold first, always.** For each feature whose records are in `harvested_records` and still on disk: post, on its slice's ticket (the issue named in the record's `Issue:` line; the parent slice issue is the fallback), one short comment carrying the record's What-was-built lines, its per-criterion outcome summary, and a **git reference** — the commit SHA at which the record last existed plus its path — where the full record lives forever in history. **Never paste the record body into the comment**: the reference reproduces it on demand at near-zero token cost. Then `git rm` the feature's record directory — the build ledger `.claude/runs/<feature_slug>.json` stays in the tree. Commit (`chore(hubs): archive <feature_slug> build records to tickets`). Process records live on tickets, not the tree (`.claude/CLAUDE.md`'s state philosophy); idempotency is unaffected because `harvested_records` — not the files — is what guards re-runs. Never archive a record absent from `harvested_records`: the fold is the only reader of its `actual_touched_files`, and archiving first would lose the prior.

5. **Report** the newly promoted hubs (files that crossed `hub_threshold` in this run), the records folded, and the records archived.

## Registry schema
```json
{
  "version": 1,
  "hub_threshold": 3,
  "harvested_records": ["<feature_slug>/<slice_id>"],
  "files": {
    "<repo-relative path>": {
      "slice_count": 0,
      "layers": ["data | business_logic | api | ui | docs"],
      "features": ["<feature_slug>"],
      "missing": false
    }
  }
}
```

A file **is a hub** iff `slice_count >= hub_threshold`. The threshold guards signal quality — a file needs real cross-slice evidence before it is trusted as structure rather than coincidence (default 3; see ADR-0001 before changing it, and note the matching side in `to-slices` deliberately over-includes — the two knobs point different ways on purpose).

## Guardrails
- **Single writer.** Only this skill writes the registry. `to-slices` reads it; nothing else touches it.
- **Pure accumulator.** No decay, no tightening, no per-slice precision. If the codebase starts churning structurally and stale hubs hurt, add recency weighting *here, in the fold* — never in `to-slices` (ADR-0001, "When to revisit").
- **Advisory output.** The registry must never gate a freeze, a merge, or any phase's exit condition. If someone proposes that, point them at ADR-0001 and decline.
- **Idempotent.** Re-runs fold only records absent from `harvested_records`; counts are never double-incremented.
