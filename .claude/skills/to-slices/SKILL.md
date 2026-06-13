---
name: to-slices
description: Decomposes the PRD into independently testable vertical slices and freezes them into the slice ledger (.claude/slices/<feature_slug>.json) with acceptance criteria, dependency edges, shared surfaces, and predicted touched_files. Use after the PRD is written, or on re-entry when to-code kicks back an integration conflict. Does NOT re-open scope or write code. Hands off to to-issues.
---

This skill is Phase 3. It reads the **frozen** discovery ledger and the **PRD** from `to-prd`, decomposes the feature into vertical slices, and freezes them into a slice ledger. This is the partitioning phase: `grill-discovery` settled *what the feature is*, `to-prd` wrote it as one whole, and this skill decides *how to chop it* into increments that can each be built and verified end to end. It does **not** re-open scope, re-decide, or write code — if the PRD or ledger is wrong, kick back to the phase that owns it.

Slicing belongs here, against the finished PRD, rather than during discovery — so the cuts are made against the authoritative, consolidated spec, not a ledger that is still settling.

This skill also owns the **prediction-improvement loop**: the `touched_files` it records are a best-effort prediction, and when Phase 5 (`to-code`) hits an integration conflict it kicks the offending slices back here with their observed actuals. Correcting the cut from those actuals is a re-cut/re-serialize — squarely this skill's job — not a re-scope.

## Inputs (authoritative)
- `.claude/discovery/<feature_slug>.json` — the frozen discovery ledger. Source of `mvp_scope` (the **coverage target**), `definition_of_done`, `affected_components`, `dependencies`, and `intent`. This is **not** a source of slices — slices are produced here.
- `.claude/prds/<feature_slug>.md` — the PRD. The authoritative behaviour (Problem/Solution, User Stories, Implementation Decisions) that slices are cut against, plus the **feature-level test seams** (Testing Decisions) that per-slice acceptance is grounded on.
- `.claude/CLAUDE.md` — the project router/map. Follow its pointers to area/reference docs for the modules a slice touches, so cut points fall on real seams and `touched_files` predictions land on real files. It points; it does not hold detail. If missing or thin, fall back to a targeted read of the ledger's `affected_components` and note the gap (`init-map` can generate the router).
- `.claude/CONTEXT.md` (or `.claude/CONTEXT-MAP.md`) — the glossary. Use its vocabulary in every slice; never coin competing terms.
- Any ADRs in the touched area — respect them when cutting.
- `.claude/slices/<feature_slug>.json` — this skill's own output. If present, load it first and resume rather than re-cutting from scratch. If its `source_prd_hash` no longer matches the current PRD file, the PRD changed under the decomposition — surface that and re-derive against the current PRD instead of silently resuming.

**Consult on re-entry (Phase-5 execution feedback).** These exist only after a `to-code` run has kicked back; they are the input to the prediction-improvement loop in step 2 and are absent on a first pass:
- `.claude/runs/<feature_slug>.json` — the build ledger. Identifies slices marked `failed`, with a note saying which kind: an **integration conflict** (code was written; correct from `actual_touched_files`) or a **plan-gate halt** (the agent stopped before coding because its plan needed a file an in-flight sibling claimed; correct from `planned_touched_files`). Both mean the same thing — the predicted `touched_files` under-claimed and the graph failed to serialize an overlapping pair.
- `.claude/runs/<feature_slug>/<slice_id>.md` — the per-slice build records. Each carries that slice's `actual_touched_files` (ground truth) and any file flagged as unanticipated; a plan-gate record has only its **Plan** section, whose `planned_touched_files` plays the same corrective role.

**Consult if present (cross-feature prediction prior).**
- `.claude/hub-registry.json` — the hub-file registry maintained out-of-band by the `harvest-hubs` skill from past features' build records. It is **advisory**: a learned prior over which files get written often and by which layers, used in step 3 to pre-seed `touched_files` with hubs *before* any conflict reveals them. Read-only here. Missing, stale, or empty → ignore it and predict from first principles; it never blocks slicing or a freeze.

If the discovery ledger is missing or its `discovery_status` is not `frozen`, **stop** and tell the user to run or complete `grill-discovery`. If the PRD is missing, **stop** and tell the user to run `to-prd`. **Staleness check:** if the PRD's frontmatter carries `source_discovery_hash` and it does not match the sha256 of the current discovery ledger file, the ledger was re-frozen after the PRD was written — **stop** and tell the user to re-run `to-prd`; do not cut slices against a PRD that no longer reflects discovery. (A PRD without the field predates the hash chain; fall back to slug + frozen status.) Do not slice from conversation or from an unfrozen pipeline.

