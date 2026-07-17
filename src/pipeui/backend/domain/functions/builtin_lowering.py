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
from pipeui.backend.data.functions.contract import ENGINE_SQL, FunctionContract, ParamContract
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
