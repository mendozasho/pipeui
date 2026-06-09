"""Function discovery, classification, and registration workflow.

§10: function_registry row + all parameter rows = one transaction per function.
§11: function_class / function_type / function_return_type derivation.
§2:  content_hash_id = uuid5(table_namespace, function_name|function_class|function_return_type)
Principle 2: collapse on content_hash_id — preserve surrogate function_id, overwrite mutables.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import uuid
from pathlib import Path
from typing import Any

import duckdb

from pipeui.ids import content_hash_id, new_id

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

# Param-type granularity ordering (lower index = more granular / higher
# granularity = more scalar-like).  §11: function_class is the *least*
# granular (highest index) parameter type.
_PARAM_GRANULARITY: dict[str, int] = {
    "int": 0,
    "float": 0,
    "bool": 0,
    "str": 1,          # may be column_backed — resolved at attach time
    "pd.Series[bool]": 2,
    "pd.Series": 2,
    "pd.DataFrame": 3,
}

# function_class derived from the least-granular (highest index) param_type
_GRANULARITY_TO_CLASS: dict[int, str] = {
    0: "scalar",
    1: "scalar",        # unaliased str → scalar at scan time (column_backed resolved at attach)
    2: "pd.Series",
    3: "pd.dataframe",
}

# function_return_type vocabulary (CONTEXT.md)
_RETURN_TYPE_MAP: dict[str, str] = {
    "int": "scalar",
    "float": "scalar",
    "str": "scalar",
    "bool": "boolean",
    "pd.Series": "pd.Series",
    "pd.Series[bool]": "pd.Series[bool]",
    "pd.DataFrame": "pd.DataFrame",
}

# function_type: validation iff return is boolean or pd.Series[bool]
_VALIDATION_RETURNS = {"boolean", "pd.Series[bool]"}


def _annotation_to_str(annotation: Any) -> str | None:
    """Convert a parameter/return annotation to its canonical param_type string.

    Returns None when the annotation is inspect.Parameter.empty / inspect.Signature.empty.
    """
    if annotation is inspect.Parameter.empty or annotation is inspect.Signature.empty:
        return None
    # Use the string representation; handle common subscripted generics
    ann_str = str(annotation)
    # typing representations → canonical form
    replacements = {
        "pandas.core.series.Series": "pd.Series",
        "pandas.core.frame.DataFrame": "pd.DataFrame",
        "<class 'int'>": "int",
        "<class 'float'>": "float",
        "<class 'bool'>": "bool",
        "<class 'str'>": "str",
    }
    for old, new in replacements.items():
        ann_str = ann_str.replace(old, new)
    # Handle typing.Optional, etc. — not in scope for v1; unsupported types will
    # fail the "not in known set" check in the caller.
    return ann_str


def _is_known_param_type(type_str: str) -> bool:
    return type_str in _PARAM_GRANULARITY


def _is_known_return_type(type_str: str) -> bool:
    return type_str in _RETURN_TYPE_MAP


def derive_function_class(param_types: list[str]) -> str:
    """Derive function_class from the list of param_type strings (§11).

    The least-granular (highest granularity-index) param drives the class.
    """
    max_granularity = max(_PARAM_GRANULARITY[pt] for pt in param_types)
    return _GRANULARITY_TO_CLASS[max_granularity]


def derive_function_return_type(return_annotation_str: str) -> str | None:
    """Map a return annotation string to function_return_type vocabulary (CONTEXT.md)."""
    return _RETURN_TYPE_MAP.get(return_annotation_str)


def derive_function_type(function_return_type: str) -> str:
    """Derive function_type from function_return_type (§11 / CONTEXT.md)."""
    return "validation" if function_return_type in _VALIDATION_RETURNS else "transform"


# ---------------------------------------------------------------------------
# Per-file function discovery
# ---------------------------------------------------------------------------

def _load_module(file_path: Path):
    """Import a .py file as a module without adding it to sys.modules permanently.

    Compiles from source directly so stale .pyc bytecode cannot shadow a file
    that was modified within the same process (e.g. during tests or after a
    user edits the file before re-scanning).
    """
    import types
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
        ann = _annotation_to_str(p.annotation)
        if ann is None:
            return f"untyped parameter `{p.name}`"
        if not _is_known_param_type(ann):
            return f"unsupported parameter type `{ann}` on `{p.name}`"

    ret_ann = _annotation_to_str(sig.return_annotation)
    if ret_ann is None:
        return "missing return annotation"
    if not _is_known_return_type(ret_ann):
        return f"unsupported return type `{ret_ann}`"

    # --- derivation ---
    param_names = [p.name for p in params]
    param_types_list = [_annotation_to_str(p.annotation) for p in params]  # type: ignore[misc]
    fn_class = derive_function_class(param_types_list)
    fn_return_type = derive_function_return_type(ret_ann)
    fn_type = derive_function_type(fn_return_type)  # type: ignore[arg-type]
    fn_sig = str(sig)
    fn_doc = inspect.getdoc(fn_obj) or None

    return {
        "param_names": param_names,
        "param_types": param_types_list,
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
# Registration (workflow layer — owns the DB connection and transactions)
# ---------------------------------------------------------------------------

def _register_one_function(
    conn: duckdb.DuckDBPyConnection,
    file_path: Path,
    fn_name: str,
    data: dict,
) -> str:
    """Register a single function in one transaction.  Returns 'added' or 're-registered'.

    Implements §10 registration transaction + Principle 2 collapse logic.
    Raises on unexpected error (caller catches and logs skip).
    """
    fn_class = data["function_class"]
    fn_return_type = data["function_return_type"]

    # §2 content_hash_id: uuid5(table_namespace("function_registry"), name|class|return_type)
    chid = content_hash_id("function_registry", fn_name, fn_class, fn_return_type)

    # Principle 2: check for existing row with same content_hash_id
    existing = conn.execute(
        "SELECT function_id FROM function_registry WHERE content_hash_id = ?",
        [chid],
    ).fetchone()

    if existing:
        function_id = existing[0]
        status = "re-registered"
    else:
        function_id = new_id()
        status = "added"

    param_names: list[str] = data["param_names"]
    param_types: list[str] = data["param_types"]

    conn.execute("BEGIN")
    try:
        if status == "added":
            conn.execute(
                """
                INSERT INTO function_registry
                    (function_id, content_hash_id, function_class, function_name,
                     function_doc, function_return_type, function_signature,
                     function_type, module_path, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
                """,
                [
                    function_id,
                    chid,
                    fn_class,
                    fn_name,
                    data["function_doc"],
                    fn_return_type,
                    data["function_signature"],
                    data["function_type"],
                    str(file_path),
                ],
            )
        else:
            # Overwrite mutable columns only; preserve function_id (Principle 2)
            conn.execute(
                """
                UPDATE function_registry SET
                    function_doc = ?,
                    function_signature = ?,
                    function_type = ?,
                    module_path = ?,
                    is_active = TRUE
                WHERE function_id = ?
                """,
                [
                    data["function_doc"],
                    data["function_signature"],
                    data["function_type"],
                    str(file_path),
                    function_id,
                ],
            )
            # Remove old parameter rows so we can re-insert the current ones
            conn.execute("DELETE FROM parameter WHERE function_id = ?", [function_id])

        # Write parameter rows (one transaction with function_registry row — §10)
        for param_name, param_type in zip(param_names, param_types):
            param_id = new_id()
            param_chid = content_hash_id(
                "parameter", param_name, str(function_id), param_type
            )
            conn.execute(
                """
                INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                [param_id, param_chid, param_name, param_type, function_id],
            )

        conn.execute("COMMIT")
        return status

    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Scan entry point
