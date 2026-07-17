"""SQL engine tests — render_sql / execute_sql_contract / `-- param:` headers (#140).

Guarantees covered (CLAUDE.md rule 10):
  - Column params render as quoted identifiers validated against a closed
    vocabulary — an unknown or hostile name is REJECTED, never quoted-and-hoped.
  - Scalar params (int/float/bool/str/date) ALWAYS bind as `?` DuckDB parameters,
    appended in template order, once per occurrence; hostile values are inert.
  - Table / source_ref params substitute executor-registered view names only.
  - Unknown placeholders and unresolved params are rejected with named errors.
  - `-- param:` headers parse name/type/default in order; unsupported types and
    defaults on non-scalar params skip the function with a reason.
  - End-to-end: a param-declared SQL transform/validation attached with real
    bindings + scalar values runs through run_pipeline against the working frame;
    the legacy implicit `{source_table}` shape keeps its instance-table behavior
    (covered in test_sql_functions.py).
"""
from __future__ import annotations

import csv
import datetime
import textwrap

import pandas as pd
import pytest

from pipeui.backend.data.functions.binding import BoundCall, ParamBinding, StepBinding
from pipeui.backend.data.functions.contract import ENGINE_SQL, FunctionContract, ParamContract
from pipeui.backend.domain.functions.attach import AttachBinding, attach_function
from pipeui.backend.domain.functions.discovery import extract_contracts
from pipeui.backend.domain.functions.registration import scan_functions
from pipeui.backend.domain.runner.run import get_staging_rows, run_pipeline
from pipeui.backend.domain.runner.sql_engine import execute_sql_contract, render_sql
from pipeui.backend.domain.sources.create import create_source
from pipeui.backend.domain.sources.ingestion import ingest_source


def _ids(staged: dict) -> list:
    """Extract the id column from get_staging_rows' {"columns", "rows"} shape."""
    if "id" not in staged["columns"]:
        return []
    return [r["id"] for r in staged["rows"]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract(*params: ParamContract, body: str = "") -> FunctionContract:
    return FunctionContract(
        name="q", engine=ENGINE_SQL, params=params,
        return_type="pd.DataFrame", signature="(…)", body=body,
    )


def _p(name: str, type_str: str, position: int = 0) -> ParamContract:
    return ParamContract(name=name, type_str=type_str, position=position)


def _call(**kw) -> BoundCall:
    kw.setdefault("bundle_key", "")
    kw.setdefault("mode", "table")
    return BoundCall(**kw)


def write_sql(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src))
    return p


def make_csv(tmp_path, name, columns, rows):
    p = tmp_path / name
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return str(p)


# ---------------------------------------------------------------------------
# render_sql — unit
# ---------------------------------------------------------------------------

