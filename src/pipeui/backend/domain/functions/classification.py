"""Function classification (functions domain) — pure derivation, ZERO DB dependency.

Derives a function's ``function_class`` / ``function_return_type`` / ``function_type``
(§11, Principle 4: derived-not-stored) from its parameter/return annotations, plus the
annotation-string canonicalization and known-type gates the discovery layer uses.

Split out of ``registration.py`` (#47): this is the leaf of the functions registration
chain — it touches no DuckDB connection, no filesystem, no app object. ``discovery``
imports it; it imports nothing from the rest of the domain. Keep it DB-free.
"""
from __future__ import annotations

import inspect
from typing import Any


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
