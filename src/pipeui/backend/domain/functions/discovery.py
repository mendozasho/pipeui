"""Function discovery / extraction (functions domain) — screen, read, contract. NO DB.

Loads a ``.py`` module (or parses a ``.sql`` header) and produces one
``FunctionContract`` per eligible function — the universal interface every consumer
reads (#134). ``.py`` sources pass the AST guardrail screen (``guardrails.py``)
BEFORE they are exec'd for signature extraction: a ``block`` finding rejects the
whole module and its top-level code never runs.

``extract_contracts`` is the single entry point; ``registration.scan_functions``
consumes it. (The legacy ``discover_*_in_file`` dict-shape wrappers were removed
in #136.)

Pure discovery: it reads the filesystem and calls ``classification`` derivations,
but touches no DuckDB connection.
"""
from __future__ import annotations

import inspect
import re
import types
from dataclasses import dataclass
from pathlib import Path

from pipeui.backend.data.functions.classification import (
    annotation_to_str,
    is_known_param_type,
    is_known_return_type,
)
from pipeui.backend.data.functions.contract import (
    ENGINE_PYTHON,
    ENGINE_SQL,
    FunctionContract,
    ParamContract,
)
from pipeui.backend.domain.functions.guardrails import ScreenFinding, screen_module


@dataclass(frozen=True)
class DiscoveredFunction:
    """One extraction result.

    Exactly one of ``contract`` / ``skip_reason`` is set for a function entry.
    Module-level screen ``flags`` (accepted-but-surfaced findings) ride on a
    dedicated ``<module>`` entry with neither contract nor skip_reason.
    """

    function_name: str
    contract: FunctionContract | None = None
    skip_reason: str | None = None
    flags: tuple[ScreenFinding, ...] = ()


# ---------------------------------------------------------------------------
# Extraction entry point
# ---------------------------------------------------------------------------

def extract_contracts(file_path: Path) -> list[DiscoveredFunction]:
    """Extract every function contract from a ``.py`` or ``.sql`` file."""
    if file_path.suffix == ".sql":
        return _extract_sql(file_path)
    return _extract_py(file_path)


# ---------------------------------------------------------------------------
# Per-file function discovery (.py)
# ---------------------------------------------------------------------------

def _load_module(file_path: Path, source: str):
    """Exec an already-screened .py source as a module, without touching sys.modules.

    Compiles from source directly so stale .pyc bytecode cannot shadow a file
    that was modified within the same process (e.g. during tests or after a
    user edits the file before re-scanning). Callers MUST run
    ``guardrails.screen_module`` first — this function trusts its input.
    """
    code = compile(source, str(file_path), "exec")
    mod = types.ModuleType(f"_pipeui_scan_{file_path.stem}")
    mod.__file__ = str(file_path)
    # __name__ is used below to filter imported symbols from defined ones
    exec(code, mod.__dict__)
    return mod


def _inspect_function(fn_name: str, fn_obj, *, source_path: str | None = None) -> FunctionContract | str:
    """Inspect one function: its ``FunctionContract``, or a skip reason string."""
    sig = inspect.signature(fn_obj)
    sig_params = list(sig.parameters.values())

    # --- eligibility checks ---
    if not sig_params:
        return "function must have at least one parameter"

    for p in sig_params:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            return "variadic parameters not supported"

    for p in sig_params:
        ann = annotation_to_str(p.annotation)
        if ann is None:
            return f"untyped parameter `{p.name}`"
        if not is_known_param_type(ann):
            return f"unsupported parameter type `{ann}` on `{p.name}`"

    ret_ann = annotation_to_str(sig.return_annotation)
    if ret_ann is None:
        return "missing return annotation"
    if not is_known_return_type(ret_ann):
        return f"unsupported return type `{ret_ann}`"

    # --- the contract: params in signature order, defaults captured (#258) ---
    params = tuple(
        ParamContract(
            name=p.name,
            type_str=annotation_to_str(p.annotation),  # type: ignore[arg-type]
            position=i,
            has_default=p.default is not inspect.Parameter.empty,
            default_value=str(p.default) if p.default is not inspect.Parameter.empty else None,
        )
        for i, p in enumerate(sig_params)
    )
    return FunctionContract(
        name=fn_name,
        engine=ENGINE_PYTHON,
        params=params,
        return_type=ret_ann,
        signature=str(sig),
        doc=inspect.getdoc(fn_obj) or None,
        source_path=source_path,
    )