class TestRenderSql:
    @pytest.mark.unit
    def test_column_param_renders_validated_quoted_identifier(self):
        c = _contract(_p("col", "pd.Series"))
        sql, params = render_sql(
            c, _call(mode="column", column_kwargs={"col": "amount"}),
            body="SELECT {col} FROM t", views={}, columns_available={"amount", "region"},
        )
        assert sql == 'SELECT "amount" FROM t'
        assert params == []

    @pytest.mark.unit
    def test_unknown_bound_column_rejected_not_quoted(self):
        c = _contract(_p("col", "pd.Series"))
        with pytest.raises(ValueError, match="not a column of the input"):
            render_sql(
                c, _call(mode="column", column_kwargs={"col": "nope"}),
                body="SELECT {col} FROM t", views={}, columns_available={"amount"},
            )

    @pytest.mark.unit
    def test_hostile_column_name_rejected(self):
        # An injection-shaped binding is outside the closed vocabulary → rejected,
        # never interpolated or quote-and-hoped.
        c = _contract(_p("col", "pd.Series"))
        hostile = 'x"; DROP TABLE users; --'
        with pytest.raises(ValueError, match="not a column of the input"):
            render_sql(
                c, _call(mode="column", column_kwargs={"col": hostile}),
                body="SELECT {col} FROM t", views={}, columns_available={"amount"},
            )

    @pytest.mark.unit
    def test_legit_column_with_embedded_quote_is_doubled(self):
        c = _contract(_p("col", "pd.Series"))
        weird = 'am"ount'
        sql, _ = render_sql(
            c, _call(mode="column", column_kwargs={"col": weird}),
            body="SELECT {col} FROM t", views={}, columns_available={weird},
        )
        assert sql == 'SELECT "am""ount" FROM t'

    @pytest.mark.unit
    def test_scalar_params_bind_in_template_order_per_occurrence(self):
        c = _contract(
            _p("lo", "int", 0), _p("hi", "float", 1), _p("on", "bool", 2),
            _p("tag", "str", 3), _p("cutoff", "date", 4),
        )
        call = _call(mode="value", literal_kwargs={
            "lo": 1, "hi": 9.5, "on": True, "tag": "a", "cutoff": datetime.date(2026, 1, 2),
        })
        body = "SELECT ok FROM t WHERE a > {hi} AND b > {lo} AND c = {tag} AND d = {on} AND e >= {cutoff} AND f > {lo}"
        sql, params = render_sql(c, call, body=body, views={}, columns_available=set())
        assert sql == "SELECT ok FROM t WHERE a > ? AND b > ? AND c = ? AND d = ? AND e >= ? AND f > ?"
        # template (left→right) order, lo appearing twice → bound twice
        assert params == [9.5, 1, "a", True, datetime.date(2026, 1, 2), 1]

    @pytest.mark.unit
    def test_hostile_scalar_value_stays_a_bound_parameter(self):
        c = _contract(_p("v", "str"))
        call = _call(mode="value", literal_kwargs={"v": "'; DROP TABLE users; --"})
        sql, params = render_sql(
            c, call, body="SELECT * FROM t WHERE name = {v}", views={}, columns_available=set(),
        )
        assert sql == "SELECT * FROM t WHERE name = ?"
        assert params == ["'; DROP TABLE users; --"]  # inert value, never interpolated

    @pytest.mark.unit
    def test_unknown_placeholder_rejected(self):
        c = _contract(_p("col", "pd.Series"))
        with pytest.raises(ValueError, match="unknown placeholder '{oops}'"):
            render_sql(
                c, _call(mode="column", column_kwargs={"col": "a"}),
                body="SELECT {oops} FROM t", views={}, columns_available={"a"},
            )

    @pytest.mark.unit
    def test_table_param_substitutes_registered_view_only(self):
        c = _contract(_p("t", "pd.DataFrame"))
        sql, _ = render_sql(
            c, _call(mode="table", table_params=("t",)),
            body="SELECT * FROM {t}", views={"t": "__pipeui_sql_t_abc"}, columns_available=set(),
        )
        assert sql == 'SELECT * FROM "__pipeui_sql_t_abc"'
        with pytest.raises(ValueError, match="no registered view"):
            render_sql(
                c, _call(mode="table", table_params=("t",)),
                body="SELECT * FROM {t}", views={}, columns_available=set(),
            )

    @pytest.mark.unit
    def test_column_backed_scalar_renders_as_identifier(self):
        # value_or_column in SQL: bound to a column, the param IS the identifier.
        c = _contract(_p("v", "str"))
        sql, params = render_sql(
            c, _call(mode="row", column_kwargs={"v": "region"}),
            body="SELECT {v} FROM t", views={}, columns_available={"region"},
        )
        assert sql == 'SELECT "region" FROM t'
        assert params == []

    @pytest.mark.unit
    def test_source_table_available_to_param_templates(self):
        c = _contract(_p("col", "pd.Series"))
        sql, _ = render_sql(
            c, _call(mode="column", column_kwargs={"col": "a"}),
            body="SELECT {col} FROM {source_table}",
            views={"source_table": "__pipeui_sql_source_table_x"},
            columns_available={"a"},
        )
        assert sql == 'SELECT "a" FROM "__pipeui_sql_source_table_x"'


# ---------------------------------------------------------------------------
# `-- param:` header parsing
# ---------------------------------------------------------------------------