## Process

1. **Load, don't re-derive.** Read the PRD, the discovery ledger, and the glossary. Inherit `intent`, `mvp_scope`, `definition_of_done`, `affected_components`, and `dependencies` — they are settled. Follow `.claude/CLAUDE.md`'s pointers far enough to choose accurate cut points; never re-explore the repo or reopen a frozen decision. If you believe a frozen decision is wrong, do not slice around it — surface it and kick back to `grill-discovery` (for scope/decisions) or `to-prd` (for prose).

2. **Ingest execution feedback — the prediction-improvement loop.** On a first pass there is nothing to ingest; skip to step 3. On any re-entry after Phase 5 kicked a conflict back (an integration conflict it could not trivially resolve), this is where the loop closes:
   - Read the build ledger for slices marked `failed`, and read those slices' build records: `actual_touched_files` for an integration conflict, `planned_touched_files` for a plan-gate halt (no code was written, so the plan is the best available observation).
   - **Replace each affected slice's predicted `touched_files` with the observed set.** The prediction was, by definition, wrong; the observation is ground truth (or, for a gated slice, the closest thing to it).
   - Re-run the file-overlap serialization rule (step 3) across the corrected write-sets. The pair that conflicted will now intersect and gain its serialize edge. If a file turns out to be written by *many* slices, do not just serialize them all into a chain — that is evidence the cut itself is wrong: re-cut to isolate the hub file into its own slice that the others `depend_on`.
   - This is a re-cut / re-serialize, which is this skill's remit. It is **NOT** re-scoping: do not change `mvp_scope`, decisions, or `acceptance` because a conflict occurred. If the conflict instead reveals a genuine scope error (the slices are fighting because the feature is mis-shaped), kick back to `grill-discovery` rather than absorbing it here.
   - Set `slice_status` back to `active`, apply the corrections, and carry them through to re-freeze (steps 6-7). The corrected `touched_files` make the next run's graph serialize this pair, so the conflict does not recur — that is the loop's whole purpose.

