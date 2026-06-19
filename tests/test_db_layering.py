"""Layer-boundary guards for the data/base/db module (#49).

db.py is the bottom data module: connection + registry schema lifecycle only. These
structural guards lock the #49 acceptance — no backend→app up-import, no FastAPI, no
stdout, and the type-inference / request-provider concerns relocated out.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pipeui.backend.data.base.db as db_module


def _imported_modules(tree: ast.AST) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    return mods


def test_db_does_not_import_app_or_fastapi():
    """No backend→app up-import (the `DB_PATH` leak #49 fixes) and no FastAPI in the
    bottom data module."""
    tree = ast.parse(Path(db_module.__file__).read_text(encoding="utf-8"))
    offenders = [
        m for m in _imported_modules(tree)
        if m.startswith("pipeui.app") or m == "fastapi" or m.startswith("fastapi.")
    ]
    assert not offenders, f"db.py must not import app/fastapi, found: {offenders}"


def test_db_emits_no_stdout():
    """The data layer must not print — failures surface via return/raise, not stdout."""
    tree = ast.parse(Path(db_module.__file__).read_text(encoding="utf-8"))
    prints = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
    ]
    assert not prints, "db.py must not call print()"


def test_db_has_no_type_inference_or_request_provider():
    """Type-inference moved to data/sources/inference.py and the FastAPI `get_conn`
    provider to middleware/deps.py — neither remains defined in db.py."""
    tree = ast.parse(Path(db_module.__file__).read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for relocated in ("infer_column_types", "map_pandas_dtype", "get_conn"):
        assert relocated not in defined, f"{relocated} should have moved out of db.py"