class TestParamHeaders:
    @pytest.mark.unit
    def test_params_parse_in_order_with_defaults(self, tmp_path):
        f = write_sql(tmp_path, "t.sql", """
            -- name: threshold_filter
            -- type: transform
            -- param: amount column
            -- param: min_val float = 15
            -- param: as_of date = 2026-01-01
            SELECT * FROM {source_table} WHERE {amount} > {min_val}
        """)
        contract = extract_contracts(f)[0].contract
        assert contract is not None
        assert [(p.name, p.type_str, p.position) for p in contract.params] == [
            ("amount", "pd.Series", 0), ("min_val", "float", 1), ("as_of", "date", 2),
        ]
        assert contract.params[1].has_default and contract.params[1].default_value == "15"
        assert contract.params[2].default_value == "2026-01-01"
        assert contract.function_class == "pd.Series"  # derived from the params
        assert contract.signature == (
            "(amount: pd.Series, min_val: float = 15, as_of: date = 2026-01-01) -> pd.DataFrame"
        )

    @pytest.mark.unit
    def test_unsupported_param_type_skips_with_reason(self, tmp_path):
        f = write_sql(tmp_path, "t.sql", """
            -- name: bad
            -- param: x blob
            SELECT 1
        """)
        item = extract_contracts(f)[0]
        assert item.contract is None
        assert "unsupported `-- param:` type `blob`" in item.skip_reason

    @pytest.mark.unit
    def test_default_on_column_param_skips_with_reason(self, tmp_path):
        f = write_sql(tmp_path, "t.sql", """
            -- name: bad
            -- param: c column = amount
            SELECT 1
        """)
        item = extract_contracts(f)[0]
        assert item.contract is None
        assert "only scalar params may declare a default" in item.skip_reason

    @pytest.mark.integration
    def test_scan_writes_parameter_rows_for_sql_params(self, db, tmp_path):
        write_sql(tmp_path, "t.sql", """
            -- name: threshold_filter
            -- type: transform
            -- param: amount column
            -- param: min_val float = 15
            SELECT * FROM {source_table} WHERE {amount} > {min_val}
        """)
        log = scan_functions(db, [str(tmp_path)])
        assert any(e["status"] == "added" for e in log), log
        rows = db.execute(
            "SELECT p.param_name, p.param_type, p.position, p.has_default, p.default_value "
            "FROM parameter p JOIN function_registry fr ON fr.function_id = p.function_id "
            "WHERE fr.function_name = 'threshold_filter' ORDER BY p.position"
        ).fetchall()
        assert rows == [
            ("amount", "pd.Series", 0, False, None),
            ("min_val", "float", 1, True, "15"),
        ]
        engine, body = db.execute(
            "SELECT engine, function_body FROM function_registry "
            "WHERE function_name = 'threshold_filter'"
        ).fetchone()
        assert engine == "sql"
        assert "{amount}" in body


# ---------------------------------------------------------------------------
# End-to-end — param-declared SQL through the real attach + run pipeline
# ---------------------------------------------------------------------------

def _ingested_source(db, tmp_path):
    path = make_csv(tmp_path, "sales.csv", ["id", "amount", "region"], [
        ["r1", 10, "east"], ["r2", 20, "west"], ["r3", 30, "east"],
    ])
    source_id, failed = create_source(db, path, "sales", "id", "upsert")
    assert not failed.has_failures()
    ingest_source(db, source_id, path)
    return source_id


def _fn_and_param_ids(db, fn_name):
    fn_id = db.execute(
        "SELECT function_id FROM function_registry WHERE function_name = ?", [fn_name]
    ).fetchone()[0]
    params = dict(db.execute(
        "SELECT param_name, param_id FROM parameter WHERE function_id = ?", [fn_id]
    ).fetchall())
    return fn_id, params


def _col_id(db, source_id, col_name):
    return db.execute(
        "SELECT cr.column_id FROM column_registry cr "
        "JOIN source_column_map scm ON scm.column_id = cr.column_id "
        "WHERE scm.source_id = ? AND cr.column_name = ?",
        [source_id, col_name],
    ).fetchone()[0]