3. **Cut vertical slices.** Decompose the feature into **vertical slices**. Each slice cuts through every layer it needs — data, business logic, api, ui — so it is independently testable end to end. Doc-only or pipeline-meta work uses the `docs` layer; never shoehorn it into `business_logic` or `ui`, because `layers` feed the hub registry's matching and invented semantics poison the prior. **Do not decompose by horizontal layer** (one slice for all data, another for all API); that produces increments that can't be tested in isolation. For each slice record `intent`, `layers` touched, `scope`, `size`, `acceptance`, `depends_on`, `shared_surfaces`, and `touched_files`.

   **Estimate `size` at cut time** — `S` (one work unit, 1–2 acceptance criteria), `M`, or `L`. A slice you cannot honestly call `L` — more than ~6 criteria, or more scope than one execution agent holds in its head — is a cut smell: split it before freeze. Size is advisory downstream (Phase 5 may use it to order a dispatch round); it never gates anything. Its job is at the cut: forcing an estimate is what catches the unestimable slice.

   Every `acceptance` clause must be **testable**: phrased as an observable behavior a test can assert, traceable to one of the PRD's test seams — never an internal property ("code is clean") or an unfalsifiable aspiration ("works correctly"). This skill owns that contract: in Phase 5 the execution agent authors its red-green tests directly from these clauses, and a clause no test can be written from **blocks its ticket and kicks back here** for re-phrasing and re-freeze. Write them so that never happens.

   Type each shared surface with its `access` mode — `creates`, `reads`, or `modifies` — and the `slice_id`s it is `shared_with`. Access mode, not raw count, is what lets the downstream orchestrator sequence: a `creates`→`reads` pair is an ordering dependency (it must also appear in `depends_on`); two slices that both `modifies` the same surface must serialize (merge conflict); `reads`/`reads` is safe to parallelize. If two slices share a `modifies` surface, the cut is probably wrong — re-cut or mark the dependency.

   **Predict `touched_files`.** For each slice record a best-effort, coarse list of the files it is expected to create or modify, predicted from the cut point and the area/reference docs `.claude/CLAUDE.md` points to — not from re-exploring the repo. It is a prediction; the loop in step 2 corrects it from build-record actuals once execution reveals the truth. Coarse-but-honest is the goal: list the files you are fairly sure the slice writes, including the unglamorous hubs (route registries, `__init__.py`, schema modules, UI manifests, shared fixtures) that cause most real conflicts.

   Two paths go in **by rule, not guesswork**, for every slice: its build-record path (`.claude/runs/<feature_slug>/<slice_id>.md`) and the test file path(s) its acceptance will be proven with (follow the repo's test layout from `.claude/CLAUDE.md` or the manifests; a self-contained per-slice script when no framework exists). Phase 5's contract guarantees every slice writes both, so a prediction without them leaves every sibling manifest systematically incomplete — a shared test helper or fixture between parallel slices would slip the plan gate. Both are transient process records (archived to the ticket at integration and harvest) and are excluded from the hub fold; the rule serves the plan gate and overlap serialization, not the registry.

   If `.claude/hub-registry.json` is present, cross-reference it to catch hubs *before* the first conflict rather than after: for each registered hub file whose recorded `layers` overlap this slice's `layers`, add it to the slice's `touched_files` unless you have a concrete reason this slice avoids it. This deliberately over-includes — a missed hub costs a merge conflict, while an extra serialization only costs sequential runtime, so the match errs toward inclusion (see [ADR-0001](./ADR-0001.md); do not "tighten" this to exact matching without revisiting that cost asymmetry). This is the proactive complement to step 2's reactive correction — the registry teaches predictions learned on past features, so the reactive loop becomes the fallback for genuinely new hubs, not the only teacher. The registry only *adds* candidate files to the prediction; it never overrides a cut or a decision.

   **File-overlap serialization (physical coupling).** Beyond logical surfaces, intersect the `touched_files` *write-sets* pairwise. Two slices that write the same file are physically coupled even when they share no logical surface. They must serialize: re-cut to remove the overlap if you can, otherwise record the shared file as a `modifies`/`modifies` `shared_surface` (the `surface` is the file path) **and** add the matching `depends_on` edge — exactly as for a logical `modifies` clash, so the downstream graph machinery wires it uniformly. A file in many slices' write-sets is a smell — prefer isolating it into its own slice the others depend on over chaining everyone behind it.

4. **Figure out the tests and coverages (per slice).** Each slice's `acceptance` is its own enumerable Definition of Done — the testable criteria that make it independently verifiable end to end, distinct from the feature-level `definition_of_done`. Ground each slice's acceptance on the **feature-level test seams the PRD already established** (highest seam possible, external behaviour only). If a slice cannot be verified at an existing seam, note the seam it needs rather than silently inventing test architecture that contradicts the PRD — flag it for the user.

5. **Coverage check (before freeze).** Every clause of `mvp_scope` must be owned by at least one slice. Cross-check against the PRD's Solution and User Stories so nothing described there is left unreachable by any slice. If a clause has no owning slice, the decomposition is incomplete — add the missing slice. If the gap reveals a genuine scope error rather than a missing slice, do **not** invent scope here: kick back to `grill-discovery`. Catching a gap here is far cheaper than after tickets exist.

6. **Freeze the slice ledger.** Maintain `.claude/slices/<feature_slug>.json`, keyed to the **same `feature_slug`** as the discovery ledger and PRD. Set `source_prd_hash` to the sha256 of the PRD file the decomposition was cut from (`sha256sum .claude/prds/<feature_slug>.md`) — Phases 4–5 hash this ledger (`ledger_hash`), so recording the PRD hash here chains every downstream artifact back to the exact PRD, and through its `source_discovery_hash`, to the exact discovery freeze. Write it atomically, per the file-write discipline in `.claude/conventions.md`. Treat the validated JSON, not the chat log, as authoritative.

7. **Confirm the decomposition with the user — the only allowed interaction.** Present the slice map (each slice's intent, layers, acceptance, dependency edges, and any file-overlap serializations). In harnesses where the confirmation renders as an option-box question (web/remote), present the slice map as plain **chat text** before asking, and keep the question itself to a bare approve/adjust choice — the map will not fit inside the question UI. This confirms the **partition only**; it must not reopen scope, decisions, or acceptance authored upstream. If the user wants those changed, kick back to the owning phase. On approval, set `slice_status` to `frozen`, do the final validated write, and commit on the release branch (`release/<feature_slug>`) — the same branch the discovery ledger and PRD live on — per the commit conventions in `.claude/conventions.md`. On a re-freeze after a feedback-loop correction (step 2), call out which slices were re-cut or newly serialized and why, so the change from the prior frozen state is legible.

### Slice ledger schema
```json
{
  "feature_slug": "string",
  "slice_status": "active | ready_to_freeze | frozen | needs_human",
  "source_prd_hash": "sha256 of the PRD file this decomposition was cut from",
  "decomposition": [
    {
      "slice_id": "number — 1-based ordinal assigned at cut time; stable across re-freezes (never renumber; a new slice takes the next unused number). Used in branch names (feature/<feature_slug>-<slice_id>), labels, and build-record paths; the descriptive name lives in slice_name",
      "slice_name": "string",
      "intent": "string",
      "layers": ["data | business_logic | api | ui | docs"],
      "scope": "string",
      "size": "S | M | L — coarse effort estimate at cut time; a slice that won't fit L gets split before freeze",
      "acceptance": ["string"],
      "depends_on": ["slice_id"],
      "touched_files": ["string"],
      "shared_surfaces": [
        {
          "surface": "string (logical surface name, or a file path for a touched_files overlap)",
          "shared_with": ["slice_id"],
          "access": "creates | reads | modifies"
        }
      ]
    }
  ]
}
```

### Section sourcing (where each slice field comes from)
- **`intent` / `scope` / `layers`** ← the PRD's Solution and Implementation Decisions, cut into independently shippable increments.
- **`size`** ← judgment at cut time over the slice's scope and criterion count (S/M/L); a slice too big to estimate is split, not guessed at.- **`acceptance`** ← the slice's own enumerable DoD, grounded in the PRD's feature-level test seams (Testing Decisions) and consistent with the ledger's `definition_of_done`.
- **`depends_on` / `shared_surfaces`** ← architectural coupling between slices, typed by access mode — both logical surfaces and file-level surfaces from `touched_files` overlap.
- **`touched_files`** ← best-effort prediction of the files the slice writes, from the cut point and `.claude/CLAUDE.md`'s area docs; **pre-seeded with hubs from `.claude/hub-registry.json`** when present, and **corrected from build-record `actual_touched_files`** whenever Phase 5 kicks a conflict back (step 2).
- **Coverage target** ← the ledger's `mvp_scope`; cross-checked against the PRD's User Stories.
- **Out of scope** → produce NO slice. `explicitly_out_of_scope` is a non-goal, never a slice.

## Guardrails & Hard Rules
- **No implementation, no re-scoping.** The slice ledger is the only file this skill writes. No source code; no new scope or decisions.
- **Single source of truth.** The frozen discovery ledger and PRD govern. If a slice would contradict them, the slice is wrong, not the upstream artifact.
- **Coverage gate.** Do not freeze until every `mvp_scope` clause is owned by ≥1 slice and every slice has non-empty `acceptance` and a `size`.
- **Overlap gate.** Do not freeze until every pair of slices whose `touched_files` write-sets intersect is serialized — a `modifies`/`modifies` shared surface on the shared file plus the matching `depends_on` edge — exactly as for a shared logical `modifies` surface.
- **Learn from execution, don't re-scope.** A Phase-5 conflict kickback is corrected here by replacing predicted `touched_files` with observed actuals and re-cutting/serializing — never by changing scope, decisions, or acceptance. Genuine scope errors still go to `grill-discovery`.
- **The hub registry is advisory and read-only here.** `.claude/hub-registry.json` only sharpens `touched_files` prediction. This skill never writes it (the `harvest-hubs` skill is its single writer), it is never a source of scope or cuts, and its absence or staleness never blocks a freeze.
- **Kick back, don't patch.** If the PRD reveals the feature is mis-shaped, splits into independent features, or duplicates existing work, set `slice_status` to `needs_human` and return to `grill-discovery` — do not absorb a scope decision into the decomposition.

## Exit & Handoff
Done when `slice_status` is `frozen`, every `mvp_scope` clause is owned by ≥1 slice, every slice has non-empty `acceptance` and a `size`, `depends_on` is acyclic, and no two slices share an unserialized `modifies` surface — including file-level `modifies` surfaces from `touched_files` overlap. The slice ledger is committed on the release branch.

Instruct the user to run `to-issues`, which reads this slice ledger by `feature_slug` together with the PRD and projects each slice onto GitHub — one parent issue per slice, sub-issues per work unit, `--blocked-by` edges from `depends_on` and `shared_surfaces` (logical and file-level alike). This skill slices; it does not ticket.
