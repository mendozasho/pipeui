"""Function discovery / parsing (functions domain) — read files, classify, NO DB.

Loads a ``.py`` module (or parses a ``.sql`` header), inspects each candidate function's
signature, and produces the per-function classification dict (or a skip reason) the
registration layer writes. Pure discovery: it reads the filesystem and calls
``classification`` derivations, but touches no DuckDB connection.

Split out of ``registration.py`` (#47): sits between ``classification`` (leaf) and
``registration`` (transaction). ``registration.scan_functions`` calls
``discover_functions_in_file`` / ``discover_sql_functions_in_file``.
"""
from __future__ import annotations

import inspect
import re
import types
from pathlib import Path

from pipeui.backend.domain.functions.classification import (
    annotation_to_str,
    is_known_param_type,
    is_known_return_type,
    derive_function_class,
    derive_function_return_type,
    derive_function_type,
)


# ---------------------------------------------------------------------------
# Per-file function discovery (.py)
# ---------------------------------------------------------------------------

def _load_module(file_path: Path):
    """Import a .py file as a module without adding it to sys.modules permanently.

    Compiles from source directly so stale .pyc bytecode cannot shadow a file
    that was modified within the same process (e.g. during tests or after a
    user edits the file before re-scanning).
    """
    source = file_path.read_text(encoding="utf-8")
    code = compile(source, str(file_path), "exec")
    mod = types.ModuleType(f"_pipeui_scan_{file_path.stem}")
    mod.__file__ = str(file_path)
    # __name__ is used in discover_functions_in_file to filter imported symbols
    exec(code, mod.__dict__)
    return mod


def _inspect_function(fn_name: str, fn_obj) -> dict | str:
    """Inspect one function and return its classification data or a skip reason string.

    Returns a dict with keys:
        param_types, param_names, function_class, function_return_type,
        function_type, function_signature, function_doc
    Or a str describing why the function was skipped.
    """
    sig = inspect.signature(fn_obj)
    params = list(sig.parameters.values())

    # --- eligibility checks ---
    if not params:
        return "function must have at least one parameter"

    for p in params:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            return "variadic parameters not supported"

    for p in params:
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

    # --- derivation ---
    param_names = [p.name for p in params]
    param_types_list = [annotation_to_str(p.annotation) for p in params]  # type: ignore[misc]
    # #258: capture each param's Python default so the executor can fall back to it
    # and the frontend can distinguish required params from optional ones.
    param_has_default = [p.default is not inspect.Parameter.empty for p in params]
    param_default_values = [
        str(p.default) if p.default is not inspect.Parameter.empty else None
        for p in params
    ]
    fn_class = derive_function_class(param_types_list)
    fn_return_type = derive_function_return_type(ret_ann)
    fn_type = derive_function_type(fn_return_type)  # type: ignore[arg-type]
    fn_sig = str(sig)
    fn_doc = inspect.getdoc(fn_obj) or None

    return {
        "param_names": param_names,
        "param_types": param_types_list,
        "param_has_default": param_has_default,
        "param_default_values": param_default_values,
        "function_class": fn_class,
        "function_return_type": fn_return_type,
        "function_type": fn_type,
        "function_signature": fn_sig,
        "function_doc": fn_doc,
    }


def discover_functions_in_file(file_path: Path) -> list[dict]:
    """Return a list of eligible function inspection dicts found in file_path.

    Each item is either:
      {"function_name": str, "data": dict}    — eligible
      {"function_name": str, "skip_reason": str}  — ineligible
    """
    results = []
    try:
        mod = _load_module(file_path)
    except Exception as exc:
        return [{"function_name": "<module>", "skip_reason": f"import error: {exc}"}]

    for name, obj in inspect.getmembers(mod, inspect.isfunction):
        if name.startswith("_"):
            continue
        # Only functions defined in this file (not imported ones)
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        result = _inspect_function(name, obj)
        if isinstance(result, str):
            results.append({"function_name": name, "skip_reason": result})
        else:
            results.append({"function_name": name, "data": result})

    return results


# ---------------------------------------------------------------------------
# Per-file function discovery (.sql)
# ---------------------------------------------------------------------------

_SQL_COMMENT_RE = re.compile(r"^--\s*(\w+)\s*:\s*(.+)$")

# Return-type suffix per function_type for SQL functions
_SQL_RETURN_SUFFIX: dict[str, str] = {
    "transform": "pd.DataFrame",
    "validation": "pd.Series[bool]",
    "unknown": "unknown",
}


def _parse_sql_header(source: str) -> dict | str:
    """Parse the leading comment block of a .sql file.

    Returns a dict with classification data or a str skip reason.
    """
    meta: dict[str, str] = {}
    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue  # skip blank lines (e.g. leading blank line after dedent)
        if not line.startswith("--"):
            break
        m = _SQL_COMMENT_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()

    if "name" not in meta:
        return "missing required `-- name:` header"

    fn_name = meta["name"]
    fn_doc = meta.get("description") or None
    raw_type = meta.get("type", "").lower()

    if raw_type == "transform":
        fn_type = "transform"
    elif raw_type == "validation":
        fn_type = "validation"
    else:
        fn_type = "unknown"

    fn_class = "pd.dataframe"
    fn_return_type = _SQL_RETURN_SUFFIX[fn_type]
    fn_sig = f"{{source_table}}: pd.DataFrame -> {fn_return_type}"

    return {
        "function_name": fn_name,
        "function_doc": fn_doc,
        "function_type": fn_type,
        "function_class": fn_class,
        "function_return_type": fn_return_type,
        "function_signature": fn_sig,
        "param_names": [],
        "param_types": [],
        "param_has_default": [],
        "param_default_values": [],
    }


def discover_sql_functions_in_file(file_path: Path) -> list[dict]:
    """Return a list of SQL function discovery dicts for a .sql file.

    Each item is either:
      {"function_name": str, "data": dict}    — eligible
      {"function_name": str, "skip_reason": str}  — ineligible
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [{"function_name": "<file>", "skip_reason": f"read error: {exc}"}]

    result = _parse_sql_header(source)
    if isinstance(result, str):
        return [{"function_name": file_path.stem, "skip_reason": result}]

    fn_name = result.pop("function_name")
    return [{"function_name": fn_name, "data": result}]
