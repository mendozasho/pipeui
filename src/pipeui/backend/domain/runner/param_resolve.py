"""Scalar parameter resolution (L3 — runner execution mechanics).

Resolves a function's non-column scalar params (``int``/``float``/``str``/``bool``)
to ``{param_name: value}`` so the executors can broadcast them into every argument
bundle (#258). Column-bound params and ``pd.Series``/``pd.DataFrame`` params are
handled by the executors themselves — this module owns *only* the scalar slice.

Split out of ``executors.py`` (#45): one responsibility, imported **down** by the
executors registry. No DuckDB, no worker, no step types — pure value coercion.
"""
from __future__ import annotations


class RequiredParamError(Exception):
    """A scalar param has no persisted value and no Python default — the function
    cannot run. Surfaced as a failed RunResult the frontend can pick up."""

    def __init__(self, param_name: str):
        self.param_name = param_name
        super().__init__(
            f"parameter '{param_name}' is required but no value or default was provided"
        )


def _coerce_scalar(value: str, param_type: str):
    """Coerce a source_scalar_map / default_value VARCHAR to the param's Python type."""
    if param_type == "int":
        return int(value)
    if param_type == "float":
        return float(value)
    if param_type == "bool":
        return str(value).strip().lower() in ("true", "1", "yes")
    return value  # str


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
