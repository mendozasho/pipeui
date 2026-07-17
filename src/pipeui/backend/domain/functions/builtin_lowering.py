"""Built-in lowering — filter and date_range as FunctionContracts. #142 (Phase 5a).

The built-ins stop being hand-written SQL executors and become plain contracts
executed through the same engine path as user functions:

  - **filter** → one SQL contract per operator family. The operator is structural
    SQL, so it *selects* the contract rather than binding a value — no enum field
    on the contract (plan: the contract stays minimal; builtins adapt to it).
  - **date_range** → three predicate contracts (``date_in_range`` /
    ``date_on_or_after`` / ``date_on_or_before``), one per bound shape — chosen per
    condition so no contract ever carries a nullable scalar. The one-level DNF
    (conditions AND within a group, groups OR across) is orchestration ABOVE the
    contract (``runner/dnf.py``); each condition is exactly one BoundCall.
  - **rename** → ONE python-engine contract, ``rename_column(df, old_name,
    new_name)``, executed IN-PROCESS (#148 fast path — app-authored source needs no
    worker isolation; its inline source rides on ``contract.body``). The batch
    semantics — simultaneous application, so swaps/chains work — are orchestration:
    the shim runs the legacy global checks, then schedules per-pair calls,
    routing through reserved temp names when a target is also a pending source.

Lowering shims translate the persisted ``builtin_config`` JSON into
``StepBinding``s at run time, so ``source_builtin_map``, the attach/patch
validators, and the UI keep working unchanged. Column names in configs bind as
column params — ``render_sql`` validates them against the working frame's actual
columns (reject-never-quote); values and date bounds ALWAYS travel as ``?``
bound parameters, exactly like the retired executors.

Layer: domain/functions, importing down into domain/runner's engine seams
(``execute_sql_contract``, ``dnf``) — same direction ``builtins.py`` already
imports ``runner.resolve``. Config validation stays with the validators' owner
(``builtins.py`` runs them before delegating), so this module never reaches into
another module's privates.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.data.functions.binding import ParamBinding, StepBinding
from pipeui.backend.data.functions.contract import (
    ENGINE_PYTHON,
    ENGINE_SQL,
    FunctionContract,
    ParamContract,
)
from pipeui.backend.domain.runner.dnf import combine_dnf, normalize_mask
from pipeui.backend.domain.runner.sql_engine import execute_sql_contract


def _sql_contract(name: str, params: tuple[ParamContract, ...], return_type: str, body: str) -> FunctionContract:
    rendered = ", ".join(f"{p.name}: {p.type_str}" for p in params)
    return FunctionContract(
        name=name, engine=ENGINE_SQL, params=params,
        return_type=return_type, signature=f"({rendered}) -> {return_type}",
        body=body,
    )


def _col(name: str, position: int = 0) -> ParamContract:
    return ParamContract(name=name, type_str="pd.Series", position=position)


def _scalar(name: str, type_str: str, position: int) -> ParamContract:
    return ParamContract(name=name, type_str=type_str, position=position)


# ---------------------------------------------------------------------------
# filter — one contract per operator family
# ---------------------------------------------------------------------------

def _comparison_contract(op_name: str, op_sql: str) -> FunctionContract:
    return _sql_contract(
        f"filter_{op_name}", (_col("column"), _scalar("value", "str", 1)),
        "pd.DataFrame",
        f"SELECT * FROM {{source_table}} WHERE {{column}} {op_sql} {{value}}",
    )


FILTER_CONTRACTS: dict[str, FunctionContract] = {
    "eq": _comparison_contract("eq", "="),
    "neq": _comparison_contract("neq", "!="),
    "gt": _comparison_contract("gt", ">"),
    "gte": _comparison_contract("gte", ">="),
    "lt": _comparison_contract("lt", "<"),
    "lte": _comparison_contract("lte", "<="),
    "contains": _sql_contract(
        "filter_contains", (_col("column"), _scalar("value", "str", 1)), "pd.DataFrame",
        "SELECT * FROM {source_table} WHERE CAST({column} AS VARCHAR) LIKE {value}",
    ),
    "not_contains": _sql_contract(
        "filter_not_contains", (_col("column"), _scalar("value", "str", 1)), "pd.DataFrame",
        "SELECT * FROM {source_table} WHERE CAST({column} AS VARCHAR) NOT LIKE {value}",
    ),
    "is_null": _sql_contract(
        "filter_is_null", (_col("column"),), "pd.DataFrame",
        "SELECT * FROM {source_table} WHERE {column} IS NULL",
    ),
    "is_not_null": _sql_contract(
        "filter_is_not_null", (_col("column"),), "pd.DataFrame",
        "SELECT * FROM {source_table} WHERE {column} IS NOT NULL",
    ),
}

# contains/not_contains wrap the raw value in LIKE wildcards at lowering time —
# the contract's value param stays a plain string (the retired executor's shape).
_LIKE_OPERATORS = {"contains", "not_contains"}
_NULLARY_OPERATORS = {"is_null", "is_not_null"}


def _run_contract(
    conn: duckdb.DuckDBPyConnection,
    contract: FunctionContract,
    binding: StepBinding,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """bind → execute one call; a FailedFunctionEntry re-raises as ValueError to
    keep the BuiltinSpec.execute contract (bad config/binding → failed step)."""
    calls = contract.bind(binding)
    result = execute_sql_contract(conn, contract, calls[0], frame=frame)
    if isinstance(result, FailedFunctionEntry):
        reasons = "; ".join(r for _, r in result.failures) or "SQL execution failed"
        raise ValueError(reasons)
    return result


def execute_filter_lowered(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Run a filter built-in through its per-operator contract.

    Config shape unchanged: ``{"column", "operator", "value"}``. The value is a
    string bound as a ``?`` parameter; DuckDB casts it to the column's type for
    comparison (the retired ``_execute_filter`` semantics). The caller
    (``builtins.py``'s BuiltinSpec) has already run the pure config validator.
    """
    operator = cfg["operator"]
    contract = FILTER_CONTRACTS[operator]
    params: list[ParamBinding] = [
        ParamBinding(param_name="column", kind="columns", columns=(cfg["column"],)),
    ]
    if operator not in _NULLARY_OPERATORS:
        value = cfg.get("value")
        if operator in _LIKE_OPERATORS:
            value = f"%{value}%"
        params.append(ParamBinding(param_name="value", kind="literal", value=value))
    return _run_contract(conn, contract, StepBinding(params=tuple(params)), df)