# ---------------------------------------------------------------------------

def scan_functions(
    conn: duckdb.DuckDBPyConnection,
    functions_paths: list[str],
) -> list[dict]:
    """Scan all directories in functions_paths and register eligible functions.

    Returns a session-only scan log: list of
      {"file": str, "function_name": str, "status": str}
    where status is "added", "re-registered", "skipped: <reason>", or "file_missing".
    """
    log: list[dict] = []

    # Track which files we actually visit so we can detect missing files afterward.
    seen_files: set[str] = set()

    for dir_str in functions_paths:
        dir_path = Path(dir_str)
        if not dir_path.is_dir():
            log.append({
                "file": dir_str,
                "function_name": "<directory>",
                "status": f"skipped: path not found or not a directory",
            })
            continue

        for py_file in sorted(dir_path.glob("*.py")):
            seen_files.add(str(py_file))
            discovered = discover_functions_in_file(py_file)
            for item in discovered:
                fn_name = item["function_name"]
                if "skip_reason" in item:
                    log.append({
                        "file": str(py_file),
                        "function_name": fn_name,
                        "status": f"skipped: {item['skip_reason']}",
                    })
                    continue

                try:
                    status = _register_one_function(conn, py_file, fn_name, item["data"])
                    log.append({
                        "file": str(py_file),
                        "function_name": fn_name,
                        "status": status,
                    })
                except Exception as exc:
                    log.append({
                        "file": str(py_file),
                        "function_name": fn_name,
                        "status": f"skipped: registration error: {exc}",
                    })

    # After processing all discovered files, mark functions whose module_path
    # was in one of the scanned directories but no longer exists on disk.
    # The registry row is never deleted — only is_active is set to false.
    scanned_dirs = {str(Path(d).resolve()) for d in functions_paths if Path(d).is_dir()}
    if scanned_dirs:
        active_rows = conn.execute(
            "SELECT function_id, function_name, module_path "
            "FROM function_registry WHERE is_active = TRUE"
        ).fetchall()
        for fn_id, fn_name, module_path in active_rows:
            if module_path is None:
                continue
            mp = Path(module_path)
            # Only consider functions whose module_path lives in one of the
            # scanned directories — skip functions from directories we didn't scan.
            if str(mp.parent.resolve()) not in scanned_dirs:
                continue
            if str(mp) not in seen_files:
                conn.execute(
                    "UPDATE function_registry SET is_active = FALSE WHERE function_id = ?",
                    [fn_id],
                )
                log.append({
                    "file": module_path,
                    "function_name": fn_name,
                    "status": "file_missing",
                })

    return log


