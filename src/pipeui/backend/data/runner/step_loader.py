"""Step loading (L1) — read the map tables into a source's ordered step list.

``fetch_steps`` reads ``source_function_map`` / ``function_set_map`` / ``parameter``
(and the per-function output config) into the function-step list; ``get_builtin_steps``
reads ``source_builtin_map`` into the built-in-step list (CONTEXT.md → Runner module
responsibilities → ``step_loader.py`` (L1)). Pure read — no dispatch, no execution.

This module is L1: it depends only downward (DB, ids). Both ``run.py`` (the
orchestrator) and ``resolve.py`` (the cycle-guard frontier) import from here, so the
``run ⇄ builtins`` and ``resolve → builtins`` step-loading edges are one-directional.
"""
from __future__ import annotations

import json
import uuid

import duckdb

from pipeui.backend.data.functions.binding import ParamBinding, StepBinding
from pipeui.backend.data.functions.contract import FunctionContract, ParamContract
from pipeui.backend.data.runner.steps import (
    BuiltinStepContext,
    FunctionStepContext,
    StepContext,
)


def fetch_steps(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> list[FunctionStepContext]:
    """Return pipeline steps for a source, ordered by position.

    The loader is the producer of the ``StepContext`` carrier: each fetched
    ``source_function_map`` row is built into a typed ``FunctionStepContext`` (with
    ``FunctionSpec`` members) via ``StepContext.from_function`` — never returned as a
    raw dict. Each member carries: function_id, function_name, function_type,
    function_class, function_return_type, module_path, params (typed Mapping rows),
    output_mode, append_name, output_targets.
    """
    set_rows = conn.execute(
        """
        SELECT
            sfm.source_function_map_id,
            fs.set_id,
            fs.set_name,
            sfm.position,
            sfm.output_mode,
            sfm.append_name
        FROM source_function_map sfm
        JOIN function_set fs ON fs.set_id = sfm.set_id
        WHERE sfm.source_id = ?
        ORDER BY sfm.position ASC
        """,
        [source_id],
    ).fetchall()

    steps = []
    for sfm_id, set_id, set_name, position, output_mode, append_name in set_rows:
        fn_rows = conn.execute(
            """
            SELECT
                fr.function_id,
                fr.function_name,
                fr.function_type,
                fr.function_class,
                fr.function_return_type,
                fr.module_path,
                fr.function_signature,
                fr.function_doc,
                fr.engine,
                fr.function_body
            FROM function_set_map fsm
            JOIN function_registry fr ON fr.function_id = fsm.function_id
            WHERE fsm.set_id = ?
            ORDER BY fsm.position
            """,
            [set_id],
        ).fetchall()

        functions = []
        for (fn_id, fn_name, fn_type, fn_class, fn_ret, module_path,
             fn_signature, fn_doc, fn_engine, fn_body) in fn_rows:
            param_rows = conn.execute(
                """
                SELECT p.param_id, p.param_name, p.param_type,
                       p.has_default, p.default_value, p.position,
                       cr.column_name, ssm.value AS scalar_value
                FROM parameter p
                LEFT JOIN alias_map am ON am.parameter_id = p.param_id
                    AND am.source_id = ?
                LEFT JOIN column_registry cr ON cr.column_id = am.column_id
                LEFT JOIN source_scalar_map ssm ON ssm.param_id = p.param_id
                    AND ssm.source_id = ?
                WHERE p.function_id = ?
                ORDER BY p.position, p.param_name, am.position
                """,
                [source_id, source_id, fn_id],
            ).fetchall()

            # Collapse multiple alias_map rows per param into a list of column names.
            # #258: also carry the persisted scalar value + Python default so the
            # executor can resolve and broadcast scalar params into every bundle.
            params_map: dict[str, dict] = {}
            for (p_id, p_name, p_type, p_has_default, p_default, p_position,
                 col_name, scalar_value) in param_rows:
                key = str(p_id)
                if key not in params_map:
                    params_map[key] = {
                        "param_id": key,
                        "param_name": p_name,
                        "param_type": p_type,
                        "bindings": [],
                        "has_default": bool(p_has_default),
                        "default_value": p_default,
                        "position": p_position,
                        "scalar_value": scalar_value,
                    }
                if col_name is not None:
                    params_map[key]["bindings"].append(col_name)

            # #136 shadow hydration: the universal contract (params in signature
            # order) + this source's persisted binding (params in loader order —
            # alphabetical until Phase 3 flips ordering to position).
            contract = FunctionContract(
                name=fn_name,
                engine=fn_engine or "python",
                params=tuple(
                    ParamContract(
                        name=p["param_name"],
                        type_str=p["param_type"],
                        position=p["position"] if p["position"] is not None else i,
                        has_default=p["has_default"],
                        default_value=p["default_value"],
                    )
                    for i, p in enumerate(sorted(
                        params_map.values(), key=lambda p: (p["position"] or 0),
                    ))
                ),
                return_type=fn_ret,
                signature=fn_signature,
                doc=fn_doc,
                source_path=module_path,
                body=fn_body,
            )
            binding = StepBinding(params=tuple(
                ParamBinding(
                    param_name=p["param_name"],
                    kind=(
                        "table" if p["param_type"] == "pd.DataFrame"
                        else "columns" if p["bindings"]
                        else "literal"
                    ),
                    columns=tuple(p["bindings"]),
                    value=p["scalar_value"],
                )
                for p in params_map.values()
            ))

            # Per-function output config (#264): output_mode / append_name / output_targets
            # belong to each function, not the whole set. Fall back to the step-level
            # source_function_map values for legacy rows with no function_output_config.
            cfg_row = conn.execute(
                "SELECT output_mode, append_name FROM function_output_config "
                "WHERE source_function_map_id = ? AND function_id = ?",
                [sfm_id, fn_id],
            ).fetchone()
            fn_output_mode = cfg_row[0] if cfg_row else output_mode
            fn_append_name = cfg_row[1] if cfg_row else append_name
            fn_target_rows = conn.execute(
                """
                SELECT cr.column_name
                FROM output_target_map otm
                JOIN column_registry cr ON cr.column_id = otm.column_id
                WHERE otm.source_function_map_id = ? AND otm.function_id = ?
                ORDER BY otm.position
                """,
                [sfm_id, fn_id],
            ).fetchall()

            functions.append({
                "function_id": str(fn_id),
                "function_name": fn_name,
                "function_type": fn_type,
                "function_class": fn_class,
                "function_return_type": fn_ret,
                "module_path": module_path,
                "params": list(params_map.values()),
                "output_mode": fn_output_mode,
                "append_name": fn_append_name,
                "output_targets": [r[0] for r in fn_target_rows],
                "contract": contract,
                "binding": binding,
            })

        # Output-target columns for a `replace` transform step, in position order
        # (bundle i -> target i). Empty for append steps and replace-with-default.
        target_rows = conn.execute(
            """
            SELECT cr.column_name
            FROM output_target_map otm
            JOIN column_registry cr ON cr.column_id = otm.column_id
            WHERE otm.source_function_map_id = ?
            ORDER BY otm.position
            """,
            [sfm_id],
        ).fetchall()
        output_targets = [r[0] for r in target_rows]

        # from_set tags SET so the runner routes the step to the function-set adapter
        # (slice 4), which flattens it into per-member FUNCTION dispatch. The loader is
        # the sole producer of the carrier — it calls the matching factory per table.
        steps.append(StepContext.from_set({
            "source_function_map_id": str(sfm_id),
            "set_id": str(set_id),
            "set_name": set_name,
            "position": position,
            "output_mode": output_mode,
            "append_name": append_name,
            "output_targets": output_targets,
            "functions": functions,
        }))

    return steps


def get_builtin_steps(
    conn: duckdb.DuckDBPyConnection,
    source_id: uuid.UUID,
) -> list[BuiltinStepContext]:
    """Return all source_builtin_map rows for a source as typed ``BuiltinStepContext``,
    ordered by position. The loader is the producer of the carrier — each row is built
    via ``StepContext.from_builtin`` (never a raw dict). ``builtin_config`` is decoded
    from its stored JSON to the typed ``Mapping`` depth boundary."""
    rows = conn.execute(
        "SELECT step_id, builtin_type, builtin_config, position FROM source_builtin_map WHERE source_id = ? ORDER BY position ASC",
        [source_id],
    ).fetchall()
    result = []
    for step_id, btype, bcfg, pos in rows:
        result.append(StepContext.from_builtin({
            "step_id": str(step_id),
            "step_type": "builtin",
            "builtin_type": btype,
            "builtin_config": json.loads(bcfg) if isinstance(bcfg, str) else bcfg,
            "position": pos,
        }))
    return result
