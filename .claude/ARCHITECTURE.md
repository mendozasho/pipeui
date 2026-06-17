# Architecture — project layer map

> **Status: TARGET.** This is the structure the codebase is migrating *onto*, not (yet) its
> on-disk shape. The current→target map in §4 is authoritative for "where a module is going."
> Module names reflect the post-#28 runner split. Migration is incremental and **out of scope
> for this doc** — see §7.

The runner-resolution-model SRP map (`.claude/CONTEXT.md` → "Runner module responsibilities")
carved up the runner cleanly but only the runner. This doc lifts that same discipline to the
**whole project**: one layer model, one dependency rule, applied fractally.

---

## 1. Three layers (top-level peers)

| Layer | Single responsibility | Current modules |
| --- | --- | --- |
| **`frontend/`** | What the user sees — UI, design, view state. Imports no backend code; talks to `middleware` only over HTTP. | the React app (untouched this pass) |
| **`middleware/`** | The API seam — HTTP routes, request/response shaping, CRUD endpoints, the data↔frontend contracts. Calls `backend` only. | `api/*` |
| **`backend/`** | Everything server-side. Decomposed **per feature** over two sub-layers, `data` (foundation) and `domain` (logic above it). Its size is irrelevant — depth is expressed as nesting. | `workflow/`, `schema/`, `validation/`, `sql_user_table/`, `db`, `results`, `ids` |

---

## 2. The one rule (fractal — holds at every nesting level)

> **Imports flow down, never up.** `frontend → middleware → backend`; inside backend,
> `domain → data`; inside either sub-layer a feature may use its own and the shared `base/`,
> but **features never reach into each other's internals — only each other's published
> contract.**
>
> **Data crosses any boundary only as a declared, frozen carrier** (a behavior-free
> dataclass), never a raw dict and never a reached-into private helper.

A single leading underscore means *module-local*. The moment a sibling module needs a name, that
name is the module's **public contract** and must be public. (This is exactly the rule the
hostile-auditor now enforces — "cross-module private reach-in"; #33 is its first application.)

---

## 3. backend — per feature, over two sub-layers

```
backend/
  data/                     # tables, DDL, access, write-contracts, carriers — imports only data
    base/                   # shared, pulled by every feature
      ids        db         schema/ (DDL + type maps + seeds)
      tables     settings   results        fails
    sources/                # source + column registry contracts
    functions/              # function + function-set registry contracts
    runner/                 # step carriers, bundles, staging store, step loading
  domain/                   # orchestration; owns transactions; called by middleware
    base/                   # shared domain helpers (as they emerge)
    sources/                # create, ingestion, migration
    functions/              # discovery/registration, sets, attach
    runner/                 # run orchestration, executors, resolve, builtins, worker, export
```

`frontend/` may adopt the same per-feature + `base/` shape.

---

## 4. Current → target module map

Re-homing only (no file merges/splits implied here — those are later, per-feature decisions).

### middleware/
| Current | Target |
| --- | --- |
| `api/sources.py` | `middleware/sources.py` |
| `api/functions.py` | `middleware/functions.py` |
| `api/function_sets.py` | `middleware/function_sets.py` |
| `api/pipelines.py` | `middleware/pipelines.py` |
| `api/validations.py` | `middleware/validations.py` |
| `api/builtins.py` | `middleware/builtins.py` |
| `api/settings.py` | `middleware/settings.py` |