# ---------------------------------------------------------------------------
# Read helpers for the API
# ---------------------------------------------------------------------------

def get_function(conn: duckdb.DuckDBPyConnection, function_id: str) -> dict | None:
    """Return full detail for one function, or None if not found.

    Includes all function_registry fields, parameter list, and attached_sources
    (joined from source_function_map → source_registry).
    """
    row = conn.execute(
        """
        SELECT function_id, content_hash_id, function_class, function_name,
               function_doc, function_return_type, function_signature,
               function_type, module_path, is_active
        FROM function_registry
        WHERE function_id = ?
        """,
        [function_id],
    ).fetchone()

    if row is None:
        return None

    col_names = [
        "function_id", "content_hash_id", "function_class", "function_name",
        "function_doc", "function_return_type", "function_signature",
        "function_type", "module_path", "is_active",
    ]
    record = dict(zip(col_names, row))
    record["function_id"] = str(record["function_id"])
    record["content_hash_id"] = str(record["content_hash_id"])

    params = conn.execute(
        """
        SELECT param_id, param_name, param_type
        FROM parameter
        WHERE function_id = ?
        ORDER BY param_name
        """,
        [record["function_id"]],
    ).fetchall()
    record["parameters"] = [
        {"param_id": str(p[0]), "param_name": p[1], "param_type": p[2]}
        for p in params
    ]

    sources = conn.execute(
        """
        SELECT DISTINCT sr.source_id, sr.source_name
        FROM source_function_map sfm
        JOIN function_set_map fsm ON fsm.set_id = sfm.set_id
        JOIN source_registry sr ON sr.source_id = sfm.source_id
        WHERE fsm.function_id = ?
        ORDER BY sr.source_name
        """,
        [record["function_id"]],
    ).fetchall()
    record["attached_sources"] = [
        {"source_id": str(s[0]), "source_name": s[1]}
        for s in sources
    ]

    return record


def list_functions(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """Return all function_registry rows with their parameter rows, ordered by function_name."""
    rows = conn.execute(
        """
        SELECT function_id, content_hash_id, function_class, function_name,
               function_doc, function_return_type, function_signature,
               function_type, module_path, is_active
        FROM function_registry
        ORDER BY function_name
        """
    ).fetchall()

    col_names = [
        "function_id", "content_hash_id", "function_class", "function_name",
        "function_doc", "function_return_type", "function_signature",
        "function_type", "module_path", "is_active",
    ]

    results = []
    for row in rows:
        record = dict(zip(col_names, row))
        record["function_id"] = str(record["function_id"])
        record["content_hash_id"] = str(record["content_hash_id"])

        params = conn.execute(
            """
            SELECT param_id, param_name, param_type
            FROM parameter
            WHERE function_id = ?
            ORDER BY param_name
            """,
            [record["function_id"]],
        ).fetchall()
        record["parameters"] = [
            {"param_id": str(p[0]), "param_name": p[1], "param_type": p[2]}
            for p in params
        ]
        results.append(record)

    return results
