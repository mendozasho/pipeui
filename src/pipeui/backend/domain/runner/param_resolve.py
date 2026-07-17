"""Scalar parameter resolution (L3 — runner execution mechanics).

Resolves a function's non-column scalar params (``int``/``float``/``str``/``bool``)
to ``{param_name: value}`` so the executors can broadcast them into every argument
bundle (#258). Column-bound params and ``pd.Series``/``pd.DataFrame`` params are
handled by the executors themselves — this module owns *only* the scalar slice.

Split out of ``executors.py`` (#45): one responsibility, imported **down** by the
executors registry. No DuckDB, no worker, no step types — pure value coercion.
"""
from __future__ import annotations

# Canonical homes moved to data/functions/binding.py (#136); re-exported here so the
# executor catch-sites keep their identity until Phase 3 deletes this module.
from pipeui.backend.data.functions.binding import (  # noqa: F401
    RequiredParamError,
    coerce_scalar as _coerce_scalar,
)


def resolve_scalar_kwargs(params: list[dict]) -> dict:
    """Resolve non-column scalar params to ``{param_name: value}`` for broadcast into
    every argument bundle (#258).

    A scalar param is one whose type is int/float/str/bool and which has NO column
    bindings (a column-bound param is the bundle column, passed separately; pd.Series /
    pd.DataFrame are handled elsewhere). Its value is the persisted source_scalar_map
    value, else the captured Python default. A param with neither raises
    RequiredParamError — the function genuinely cannot run.
    """
    extra: dict = {}
    for p in params:
        if p.get("bindings"):
            continue  # column-bound — passed as the bundle column, not a scalar
        if p["param_type"] not in ("int", "float", "str", "bool"):
            continue  # pd.Series / pd.DataFrame
        raw = p.get("scalar_value")
        if raw is None and p.get("has_default"):
            raw = p.get("default_value")
        if raw is None:
            raise RequiredParamError(p["param_name"])
        extra[p["param_name"]] = _coerce_scalar(raw, p["param_type"])
    return extra