### backend/data/
| Current | Target | Note |
| --- | --- | --- |
| `ids.py` | `backend/data/base/ids.py` | foundation; zero pipeui imports |
| `db.py` | `backend/data/base/db.py` | connection + schema lifecycle |
| `schema/constants.py`, `schema/queries.py` | `backend/data/base/schema/` | DDL, type maps, seeds |
| `sql_user_table/__init__.py` | `backend/data/base/tables.py` | instance-table name/DDL builder |
| `results.py` | `backend/data/base/results.py` | `RunResult` / `ValidationRunResult` carriers |
| `validation/settings.py` | `backend/data/base/settings.py` | `AppSettings` |
| `validation/fails.py` | `backend/data/base/fails.py` | failure carriers — cross-feature |
| `validation/source.py` | `backend/data/sources/registry.py` | `SourceRegistry{Entry,Update}` |
| `validation/column.py` | `backend/data/sources/columns.py` | `ColumnRegistry{Entry,Update}` |
| `validation/function_set.py` | `backend/data/functions/sets.py` | `FunctionSet{Entry,Update}` |
| `workflow/step.py` | `backend/data/runner/steps.py` | `StepContext` carriers |
| `workflow/bundles.py` | `backend/data/runner/bundles.py` | argument-bundle pairing |
| `workflow/staging.py` | `backend/data/runner/staging.py` | staging-store I/O |
| `workflow/step_loader.py` | `backend/data/runner/step_loader.py` | pure map-table reads |

### backend/domain/
| Current | Target |
| --- | --- |
| `workflow/create.py` | `backend/domain/sources/create.py` |
| `workflow/ingestion.py` | `backend/domain/sources/ingestion.py` |
| `workflow/migration.py` | `backend/domain/sources/migration.py` |
| `workflow/functions.py` | `backend/domain/functions/registration.py` |
| `workflow/function_sets.py` | `backend/domain/functions/sets.py` |
| `workflow/attach.py` | `backend/domain/functions/attach.py` |
| `workflow/run.py` | `backend/domain/runner/run.py` |
| `workflow/executors.py` | `backend/domain/runner/executors.py` |
| `workflow/resolve.py` | `backend/domain/runner/resolve.py` |
| `workflow/builtins.py` | `backend/domain/runner/builtins.py` |
| `workflow/worker.py` | `backend/domain/runner/worker.py` |
| `workflow/export.py` | `backend/domain/runner/export.py` |

### composition root (cross-cutting — wires layers, owned by none)
| Current | Target |
| --- | --- |
| `main.py`, `config.py`, `helpers.py`, `cli.py` | `app/` (or package root) |

---

## 5. Contracts crossing each seam

Every cross-boundary value is one of these frozen carriers (never an ad-hoc dict):

| Carrier | Defined in (target) | Boundary |
| --- | --- | --- |
| `StepContext` + `FunctionStepContext`/`BuiltinStepContext`, `FunctionSpec` | `data/runner/steps` | data → domain |
| `StepRunEnv`, `StepExecResult` | `domain/runner/executors` | within domain/runner |
| `RunResult` / `ValidationRunResult` | `data/base/results` | produced by domain, consumed by domain + middleware/export |
| `FrameRef` | `domain/runner/resolve` | within domain/runner; flows into `RunResult.consumed_result_id` |
| `SourceRegistry*` / `ColumnRegistry*` / `FunctionSet*` (`*Entry`/`*Update`) | `data/{sources,functions}` | data → domain (write-gating) |
| `FailedRegistryEntry` / `FailedFunctionEntry` | `data/base/fails` | data carrier, used across features |

---

## 6. The generalized layering rule (for the auditor)

Once the layer map is documented, the boundary check generalizes from a hardcoded list to one
rule the hostile-auditor evaluates against this doc. An import is a **finding** when:

- its target sits **above** its source in the layer order (`data` importing `domain`/`middleware`/`frontend`; `domain` importing `middleware`/`frontend`; `middleware` importing `frontend`), **or**
- a feature imports **another feature's non-`base` internals** (cross-feature reach-in) rather than that feature's published contract, **or**
- data crosses a boundary as a **raw dict / reached-into private** instead of a declared carrier from §5.

This subsumes the existing project-specific boundaries (api→workflow only; user functions get
data only; instance table ≠ registry) and the cross-module-private-reach-in rule.

---

## 7. Migration (out of scope here — tracked separately)

Incremental, feature by feature. **First slice = #33** — promote the runner's shared private
helpers (`staging`, `step_loader`) to public contracts; that is precisely a `domain → data`
boundary being made explicit. Subsequent slices relocate modules into the tree per §4, one
feature at a time, each behavior-preserving. Whether to drive the full migration through the
ez-skills pipeline is a separate decision (see #35).