class TestSqlContractEndToEnd:
    @pytest.mark.integration
    def test_param_sql_transform_filters_working_frame(self, db, tmp_path):
        source_id = _ingested_source(db, tmp_path)
        fn_dir = tmp_path / "fns"
        fn_dir.mkdir()
        write_sql(fn_dir, "thresh.sql", """
            -- name: threshold_filter
            -- type: transform
            -- param: amount column
            -- param: min_val float = 15
            SELECT * FROM {source_table} WHERE {amount} > {min_val}
        """)
        scan_functions(db, [str(fn_dir)])
        fn_id, param_ids = _fn_and_param_ids(db, "threshold_filter")
        attach_function(
            db, source_id,
            [AttachBinding(param_id=param_ids["amount"], column_ids=[_col_id(db, source_id, "amount")])],
            function_id=fn_id,
        )

        out = run_pipeline(db, source_id, "transforms")
        steps = out["steps"]
        assert steps and steps[0]["status"] == "ok", steps
        # default min_val=15 → rows r2 (20) and r3 (30) survive
        staged = get_staging_rows(db, source_id)
        assert sorted(_ids(staged)) == ["r2", "r3"]

    @pytest.mark.integration
    def test_param_sql_transform_scalar_override_beats_default(self, db, tmp_path):
        source_id = _ingested_source(db, tmp_path)
        fn_dir = tmp_path / "fns"
        fn_dir.mkdir()
        write_sql(fn_dir, "thresh.sql", """
            -- name: threshold_filter
            -- type: transform
            -- param: amount column
            -- param: min_val float = 15
            SELECT * FROM {source_table} WHERE {amount} > {min_val}
        """)
        scan_functions(db, [str(fn_dir)])
        fn_id, param_ids = _fn_and_param_ids(db, "threshold_filter")
        attach_function(
            db, source_id,
            [AttachBinding(param_id=param_ids["amount"], column_ids=[_col_id(db, source_id, "amount")])],
            function_id=fn_id,
            scalar_values={param_ids["min_val"]: "25"},
        )
        run_pipeline(db, source_id, "transforms")
        staged = get_staging_rows(db, source_id)
        assert sorted(_ids(staged)) == ["r3"]  # only 30 > 25

    @pytest.mark.integration
    def test_param_sql_validation_counts_pass_fail(self, db, tmp_path):
        source_id = _ingested_source(db, tmp_path)
        fn_dir = tmp_path / "fns"
        fn_dir.mkdir()
        write_sql(fn_dir, "check.sql", """
            -- name: amount_above
            -- type: validation
            -- param: amount column
            -- param: min_val float = 15
            SELECT {amount} > {min_val} FROM {source_table}
        """)
        scan_functions(db, [str(fn_dir)])
        fn_id, param_ids = _fn_and_param_ids(db, "amount_above")
        attach_function(
            db, source_id,
            [AttachBinding(param_id=param_ids["amount"], column_ids=[_col_id(db, source_id, "amount")])],
            function_id=fn_id,
        )
        steps = run_pipeline(db, source_id, "validations")["steps"]
        assert steps and steps[0]["status"] == "ok", steps
        assert steps[0]["rows_passed"] == 2   # 20, 30 > 15
        assert steps[0]["rows_failed"] == 1   # 10

    @pytest.mark.integration
    def test_hostile_scalar_value_cannot_drop_tables(self, db, tmp_path):
        source_id = _ingested_source(db, tmp_path)
        fn_dir = tmp_path / "fns"
        fn_dir.mkdir()
        write_sql(fn_dir, "eq.sql", """
            -- name: region_eq
            -- type: transform
            -- param: region column
            -- param: wanted str = east
            SELECT * FROM {source_table} WHERE {region} = {wanted}
        """)
        scan_functions(db, [str(fn_dir)])
        fn_id, param_ids = _fn_and_param_ids(db, "region_eq")
        attach_function(
            db, source_id,
            [AttachBinding(param_id=param_ids["region"], column_ids=[_col_id(db, source_id, "region")])],
            function_id=fn_id,
            scalar_values={param_ids["wanted"]: "east'; DROP TABLE function_registry; --"},
        )
        steps = run_pipeline(db, source_id, "transforms")["steps"]
        # the hostile string is a bound value: matches nothing, drops nothing
        assert steps and steps[0]["status"] == "ok", steps
        staged = get_staging_rows(db, source_id)
        assert _ids(staged) == []
        assert db.execute("SELECT COUNT(*) FROM function_registry").fetchone()[0] == 1

    @pytest.mark.integration
    def test_execute_sql_contract_source_ref_view(self, db, tmp_path):
        # A hand-built source_ref call: the referenced source's RAW frame is
        # registered as the param's view.
        source_id = _ingested_source(db, tmp_path)
        contract = FunctionContract(
            name="peek", engine=ENGINE_SQL,
            params=(ParamContract(name="other", type_str="source_ref", position=0),),
            return_type="pd.DataFrame", signature="(other: source_ref) -> pd.DataFrame",
            body="-- name: peek\nSELECT COUNT(*) AS n FROM {other}",
        )
        calls = contract.bind(StepBinding(params=(
            ParamBinding(param_name="other", kind="source_ref", source_ref=str(source_id)),
        )))
        assert len(calls) == 1
        result = execute_sql_contract(
            db, contract, calls[0], frame=pd.DataFrame(), source_id=source_id,
        )
        assert isinstance(result, pd.DataFrame), result
        assert int(result["n"].iloc[0]) == 3