# ---------------------------------------------------------------------------
# date_range — predicate contracts + DNF orchestration
# ---------------------------------------------------------------------------

DATE_IN_RANGE = _sql_contract(
    "date_in_range",
    (_col("dates"), _scalar("start", "date", 1), _scalar("end", "date", 2)),
    "pd.Series[bool]",
    "SELECT CAST({dates} AS DATE) BETWEEN CAST({start} AS DATE) AND CAST({end} AS DATE) FROM {source_table}",
)
DATE_ON_OR_AFTER = _sql_contract(
    "date_on_or_after", (_col("dates"), _scalar("start", "date", 1)), "pd.Series[bool]",
    "SELECT CAST({dates} AS DATE) >= CAST({start} AS DATE) FROM {source_table}",
)
DATE_ON_OR_BEFORE = _sql_contract(
    "date_on_or_before", (_col("dates"), _scalar("end", "date", 1)), "pd.Series[bool]",
    "SELECT CAST({dates} AS DATE) <= CAST({end} AS DATE) FROM {source_table}",
)


def _condition_predicate(cond: dict) -> tuple[FunctionContract, StepBinding]:
    """Pick the predicate contract for a condition's bound shape and bind it.

    Both bounds → ``date_in_range``; start only → ``date_on_or_after``; end only →
    ``date_on_or_before``. Validation guarantees at least one bound is set, so no
    contract ever needs a nullable scalar.
    """
    column = ParamBinding(param_name="dates", kind="columns", columns=(cond["column"],))
    start, end = cond.get("start"), cond.get("end")
    has_start, has_end = start not in (None, ""), end not in (None, "")
    if has_start and has_end:
        return DATE_IN_RANGE, StepBinding(params=(
            column,
            ParamBinding(param_name="start", kind="literal", value=start),
            ParamBinding(param_name="end", kind="literal", value=end),
        ))
    if has_start:
        return DATE_ON_OR_AFTER, StepBinding(params=(
            column, ParamBinding(param_name="start", kind="literal", value=start),
        ))
    return DATE_ON_OR_BEFORE, StepBinding(params=(
        column, ParamBinding(param_name="end", kind="literal", value=end),
    ))


