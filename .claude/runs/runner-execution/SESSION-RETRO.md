# runner-execution — session retro & handoff

Status of PR #253 (`release/runner-execution` → `release/v0.0.1-beta`, still OPEN, not merged).

## What shipped this session (all folded into #253)
The 6 frozen slices (1–6) integrated, then **10 corrective addenda** found during manual testing:

| # | What | Why it was needed |
|---|---|---|
| 4b (#251) | persist user append name | UI collected it, server dropped it |
| 4c (#254) | registry migration for pre-feature DBs | get_pipeline 500 on existing DBs |
| 4d (#256) | builtin palette detail drawer | builtins had no drawer |
| scalar (#258) | executor reads source_scalar_map | functions w/ scalar params couldn't run (0/0) |
| colorder (#260) | current_bindings ORDER BY position | column order reset on edit |
| nan-export (#262) | scrub NaN/inf in staging export | transformed export 500 on null data |
| outcfg (#264) | per-function output config + auto-label fn_name_column | append cols mis-named; config per-set not per-function |
| setiter (#266) | run every function in a set by its own type | validations in mixed sets silently dropped |
| anno (#268) | worker compiles user src with future-annotations | `pd.Series[bool]` crashed the worker |
| noise (#270) | strip setrlimit noise from worker errors | real errors buried under macOS warning |

Backend ~448 py tests green (1 pre-existing macOS `setrlimit` OOM failure, env-only). Frontend ~80 green.

## STILL OPEN — pick up here next session
1. **Validations export still not correct (user-reported, NOT resolved).** On `customers` the mixed-set
   validations now RUN (setiter+anno fixes), but `within_range` is bound to `customer_id` (a string) and
   `is_positive` is bound to NO column, so both fail. User says the export is still wrong — so EITHER the
   bindings need fixing in the app AND/OR there is a further issue beyond the bindings. **Re-investigate the
   `/pipelines/{source}/export/results` output on customers from scratch.** Use realistic data; do not assume.
2. **#264 per-function output-config modal UI (backend done, UI remaining).** Multi-function sets need
   per-function `output_mode` + append-name inputs; API per-function payload; edit round-trip (Principle 7).
3. **Design-gated, untouched:** #224 polished drag-reorder pane, #225 rich results drawer (blocked-on-design).
4. **Pre-existing:** `test_worker.py::test_oom_worker_killed_by_setrlimit` fails on macOS (Unix-only v1).

## The recurring root-cause pattern (the real retro lesson)
Every addendum above came from the SAME failure mode: **slice tests used clean, single-case synthetic data**,
so real data broke paths the green suite "covered":
- null-free rows → NaN export 500 (#262)
- single-function sets → mixed-set drop (#266) and per-set output config (#264)
- seeded scalar values present → source_scalar_map never read (#258)
- tidy annotations → `pd.Series[bool]` worker crash (#268)
**Fixes:** test with nulls/edge cases/multi-function+mixed sets/real annotations, AND live-verify each PR
"How to verify" item against real seeded data — a green suite on synthetic data is not proof. Discovery/to-PRD
must verify assumed wiring against actual code, not assume it. (Memories: pipeui-test-with-realistic-data,
pipeui-runner-single-source-and-discovery-grounding, pipeui-edits-preserve-persisted-values.)

## Mechanics for the next session
- Accumulator `release/runner-execution`; final PR #253 → `release/v0.0.1-beta` (NOT main).
- Run agents/tests off iCloud: `PYTHONPATH=src <repo>/.venv/bin/python -m pytest`; frontend `node_modules/.bin/vitest`
  (npm ci if jsdom dataless). Dev server: `./start.sh start` (reloads on change; spawns fresh workers).
- Parents #218–#223 stay open until the user merges #253.
