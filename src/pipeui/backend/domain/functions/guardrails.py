"""Static screen over uploaded function modules (functions domain) — AST only, no exec.

``screen_module`` parses a user module's source and reports findings BEFORE the
scanner exec's it for signature extraction (``discovery._load_module``). Any
``block``-severity finding rejects the module — its top-level code never runs in the
app process. ``flag`` findings accept the module but surface in the scan log.

Scope (Principle 5): this is an **accident screen for a single trusted local user**,
not a sandbox — it blocks the obvious footguns (shelling out, eval, filesystem and
network access, dunder escape hatches) with explainable line-numbered reasons. It is
not a defense against a determined adversary; that remains the process-isolation
boundary at execution time (§10). AST-only signature extraction (never exec'ing user
code in-process at all) is recorded as deferred hardening.

Pure module: ``ast`` + ``dataclasses`` only. No DB, no filesystem, no app object.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

Severity = Literal["block", "flag"]

# Imports that are blocked outright — process/OS/network/serialization escape
# hatches a data-transform function has no business touching.
_BLOCKED_IMPORTS: frozenset[str] = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "ctypes", "importlib",
    "multiprocessing", "signal", "pickle", "marshal", "builtins", "pty",
    "http", "urllib", "requests",
})

# Calls to these bare names are blocked anywhere in the module (they are unsafe at
# scan time at module level, and unsafe at run time inside a function body).
_BLOCKED_CALLS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__", "open", "globals", "locals",
})

# getattr/setattr/delattr with a string-literal dunder name — same escape hatch as
# direct dunder attribute access, spelled dynamically.
_ATTR_FUNCS: frozenset[str] = frozenset({"getattr", "setattr", "delattr"})

# Imports that are expected in data functions; anything else is flagged (not
# blocked — the per-user venv is a designed feature, §10).
_ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "pandas", "numpy", "math", "statistics", "datetime", "re", "json",
    "decimal", "collections", "itertools", "functools", "typing", "dataclasses",
    "__future__",
})

# Module-level statement types that do NOT run arbitrary code at scan time.
_BENIGN_TOPLEVEL = (
    ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
    ast.ClassDef, ast.Assign, ast.AnnAssign,
)


@dataclass(frozen=True)
class ScreenFinding:
    """One screen result: what was found, where, and whether it rejects the module."""

    severity: Severity
    rule: str        # short kebab-case id, e.g. "import-os", "call-eval"
    lineno: int
    detail: str      # human sentence for the scan log, e.g. "imports 'subprocess'"


def _root_module(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) > 4


def screen_module(source: str, *, filename: str = "<user module>") -> list[ScreenFinding]:
    """Screen a module's source. Returns findings; empty list = clean.

    Callers reject the module (and must not exec it) when any finding has
    ``severity == "block"``.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [ScreenFinding(
            severity="block",
            rule="syntax-error",
            lineno=exc.lineno or 0,
            detail=f"syntax error: {exc.msg}",
        )]

    findings: list[ScreenFinding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _root_module(alias.name)
                findings.extend(_screen_import(root, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) have module=None; nothing to screen by name.
            if node.module is not None and node.level == 0:
                findings.extend(_screen_import(_root_module(node.module), node.lineno))
        elif isinstance(node, ast.Call):
            findings.extend(_screen_call(node))
        elif isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            findings.append(ScreenFinding(
                severity="block",
                rule="dunder-access",
                lineno=node.lineno,
                detail=f"dunder attribute access '{node.attr}'",
            ))

    # Module-level statements that execute arbitrary code at scan time (the module
    # is exec'd to read signatures). A bare docstring Expr is fine.
    for stmt in tree.body:
        if isinstance(stmt, _BENIGN_TOPLEVEL):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # module docstring / stray literal
        findings.append(ScreenFinding(
            severity="flag",
            rule="toplevel-statement",
            lineno=stmt.lineno,
            detail=f"module-level {type(stmt).__name__.lower()} statement runs at scan time",
        ))

    return findings


def _screen_import(root: str, lineno: int) -> list[ScreenFinding]:
    if root in _BLOCKED_IMPORTS:
        return [ScreenFinding(
            severity="block",
            rule=f"import-{root}",
            lineno=lineno,
            detail=f"imports '{root}'",
        )]
    if root not in _ALLOWED_IMPORTS:
        return [ScreenFinding(
            severity="flag",
            rule=f"import-unlisted-{root}",
            lineno=lineno,
            detail=f"imports '{root}' (outside the expected data-function set)",
        )]
    return []


def _screen_call(node: ast.Call) -> list[ScreenFinding]:
    if not isinstance(node.func, ast.Name):
        return []
    name = node.func.id
    if name in _BLOCKED_CALLS:
        return [ScreenFinding(
            severity="block",
            rule=f"call-{name}",
            lineno=node.lineno,
            detail=f"call to {name}()",
        )]
    if name in _ATTR_FUNCS and node.args:
        first = node.args[1] if len(node.args) > 1 else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and _is_dunder(first.value):
            return [ScreenFinding(
                severity="block",
                rule=f"call-{name}-dunder",
                lineno=node.lineno,
                detail=f"{name}() with dunder name '{first.value}'",
            )]
    return []