def _extract_py(file_path: Path) -> list[DiscoveredFunction]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [DiscoveredFunction("<module>", skip_reason=f"read error: {exc}")]

    # Guardrail screen BEFORE any exec of user code (#134).
    findings = screen_module(source, filename=str(file_path))
    blocks = [f for f in findings if f.severity == "block"]
    flags = tuple(f for f in findings if f.severity == "flag")
    if blocks:
        detail = "; ".join(f"{b.detail} (line {b.lineno})" for b in blocks)
        return [DiscoveredFunction(
            "<module>", skip_reason=f"blocked by static screen: {detail}", flags=flags,
        )]

    results: list[DiscoveredFunction] = []
    if flags:
        results.append(DiscoveredFunction("<module>", flags=flags))

    try:
        mod = _load_module(file_path, source)
    except Exception as exc:
        return [DiscoveredFunction("<module>", skip_reason=f"import error: {exc}")]

    for name, obj in inspect.getmembers(mod, inspect.isfunction):
        if name.startswith("_"):
            continue
        # Only functions defined in this file (not imported ones)
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        result = _inspect_function(name, obj, source_path=str(file_path))
        if isinstance(result, str):
            results.append(DiscoveredFunction(name, skip_reason=result))
        else:
            results.append(DiscoveredFunction(name, contract=result))

    return results


# ---------------------------------------------------------------------------
# Per-file function discovery (.sql)
# ---------------------------------------------------------------------------

_SQL_COMMENT_RE = re.compile(r"^--\s*(\w+)\s*:\s*(.+)$")
# #140: `-- param: <name> <type> [= <default>]` declares one template parameter.
_SQL_PARAM_RE = re.compile(r"^--\s*param\s*:\s*(\w+)\s+(\w+)(?:\s*=\s*(.+))?$")

# `-- param:` type spellings → canonical contract param types. `column` binds a
# source column (rendered as a validated identifier); scalars bind as `?` values;
# `table` receives the working frame as a registered view.
_SQL_PARAM_TYPES: dict[str, str] = {
    "column": "pd.Series",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "str": "str",
    "date": "date",
    "table": "pd.DataFrame",
}

# Return-type suffix per function_type for SQL functions
_SQL_RETURN_SUFFIX: dict[str, str] = {
    "transform": "pd.DataFrame",
    "validation": "pd.Series[bool]",
    "unknown": "unknown",
}


def _parse_sql_header(source: str) -> dict | str:
    """Parse the leading comment block of a .sql file.

    Returns ``{"function_name", "function_doc", "return_type", "signature",
    "params"}`` (``params`` = ordered ``ParamContract`` tuple, empty for the
    legacy implicit whole-table shape) or a str skip reason.
    """
    meta: dict[str, str] = {}
    params: list[ParamContract] = []
    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue  # skip blank lines (e.g. leading blank line after dedent)
        if not line.startswith("--"):
            break
        pm = _SQL_PARAM_RE.match(line)
        if pm:
            p_name, raw_type, raw_default = pm.group(1), pm.group(2).lower(), pm.group(3)
            type_str = _SQL_PARAM_TYPES.get(raw_type)
            if type_str is None:
                return (
                    f"unsupported `-- param:` type `{raw_type}` on `{p_name}` "
                    f"(expected one of: {', '.join(sorted(_SQL_PARAM_TYPES))})"
                )
            if raw_default is not None and type_str in ("pd.Series", "pd.DataFrame"):
                return f"`-- param: {p_name}` — only scalar params may declare a default"
            params.append(ParamContract(
                name=p_name,
                type_str=type_str,
                position=len(params),
                has_default=raw_default is not None,
                default_value=raw_default.strip() if raw_default is not None else None,
            ))
            continue
        m = _SQL_COMMENT_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()

    if "name" not in meta:
        return "missing required `-- name:` header"

    raw_type = meta.get("type", "").lower()
    fn_type = raw_type if raw_type in ("transform", "validation") else "unknown"
    fn_return_type = _SQL_RETURN_SUFFIX[fn_type]

    if params:
        rendered = ", ".join(
            f"{p.name}: {p.type_str}"
            + (f" = {p.default_value}" if p.has_default else "")
            for p in params
        )
        signature = f"({rendered}) -> {fn_return_type}"
    else:
        signature = f"{{source_table}}: pd.DataFrame -> {fn_return_type}"

    return {
        "function_name": meta["name"],
        "function_doc": meta.get("description") or None,
        "return_type": fn_return_type,
        "signature": signature,
        "params": tuple(params),
    }


def _extract_sql(file_path: Path) -> list[DiscoveredFunction]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [DiscoveredFunction("<file>", skip_reason=f"read error: {exc}")]

    parsed = _parse_sql_header(source)
    if isinstance(parsed, str):
        return [DiscoveredFunction(file_path.stem, skip_reason=parsed)]

    # A header with no `-- param:` declarations is the implicit whole-table shape:
    # zero params, function_class "pd.dataframe" (derived), body = the SQL text.
    contract = FunctionContract(
        name=parsed["function_name"],
        engine=ENGINE_SQL,
        params=parsed["params"],
        return_type=parsed["return_type"],
        signature=parsed["signature"],
        doc=parsed["function_doc"],
        source_path=str(file_path),
        body=source,
    )
    return [DiscoveredFunction(contract.name, contract=contract)]