def execute_date_range_lowered(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Run a date_range built-in as DNF-orchestrated predicate-contract calls.

    Each condition is ONE BoundCall of a predicate contract producing a boolean
    mask (NULL dates fail their condition — normalized to False); masks AND
    within a group, OR across groups (``dnf.combine_dnf``); the final mask
    filters the working frame. Semantics identical to the retired
    ``_execute_date_range`` (inclusive bounds, DATE-granularity casts, a NULL
    row may still pass via another OR group). The caller (``builtins.py``'s
    BuiltinSpec) has already run the pure config validator.
    """
    n_rows = len(df)
    group_masks: list[list[pd.Series]] = []
    for group in cfg["groups"]:
        cond_masks: list[pd.Series] = []
        for cond in group["conditions"]:
            contract, binding = _condition_predicate(cond)
            result = _run_contract(conn, contract, binding, df)
            cond_masks.append(normalize_mask(result.iloc[:, 0], n_rows))
        group_masks.append(cond_masks)

    keep = combine_dnf(group_masks, n_rows)
    return df[keep.values].reset_index(drop=True)


# ---------------------------------------------------------------------------
# rename — one python-engine contract + simultaneous-apply orchestration
# ---------------------------------------------------------------------------

_RENAME_SOURCE = '''def rename_column(df, old_name, new_name):
    return df.rename(columns={old_name: new_name})
'''

RENAME_COLUMN = FunctionContract(
    name="rename_column",
    engine=ENGINE_PYTHON,
    params=(
        ParamContract(name="df", type_str="pd.DataFrame", position=0),
        ParamContract(name="old_name", type_str="str", position=1),
        ParamContract(name="new_name", type_str="str", position=2),
    ),
    return_type="pd.DataFrame",
    signature="(df: pd.DataFrame, old_name: str, new_name: str) -> pd.DataFrame",
    body=_RENAME_SOURCE,
)


def _run_python_contract_inprocess(contract: FunctionContract, call, frame: pd.DataFrame):
    """Execute an APP-AUTHORED python contract in-process (#148 fast path).

    The worker's process isolation is an accident boundary for USER code
    (Principle 5). A contract whose source this module authors (``contract.body``)
    is the backend's own code — the same trust level as the executor calling it —
    so a subprocess per call buys no safety and costs a process spawn + two Arrow
    round-trips. Semantics mirror ``realize``'s table/value modes: the frame under
    the table param's name (first param fallback), literals as kwargs.

    ONLY for contracts defined in this module. User code always goes through
    ``realize`` and the worker.
    """
    namespace: dict = {"pd": pd}
    exec(contract.body, namespace)  # noqa: S102 — app-authored source, never user code
    fn = namespace[contract.name]
    kwarg = call.table_params[0] if call.table_params else contract.params[0].name
    return fn(**{kwarg: frame}, **dict(call.literal_kwargs))


def _apply_rename(df: pd.DataFrame, old: str, new: str) -> pd.DataFrame:
    """One contract call: rename a single column (in-process fast path — the
    contract is app-authored, see ``_run_python_contract_inprocess``)."""
    binding = StepBinding(params=(
        ParamBinding(param_name="df", kind="table"),
        ParamBinding(param_name="old_name", kind="literal", value=old),
        ParamBinding(param_name="new_name", kind="literal", value=new),
    ))
    call = RENAME_COLUMN.bind(binding)[0]
    return _run_python_contract_inprocess(RENAME_COLUMN, call, df)


def _rename_schedule(cols: list[str], renames: dict[str, str]) -> list[tuple[str, str]]:
    """Order the per-pair calls so sequential application equals SIMULTANEOUS
    application of the whole mapping (the legacy ``df.rename`` semantics).

    When no target is also a pending source, the pairs apply directly. A swap or
    chain (a→b, b→a / a→b, b→c) routes through reserved temp names: pass 1 moves
    every source to a collision-free temp, pass 2 moves temps to their targets.
    """
    pairs = list(renames.items())
    sources = set(renames.keys())
    needs_two_pass = any(new in sources and new != old for old, new in pairs)
    if not needs_two_pass:
        return pairs

    taken = set(cols) | set(renames.values())
    schedule: list[tuple[str, str]] = []
    temps: list[tuple[str, str]] = []
    for i, (old, new) in enumerate(pairs):
        tmp = f"__pipeui_ren_{i}"
        while tmp in taken:
            tmp += "_x"
        taken.add(tmp)
        schedule.append((old, tmp))
        temps.append((tmp, new))
    return schedule + temps


def execute_rename_lowered(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Run a rename built-in as per-pair ``rename_column`` contract calls.

    The retired ``_execute_rename``'s batch checks run FIRST (identical
    messages, frame untouched on error): every source column must exist in the
    working frame; no target may collide with a *surviving* column (one not
    itself renamed away — so swaps are legal). The scheduled calls then
    reproduce simultaneous application. The caller (``builtins.py``'s
    BuiltinSpec) has already run the pure config validator.
    """
    renames: dict[str, str] = cfg["renames"]
    cols = list(df.columns)

    missing = [old for old in renames if old not in cols]
    if missing:
        raise ValueError(f"rename: column(s) not found in the working data: {missing}")

    surviving = set(cols) - set(renames.keys())
    collisions = sorted({new for new in renames.values() if new in surviving})
    if collisions:
        raise ValueError(f"rename: target name(s) already exist in the working data: {collisions}")

    current = df
    for old, new in _rename_schedule(cols, renames):
        current = _apply_rename(current, old, new)
    return current


# ---------------------------------------------------------------------------
# join — factory-parameterized contracts + shim-owned execution (#146)
# ---------------------------------------------------------------------------

# Structural SQL vocabularies — these become template text, so they are closed
# sets enforced HERE at lowering time (the retired executor interpolated
# join_type from config verbatim; the whitelist is a hardening gain).
_JOIN_TYPES = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL"}
_PIVOT_AGGREGATIONS = {"sum", "avg", "min", "max", "count"}


def join_contract(join_type: str, n_keys: int, keep_all: bool) -> FunctionContract:
    """Build the join contract for one (join_type, key count, keep) shape.

    Like filter's per-operator contracts, the structural parts of the SQL select
    the contract instead of binding values. Each key pair is its own pair of
    single-column params (``left_on_i`` / ``right_on_i``), so binding never
    multi-select-expands — a composite key is N params in ONE call, not N calls.
    """
    sql_join = _JOIN_TYPES[join_type]
    params: list[ParamContract] = [
        ParamContract(name="left", type_str="pd.DataFrame", position=0),
        ParamContract(name="right", type_str="source_ref", position=1),
    ]
    on_parts: list[str] = []
    for i in range(n_keys):
        params.append(ParamContract(name=f"left_on_{i}", type_str="pd.Series", position=2 + 2 * i))
        params.append(ParamContract(name=f"right_on_{i}", type_str="pd.Series", position=3 + 2 * i))
        on_parts.append(f"{{left}}.{{left_on_{i}}} = {{right}}.{{right_on_{i}}}")
    select = "{left}.*, {right}.*" if keep_all else "*"
    body = f"SELECT {select} FROM {{left}} {sql_join} JOIN {{right}} ON {' AND '.join(on_parts)}"
    rendered = ", ".join(f"{p.name}: {p.type_str}" for p in params)
    return FunctionContract(
        name=f"join_{join_type}_{n_keys}key", engine=ENGINE_SQL, params=tuple(params),
        return_type="pd.DataFrame", signature=f"({rendered}) -> pd.DataFrame",
        body=body,
    )


def execute_join_lowered(conn, df, cfg, run_transforms=None):
    """Run a join built-in through its factory contract; shim-owned execution.

    The shim owns what the generic engine path cannot: resolving the right frame
    per ``use_transformed`` (RAW instance table vs resolved transformed output,
    materializing a never-run right source via the injected ``run_transforms``),
    the consumed-result lineage, and the per-side column vocabularies (left keys
    validate against the working frame, right keys against the resolved right
    frame — reject-never-quote on both sides).

    Returns ``(result_df, consumed_result_id)`` exactly like the retired
    ``_execute_join``. The caller has already run the pure config validator.
    """
    import uuid as _uuid

    from pipeui.backend.domain.runner.resolve import RAW, TRANSFORMED, resolve_frame
    from pipeui.backend.domain.runner.sql_engine import render_sql

    join_type = cfg.get("join_type", "inner")
    if join_type not in _JOIN_TYPES:
        raise ValueError(f"join_type must be one of {sorted(_JOIN_TYPES)!r}; got {join_type!r}")
    on_clauses = cfg["on"]
    keep_all = cfg.get("keep_columns", "all") == "all"

    contract = join_contract(join_type, len(on_clauses), keep_all)
    bindings: list[ParamBinding] = [
        ParamBinding(param_name="left", kind="table"),
        ParamBinding(param_name="right", kind="source_ref", source_ref=cfg["right_source_id"]),
    ]
    for i, clause in enumerate(on_clauses):
        bindings.append(ParamBinding(
            param_name=f"left_on_{i}", kind="columns", columns=(clause["left_col"],),
        ))
        bindings.append(ParamBinding(
            param_name=f"right_on_{i}", kind="columns", columns=(clause["right_col"],),
        ))
    call = contract.bind(StepBinding(params=tuple(bindings)))[0]

    mode = TRANSFORMED if cfg.get("use_transformed") else RAW
    right_df, ref = resolve_frame(
        conn, _uuid.UUID(cfg["right_source_id"]), mode, run_transforms=run_transforms,
    )
    consumed_result_id = ref.result_id if mode == TRANSFORMED else None

    left_view = f"__pipeui_join_left_{_uuid.uuid4().hex[:8]}"
    right_view = f"__pipeui_join_right_{_uuid.uuid4().hex[:8]}"
    conn.register(left_view, df)
    conn.register(right_view, right_df)
    vocab = {}
    for i in range(len(on_clauses)):
        vocab[f"left_on_{i}"] = set(df.columns)
        vocab[f"right_on_{i}"] = set(right_df.columns)
    try:
        sql, bound = render_sql(
            contract, call, body=contract.body,
            views={"left": left_view, "right": right_view},
            columns_available=vocab,
        )
        result = conn.execute(sql, bound).df()
    finally:
        for view in (left_view, right_view):
            try:
                conn.unregister(view)
            except Exception:
                pass
    return result, consumed_result_id


# ---------------------------------------------------------------------------
# pivot — factory-parameterized contract through the generic engine path (#146)
# ---------------------------------------------------------------------------

def pivot_contract(using_aggs: list[str], n_index: int) -> FunctionContract:
    """Build the pivot contract for one (aggregation list, index count) shape.

    ``using_aggs`` are whitelisted aggregation names (structural SQL — they
    select the contract); each USING part and each GROUP BY column is its own
    single-column param.
    """
    params: list[ParamContract] = [
        ParamContract(name="table", type_str="pd.DataFrame", position=0),
        ParamContract(name="pivot_column", type_str="pd.Series", position=1),
    ]
    using_parts: list[str] = []
    for i, agg in enumerate(using_aggs):
        params.append(ParamContract(name=f"value_{i}", type_str="pd.Series", position=2 + i))
        using_parts.append(f"{agg}({{value_{i}}})")
    for j in range(n_index):
        params.append(ParamContract(
            name=f"index_{j}", type_str="pd.Series", position=2 + len(using_aggs) + j,
        ))
    group = (
        " GROUP BY " + ", ".join(f"{{index_{j}}}" for j in range(n_index))
        if n_index else ""
    )
    body = f"PIVOT {{table}} ON {{pivot_column}} USING {', '.join(using_parts)}{group}"
    rendered = ", ".join(f"{p.name}: {p.type_str}" for p in params)
    return FunctionContract(
        name=f"pivot_{'_'.join(using_aggs)}_{n_index}idx", engine=ENGINE_SQL,
        params=tuple(params), return_type="pd.DataFrame",
        signature=f"({rendered}) -> pd.DataFrame", body=body,
    )


def execute_pivot_lowered(conn, df, cfg):
    """Run a pivot built-in through its factory contract via the generic engine.

    Config shape unchanged. Each (value column, aggregation) combination becomes
    one USING param (aggregations default to ["sum"], the retired executor's
    fallback); aggregation names are whitelisted at lowering time (they are
    template text — the retired executor interpolated them verbatim). The caller
    has already run the pure config validator.
    """
    using_aggs: list[str] = []
    using_cols: list[str] = []
    for vc in cfg["value_columns"]:
        col_name = vc.get("col_name") or vc.get("col_id", "value")
        for agg in vc.get("aggregations", ["sum"]):
            if agg not in _PIVOT_AGGREGATIONS:
                raise ValueError(
                    f"unknown aggregations [{agg!r}]; valid: {sorted(_PIVOT_AGGREGATIONS)!r}"
                )
            using_aggs.append(agg)
            using_cols.append(col_name)
    index_cols = cfg.get("index_columns", [])

    contract = pivot_contract(using_aggs, len(index_cols))
    bindings: list[ParamBinding] = [
        ParamBinding(param_name="table", kind="table"),
        ParamBinding(param_name="pivot_column", kind="columns", columns=(cfg["pivot_column"],)),
    ]
    for i, col in enumerate(using_cols):
        bindings.append(ParamBinding(param_name=f"value_{i}", kind="columns", columns=(col,)))
    for j, col in enumerate(index_cols):
        bindings.append(ParamBinding(param_name=f"index_{j}", kind="columns", columns=(col,)))
    return _run_contract(conn, contract, StepBinding(params=tuple(bindings)), df)
