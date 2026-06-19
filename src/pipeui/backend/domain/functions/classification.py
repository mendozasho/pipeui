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
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TypeDescriptor:
    """One supported param/return type and everything classification derives from it.

    Single source of truth (OCP, #51): adding a supported type is ONE entry in
    ``_TYPE_DESCRIPTORS`` — no edits scattered across separate maps.

    Fields:
    - ``type_str`` — canonical param/return annotation string (e.g. ``"pd.Series"``).
    - ``granularity`` — §11 ordering; ``function_class`` is the class of the *least*
      granular (highest-index) parameter type. ``str`` is granularity 1 (scalar at
      scan time; column_backed resolved at attach).
    - ``function_class`` — the class this type contributes.
    - ``return_type`` — the ``function_return_type`` vocabulary value (CONTEXT.md).
    - ``is_validation_return`` — True when a function returning this type is a
      ``validation`` (else ``transform``).
    """
    type_str: str
    granularity: int
    function_class: str
    return_type: str
    is_validation_return: bool


# Ordered low → high granularity. The one table all classification reads from.
# A new supported type is a single row here (OCP, #51).
_TYPE_DESCRIPTORS: tuple[TypeDescriptor, ...] = (
    TypeDescriptor("int",             0, "scalar",       "scalar",          False),
    TypeDescriptor("float",           0, "scalar",       "scalar",          False),
    TypeDescriptor("bool",            0, "scalar",       "boolean",         True),
    TypeDescriptor("str",             1, "scalar",       "scalar",          False),
    TypeDescriptor("pd.Series[bool]", 2, "pd.Series",    "pd.Series[bool]", True),
    TypeDescriptor("pd.Series",       2, "pd.Series",    "pd.Series",       False),
    TypeDescriptor("pd.DataFrame",    3, "pd.dataframe", "pd.DataFrame",    False),
)

def _derive_lookups(
    descriptors: tuple[TypeDescriptor, ...],
) -> tuple[dict[str, TypeDescriptor], frozenset[str]]:
    """Build the two lookups from the descriptor table — the ONLY place they are
    derived. Reused at module load and by tests so a row added to ``_TYPE_DESCRIPTORS``
    provably propagates to every consumer (OCP, #51) through this one function."""
    by_type = {d.type_str: d for d in descriptors}
    validation_return_types = frozenset(
        d.return_type for d in descriptors if d.is_validation_return
    )
    return by_type, validation_return_types


# Lookups derived from the single table — never maintained independently.
_BY_TYPE, _VALIDATION_RETURN_TYPES = _derive_lookups(_TYPE_DESCRIPTORS)


def annotation_to_str(annotation: Any) -> str | None:
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


def is_known_param_type(type_str: str) -> bool:
    return type_str in _BY_TYPE


def is_known_return_type(type_str: str) -> bool:
    return type_str in _BY_TYPE


def derive_function_class(param_types: list[str]) -> str:
    """Derive function_class from the list of param_type strings (§11).

    The least-granular (highest granularity-index) param drives the class. Raises
    ``KeyError`` on an unknown param type (callers gate with ``is_known_param_type``).
    """
    driver = max((_BY_TYPE[pt] for pt in param_types), key=lambda d: d.granularity)
    return driver.function_class


def derive_function_return_type(return_annotation_str: str) -> str | None:
    """Map a return annotation string to function_return_type vocabulary (CONTEXT.md)."""
    descriptor = _BY_TYPE.get(return_annotation_str)
    return descriptor.return_type if descriptor else None


def derive_function_type(function_return_type: str) -> str:
    """Derive function_type from function_return_type (§11 / CONTEXT.md)."""
    return "validation" if function_return_type in _VALIDATION_RETURN_TYPES else "transform"
