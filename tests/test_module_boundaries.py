"""Module-boundary guards.

Enforces the encapsulation rule documented in ARCHITECTURE.md:

    "A single leading underscore means *module-local*. The moment a sibling module
     needs a name, that name is the module's public contract and must be public."

A `from x import _name` (single leading underscore, not a dunder) is a cross-module
private reach-in: module B reaching into module A's internals instead of a named
public interface. This is the standing check the hostile-auditor runs; this test
makes it fail in CI rather than only at review time (the gap that let two such
imports into executors.py before #52).
"""
import ast
from pathlib import Path

import pytest

import pipeui

_SRC_ROOT = Path(pipeui.__file__).parent


def _python_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _cross_module_private_imports(path: Path) -> list[str]:
    """Return 'module.imported_name' for each `from m import _name` in `path`.

    Uses the imported name (not its `as` alias), so `from m import public as _local`
    — aliasing a *public* name — is correctly ignored. Dunders (`__future__`,
    `__all__`, etc.) are excluded.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            name = alias.name
            if name.startswith("_") and not name.startswith("__"):
                hits.append(f"{node.module or '.'}.{name}")
    return hits


@pytest.mark.unit
def test_no_cross_module_private_imports():
    """No production module imports another module's single-underscore private name."""
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        hits = _cross_module_private_imports(path)
        if hits:
            offenders[str(path.relative_to(_SRC_ROOT))] = hits
    assert not offenders, (
        "Cross-module private reach-in(s) found — promote the name to a public "
        "contract on its owner module (ARCHITECTURE.md):\n"
        + "\n".join(f"  {f}: {', '.join(names)}" for f, names in offenders.items())
    )
