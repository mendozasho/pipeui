"""Behavioral guarantees for attach_function and suggest_bindings workflows (Phase E1 / §13, #151).

attach_function guarantees:
  1. function_id auto-creates a function_set + function_set_map and writes
     source_function_map referencing the new set.
  2. set_id uses an existing function_set.
  3. source_function_map + alias_map rows commit atomically — failure leaves
     no partial rows.
  4. Missing column_backed/pd.Series binding returns structured failure (ok=False),
     identifies which params are missing.
  5. pd.DataFrame params require no binding and do not block save.
  6. scalar params require no binding and do not block save.
  7. Multi-bind: multiple column_ids for one param produce separate alias_map rows.
  8. Re-attaching the same bare function_id reuses the existing single-function set.

suggest_bindings guarantees:
  S1. Returns empty params list when there are no prior alias_map bindings.
  S2. Correctly surfaces columns on the target source that share a column_id
      with prior bindings for the same parameter_id on other sources.
  S3. pd.DataFrame params are excluded from the response.
  S4. scalar (non-str/pd.Series) params are excluded from the response.
  S5. Columns from prior bindings that do NOT exist on the target source are not
      included in suggested_columns.
  S6. Works for set_id input (aggregates params across all functions in the set).
  S7. When the same parameter was bound to multiple column_ids on different prior
      sources, ALL matching columns on the target source are returned.
  S8. Raises ValueError when both or neither of function_id/set_id are provided.
"""
from __future__ import annotations

import uuid

import pytest

import datetime

from pipeui.backend.data.base.ids import content_hash_id
from pipeui.backend.domain.functions.attach import AttachBinding, attach_function, get_pipeline, patch_pipeline_step, suggest_bindings
from tests.conftest import make_registered_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_function(conn, fn_name: str, params: list[tuple[str, str]]) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Insert a function_registry row + parameter rows. Returns (function_id, [param_id, ...])."""
    fn_id = uuid.uuid4()
    fn_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [fn_id, fn_ch, "transform", fn_name, f"Doc for {fn_name}", "pd.Series",
         ", ".join(f"{n}: {t}" for n, t in params), "transform", f"/tmp/{fn_name}.py", True],
    )
    param_ids = []
    for p_name, p_type in params:
        p_id = uuid.uuid4()
        p_ch = uuid.uuid4()
        conn.execute(
            "INSERT INTO parameter (param_id, content_hash_id, param_name, param_type, function_id) VALUES (?, ?, ?, ?, ?)",
            [p_id, p_ch, p_name, p_type, fn_id],
        )
        param_ids.append(p_id)
    return fn_id, param_ids


def _make_named_set(conn, set_name: str, fn_id: uuid.UUID, position: int = 0) -> uuid.UUID:
    """Insert a function_set + function_set_map for fn_id. Returns set_id."""
    set_id = uuid.uuid4()
    set_ch = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set (set_id, content_hash_id, set_name, set_description) VALUES (?, ?, ?, ?)",
        [set_id, set_ch, set_name, None],
    )
    sm_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) VALUES (?, ?, ?, ?)",
        [sm_id, set_id, fn_id, position],
    )
    return set_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_function_id_auto_creates_set(db):
    """Guarantee 1: function_id auto-creates function_set + function_set_map."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (param_id,) = _make_function(db, "my_fn", [("col", "str")])

    result = attach_function(
        db,
        source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )

    assert result["ok"] is True
    sfm_id = result["source_function_map_id"]

    # source_function_map row exists
    row = db.execute("SELECT set_id FROM source_function_map WHERE source_function_map_id = ?", [sfm_id]).fetchone()
    assert row is not None
    created_set_id = row[0]

    # function_set row was auto-created with function name
    set_row = db.execute("SELECT set_name FROM function_set WHERE set_id = ?", [created_set_id]).fetchone()
    assert set_row is not None
    assert set_row[0] == "my_fn"

    # function_set_map row at position 0
    sm_row = db.execute(
        "SELECT position FROM function_set_map WHERE set_id = ? AND function_id = ?",
        [created_set_id, fn_id],
    ).fetchone()
    assert sm_row is not None
    assert sm_row[0] == 0


@pytest.mark.integration
def test_set_id_uses_existing_set(db):
    """Guarantee 2: set_id uses the existing function_set."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_set", [("col", "str")])
    existing_set_id = _make_named_set(db, "My Set", fn_id)

    result = attach_function(
        db,
        source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        set_id=existing_set_id,
    )

    assert result["ok"] is True
    row = db.execute(
        "SELECT set_id FROM source_function_map WHERE source_function_map_id = ?",
        [result["source_function_map_id"]],
    ).fetchone()
    assert str(row[0]) == str(existing_set_id)


@pytest.mark.integration
def test_missing_binding_returns_structured_failure(db):
    """Guarantee 4: missing column_backed/pd.Series binding returns structured failure."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_missing", [("col", "str")])

    # No bindings provided
    result = attach_function(db, source_id, [], function_id=fn_id)

    assert result["ok"] is False
    assert len(result["missing_params"]) == 1
    assert result["missing_params"][0]["param_name"] == "col"
    assert "detail" in result

    # No partial rows written
    sfm_count = db.execute("SELECT COUNT(*) FROM source_function_map").fetchone()[0]
    assert sfm_count == 0


@pytest.mark.integration
def test_dataframe_param_exempt_from_binding(db):
    """Guarantee 5: pd.DataFrame params don't block save."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_df", [("df", "pd.DataFrame")])

    result = attach_function(db, source_id, [], function_id=fn_id)
    assert result["ok"] is True


@pytest.mark.integration
def test_scalar_param_exempt_from_binding(db):
    """Guarantee 6: scalar params (non-str, non-pd.Series) don't block save."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_scalar", [("threshold", "int")])

    result = attach_function(db, source_id, [], function_id=fn_id)
    assert result["ok"] is True


@pytest.mark.integration
def test_str_param_plain_string_literal_attaches_without_binding(db):
    """Bug #186 (1): a str param in plain-string mode (literal value provided, no
    column binding) is exempt from the binding requirement and the literal is
    persisted to source_scalar_map."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_str_plain", [("label", "str")])

    result = attach_function(
        db, source_id, [],
        function_id=fn_id,
        scalar_values={param_id: "hello"},
    )
    assert result["ok"] is True, result

    # Literal persisted to source_scalar_map
    row = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, param_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "hello"

    # No alias_map row written for a plain-string str param
    alias_count = db.execute(
        "SELECT COUNT(*) FROM alias_map WHERE parameter_id = ? AND source_id = ?",
        [param_id, source_id],
    ).fetchone()[0]
    assert alias_count == 0


@pytest.mark.integration
def test_str_param_without_binding_or_value_still_fails(db):
    """Bug #186 (1): a str param with neither a column binding nor a literal value
    still returns the structured 'missing required column bindings' failure."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_str_novalue", [("label", "str")])

    result = attach_function(db, source_id, [], function_id=fn_id)
    assert result["ok"] is False
    assert len(result["missing_params"]) == 1
    assert result["missing_params"][0]["param_name"] == "label"


@pytest.mark.integration
def test_scalar_value_persisted_on_attach(db):
    """Bug #186 (1)/(2): a scalar (int) param's literal value provided at attach
    time is persisted to source_scalar_map in the same call."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_int_attach", [("threshold", "int")])

    result = attach_function(
        db, source_id, [],
        function_id=fn_id,
        scalar_values={param_id: "11"},
    )
    assert result["ok"] is True
    row = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, param_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "11"


@pytest.mark.integration
def test_append_name_persisted_on_attach(db):
    """Slice 4b: an append-mode transform attached with a user-provided append_name
    persists a NORMALIZED (column-ready) name to source_function_map.append_name in
    the same call — "My Score" -> "My_Score"."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (param_id,) = _make_function(db, "fn_append_named", [("col", "str")])

    result = attach_function(
        db,
        source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
        output_mode="append",
        append_name="My Score",
    )
    assert result["ok"] is True
    row = db.execute(
        "SELECT append_name FROM source_function_map WHERE source_function_map_id = ?",
        [result["source_function_map_id"]],
    ).fetchone()
    assert row is not None
    assert row[0] == "My_Score"


@pytest.mark.integration
def test_append_name_null_when_not_provided(db):
    """Slice 4b: with no append_name, the persisted value is NULL so the runtime
    falls back to the cleaned auto-label."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (param_id,) = _make_function(db, "fn_append_unnamed", [("col", "str")])

    result = attach_function(
        db,
        source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
        output_mode="append",
    )
    assert result["ok"] is True
    row = db.execute(
        "SELECT append_name FROM source_function_map WHERE source_function_map_id = ?",
        [result["source_function_map_id"]],
    ).fetchone()
    assert row[0] is None


@pytest.mark.integration
def test_multi_bind_produces_separate_alias_map_rows(db):
    """Guarantee 7: multiple column_ids for one param produce separate alias_map rows."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    fn_id, (param_id,) = _make_function(db, "fn_multi", [("cols", "pd.Series")])

    result = attach_function(
        db,
        source_id,
        [AttachBinding(param_id=param_id, column_ids=col_ids)],
        function_id=fn_id,
    )
    assert result["ok"] is True

    alias_count = db.execute(
        "SELECT COUNT(*) FROM alias_map WHERE parameter_id = ? AND source_id = ?",
        [param_id, source_id],
    ).fetchone()[0]
    assert alias_count == 3


@pytest.mark.integration
def test_function_id_reuses_existing_single_function_set(db):
    """Guarantee 8: re-attaching the same function_id reuses the existing auto-set."""
    source_id, col_ids = make_registered_source(db, n_columns=1)

    # Create a second source that reuses the same column (by design col_0/INTEGER
    # is already in column_registry; we just add a source_column_map row).
    source_id_2 = uuid.uuid4()
    ch2 = content_hash_id("source_registry", f"src2_{source_id_2}", "id", "upsert")
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_id_2, ch2, f"src2_{source_id_2}", datetime.date.today(), "upsert", "id"],
    )
    scm_id2 = content_hash_id("source_column_map", str(source_id_2), str(col_ids[0]))
    db.execute(
        "INSERT INTO source_column_map VALUES (?, ?, ?)",
        [scm_id2, col_ids[0], source_id_2],
    )

    fn_id, (param_id,) = _make_function(db, "fn_reuse", [("col", "str")])

    # First attach — creates auto-set
    result1 = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )
    assert result1["ok"] is True

    # Second attach to different source — reuses auto-set
    result2 = attach_function(
        db, source_id_2,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )
    assert result2["ok"] is True

    # Only one function_set row should exist
    set_count = db.execute("SELECT COUNT(*) FROM function_set").fetchone()[0]
    assert set_count == 1

    # Both source_function_map rows reference the same set
    rows = db.execute("SELECT set_id FROM source_function_map").fetchall()
    set_ids = {str(r[0]) for r in rows}
    assert len(set_ids) == 1


@pytest.mark.integration
def test_transaction_atomicity_on_duplicate_alias_map(db):
    """Guarantee 3: duplicate alias_map_id causes rollback — no partial rows."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_dup", [("col", "str")])

    # Pre-insert an alias_map row with the same deterministic id
    from pipeui.backend.data.base.ids import content_hash_id as chid
    dup_id = chid("alias_map", str(param_id), str(col_ids[0]), str(source_id))
    alias_uuid = uuid.uuid4()
    db.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
        [dup_id, col_ids[0], param_id, source_id],
    )

    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )

    # Should return failure
    assert result["ok"] is False

    # source_function_map must be empty (rolled back)
    sfm_count = db.execute("SELECT COUNT(*) FROM source_function_map").fetchone()[0]
    assert sfm_count == 0


# ---------------------------------------------------------------------------
# suggest_bindings tests
# ---------------------------------------------------------------------------

def _insert_alias(conn, column_id, param_id, source_id):
    alias_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO alias_map (alias_map_id, column_id, parameter_id, source_id) VALUES (?, ?, ?, ?)",
        [alias_id, column_id, param_id, source_id],
    )


@pytest.mark.integration
def test_suggest_no_prior_bindings_returns_empty(db):
    """Guarantee S1: no prior alias_map rows → param included but suggested_columns empty."""
    source_id, _ = make_registered_source(db, n_columns=2)
    fn_id, (p_id,) = _make_function(db, "fn_suggest_s1", [("col", "str")])

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert len(result["params"]) == 1
    assert result["params"][0]["suggested_columns"] == []


@pytest.mark.integration
def test_suggest_prior_binding_surfaces_matching_column(db):
    """Guarantee S2: prior alias_map binding on another source surfaces the matching column."""
    target_source_id, target_col_ids = make_registered_source(db, n_columns=2)
    other_source_id, _ = make_registered_source(db, n_columns=0)

    # Manually add target_col_ids[0] to the other source's column map
    scm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_column_map VALUES (?, ?, ?)",
        [scm_id, target_col_ids[0], other_source_id],
    )

    fn_id, (p_id,) = _make_function(db, "fn_suggest_s2", [("col", "str")])
    _insert_alias(db, target_col_ids[0], p_id, other_source_id)

    result = suggest_bindings(db, target_source_id, function_id=fn_id)
    params = result["params"]
    assert len(params) == 1
    assert len(params[0]["suggested_columns"]) == 1
    assert params[0]["suggested_columns"][0]["column_id"] == str(target_col_ids[0])


@pytest.mark.integration
def test_suggest_excludes_dataframe_params(db):
    """Guarantee S3: pd.DataFrame params are not included in suggestions."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, _ = _make_function(db, "fn_suggest_s3", [("df", "pd.DataFrame")])

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert result["params"] == []


@pytest.mark.integration
def test_suggest_includes_scalar_params_with_kind(db):
    """Guarantee S4 (updated #151): int/float/bool params are included with param_kind='scalar'
    and empty suggested_columns, not excluded."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_suggest_s4", [("threshold", "float")])

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert len(result["params"]) == 1
    p = result["params"][0]
    assert p["param_kind"] == "scalar"
    assert p["suggested_columns"] == []
    assert p["current_scalar_value"] is None


@pytest.mark.integration
def test_suggest_excludes_column_not_on_target(db):
    """Guarantee S5: columns bound on other sources absent from target are not returned."""
    import datetime as _dt
    from pipeui.backend.data.base.ids import content_hash_id as _ch
    target_source_id, _ = make_registered_source(db, n_columns=1)

    # Create a source with a uniquely-named column (not shared with target)
    other_source_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [other_source_id, uuid.uuid4(), f"other_s5_{other_source_id}", _dt.date.today(), "upsert", "id"],
    )
    other_col_id = uuid.uuid4()
    other_col_name = f"unique_s5_{other_source_id}"
    other_col_ch = _ch("column_registry", other_col_name, "INTEGER")
    db.execute("INSERT INTO column_registry VALUES (?, ?, ?, ?)", [other_col_id, other_col_ch, other_col_name, "INTEGER"])
    scm_id = uuid.uuid4()
    db.execute("INSERT INTO source_column_map VALUES (?, ?, ?)", [scm_id, other_col_id, other_source_id])

    fn_id, (p_id,) = _make_function(db, "fn_suggest_s5", [("col", "str")])
    _insert_alias(db, other_col_id, p_id, other_source_id)

    result = suggest_bindings(db, target_source_id, function_id=fn_id)
    assert result["params"][0]["suggested_columns"] == []


@pytest.mark.integration
def test_suggest_set_id_aggregates_params(db):
    """Guarantee S6: set_id input aggregates eligible params across all functions in set."""
    import datetime as _dt
    target_source_id, target_col_ids = make_registered_source(db, n_columns=2)
    other_source_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [other_source_id, uuid.uuid4(), f"other_s6_{other_source_id}", _dt.date.today(), "upsert", "id"],
    )
    for col_id in target_col_ids:
        scm_id = uuid.uuid4()
        db.execute("INSERT INTO source_column_map VALUES (?, ?, ?)", [scm_id, col_id, other_source_id])

    fn_a_id, (p_a_id,) = _make_function(db, "fn_a_s6", [("col_a", "str")])
    fn_b_id, (p_b_id,) = _make_function(db, "fn_b_s6", [("series_b", "pd.Series")])

    _insert_alias(db, target_col_ids[0], p_a_id, other_source_id)
    _insert_alias(db, target_col_ids[1], p_b_id, other_source_id)

    set_id = _make_named_set(db, "Set S6", fn_a_id)
    sm_id = uuid.uuid4()
    db.execute(
        "INSERT INTO function_set_map (set_map_id, set_id, function_id, position) VALUES (?, ?, ?, ?)",
        [sm_id, set_id, fn_b_id, 1],
    )

    result = suggest_bindings(db, target_source_id, set_id=set_id)
    param_names = {p["param_name"] for p in result["params"]}
    assert "col_a" in param_names
    assert "series_b" in param_names


@pytest.mark.integration
def test_suggest_tiebreaker_returns_all_matches(db):
    """Guarantee S7: when the same param was bound to multiple columns, all are returned."""
    import datetime as _dt
    target_source_id, target_col_ids = make_registered_source(db, n_columns=2)
    source_a_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_a_id, uuid.uuid4(), f"src_a_s7_{source_a_id}", _dt.date.today(), "upsert", "id"],
    )
    source_b_id = uuid.uuid4()
    db.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [source_b_id, uuid.uuid4(), f"src_b_s7_{source_b_id}", _dt.date.today(), "upsert", "id"],
    )

    fn_id, (p_id,) = _make_function(db, "fn_suggest_s7", [("col", "str")])
    _insert_alias(db, target_col_ids[0], p_id, source_a_id)
    _insert_alias(db, target_col_ids[1], p_id, source_b_id)

    result = suggest_bindings(db, target_source_id, function_id=fn_id)
    col_ids = {c["column_id"] for c in result["params"][0]["suggested_columns"]}
    assert str(target_col_ids[0]) in col_ids
    assert str(target_col_ids[1]) in col_ids


@pytest.mark.integration
def test_suggest_raises_when_neither_id_provided(db):
    """Guarantee S8a: ValueError when neither function_id nor set_id is provided."""
    source_id, _ = make_registered_source(db, n_columns=1)
    with pytest.raises(ValueError):
        suggest_bindings(db, source_id)


@pytest.mark.integration
def test_suggest_raises_when_both_ids_provided(db):
    """Guarantee S8b: ValueError when both function_id and set_id are provided."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, _ = _make_function(db, "fn_s8b", [])
    set_id = _make_named_set(db, "Set S8b", fn_id)
    with pytest.raises(ValueError):
        suggest_bindings(db, source_id, function_id=fn_id, set_id=set_id)


# ---------------------------------------------------------------------------
# suggest_bindings — scalar params + available_columns + current_scalar_value
# (#151 additions)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_suggest_includes_scalar_params_with_kind_scalar(db):
    """Guarantee S9: scalar params (int/float/bool) appear in response with param_kind='scalar'."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_int, p_float, p_bool) = _make_function(
        db, "fn_scalar_kinds",
        [("threshold", "int"), ("ratio", "float"), ("flag", "bool")],
    )

    result = suggest_bindings(db, source_id, function_id=fn_id)
    params = result["params"]
    assert len(params) == 3
    kinds = {p["param_name"]: p["param_kind"] for p in params}
    assert kinds["threshold"] == "scalar"
    assert kinds["ratio"] == "scalar"
    assert kinds["flag"] == "scalar"
    for p in params:
        assert p["suggested_columns"] == []
        assert p["current_scalar_value"] is None


@pytest.mark.integration
def test_suggest_column_params_have_kind_column(db):
    """Guarantee S10: str/pd.Series params have param_kind='column'."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (p_str, p_series) = _make_function(
        db, "fn_col_kinds",
        [("col_a", "str"), ("col_b", "pd.Series")],
    )

    result = suggest_bindings(db, source_id, function_id=fn_id)
    params = result["params"]
    assert len(params) == 2
    for p in params:
        assert p["param_kind"] == "column"


@pytest.mark.integration
def test_suggest_available_columns_from_source(db):
    """Guarantee S11: available_columns contains the source's column_registry rows."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    fn_id, _ = _make_function(db, "fn_avail", [("x", "str")])

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert "available_columns" in result
    avail_ids = {c["column_id"] for c in result["available_columns"]}
    for cid in col_ids:
        assert str(cid) in avail_ids


@pytest.mark.integration
def test_suggest_current_scalar_value_null_when_no_row(db):
    """Guarantee S12: current_scalar_value is null when no source_scalar_map row exists."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_scalar_null", [("n", "int")])

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert result["params"][0]["current_scalar_value"] is None


@pytest.mark.integration
def test_suggest_current_scalar_value_populated_from_map(db):
    """Guarantee S13: current_scalar_value is populated from source_scalar_map when row exists."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_scalar_val", [("n", "int")])

    # Manually insert a source_scalar_map row
    db.execute(
        "INSERT INTO source_scalar_map (scalar_map_id, source_id, param_id, value) VALUES (?, ?, ?, ?)",
        [uuid.uuid4(), source_id, p_id, "42"],
    )

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert result["params"][0]["current_scalar_value"] == "42"


# ---------------------------------------------------------------------------
# patch_pipeline_step — bindings + scalar_values (#151 additions)
# ---------------------------------------------------------------------------

def _attach_fn(conn, source_id, fn_id, col_ids):
    """Helper: attach function_id to source_id with first column bound."""
    from pipeui.backend.domain.functions.attach import AttachBinding
    params = conn.execute(
        "SELECT param_id, param_type FROM parameter WHERE function_id = ?", [fn_id]
    ).fetchall()
    bindings = [
        AttachBinding(param_id=p_id, column_ids=[col_ids[0]])
        for p_id, p_type in params
        if p_type in {"str", "pd.Series"}
    ]
    return attach_function(conn, source_id, bindings, function_id=fn_id)


@pytest.mark.integration
def test_patch_scalar_values_upserts_into_source_scalar_map(db):
    """Guarantee P1: PATCH with scalar_values upserts into source_scalar_map."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_patch_scalar", [("n", "int")])
    result = attach_function(db, source_id, [], function_id=fn_id)
    assert result["ok"] is True
    sfm_id = uuid.UUID(result["source_function_map_id"])

    ok = patch_pipeline_step(db, source_id, sfm_id, scalar_values={p_id: "99"})
    assert ok is True

    row = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchone()
    assert row is not None
    assert row[0] == "99"


@pytest.mark.integration
def test_patch_scalar_values_upsert_updates_existing(db):
    """Guarantee P2: second PATCH with same (source_id, param_id) updates, no duplicate row."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_patch_upsert", [("n", "int")])
    result = attach_function(db, source_id, [], function_id=fn_id)
    sfm_id = uuid.UUID(result["source_function_map_id"])

    patch_pipeline_step(db, source_id, sfm_id, scalar_values={p_id: "1"})
    patch_pipeline_step(db, source_id, sfm_id, scalar_values={p_id: "2"})

    rows = db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2"


@pytest.mark.integration
def test_patch_bindings_replaces_alias_map_rows_atomically(db):
    """Guarantee P3: PATCH with bindings replaces all alias_map rows for the step atomically."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (p_id,) = _make_function(db, "fn_patch_bind", [("col", "str")])
    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=p_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )
    sfm_id = uuid.UUID(result["source_function_map_id"])

    # Initially bound to col_ids[0]; patch to bind to col_ids[1] only
    ok = patch_pipeline_step(db, source_id, sfm_id, bindings={p_id: [col_ids[1]]})
    assert ok is True

    rows = db.execute(
        "SELECT column_id FROM alias_map WHERE parameter_id = ? AND source_id = ?",
        [p_id, source_id],
    ).fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == str(col_ids[1])


# ---------------------------------------------------------------------------
# get_pipeline — scalar value per param (Bug #186 (2))
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_get_pipeline_returns_scalar_value_for_param(db):
    """Bug #186 (2): get_pipeline returns the persisted scalar value per param so
    a placed-step card can show '= 11' rather than 'unbound'."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_pipe_scalar", [("threshold", "int")])

    result = attach_function(
        db, source_id, [],
        function_id=fn_id,
        scalar_values={param_id: "11"},
    )
    assert result["ok"] is True

    pipeline = get_pipeline(db, source_id)
    params = pipeline["steps"][0]["functions"][0]["params"]
    threshold = next(p for p in params if p["param_name"] == "threshold")
    assert threshold["scalar_value"] == "11"


@pytest.mark.integration
def test_get_pipeline_scalar_value_none_when_unset(db):
    """Bug #186 (2): a scalar param with no persisted value reports scalar_value=None."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_pipe_scalar_none", [("threshold", "int")])

    result = attach_function(db, source_id, [], function_id=fn_id)
    assert result["ok"] is True

    pipeline = get_pipeline(db, source_id)
    params = pipeline["steps"][0]["functions"][0]["params"]
    threshold = next(p for p in params if p["param_name"] == "threshold")
    assert threshold["scalar_value"] is None


# ---------------------------------------------------------------------------
# suggest_bindings — edit-state restore (#186 sub-issues #188, #191)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_suggest_returns_function_doc(db):
    """Guarantee (#188): every suggest_bindings param carries its owning function's
    description so the mapping modal can label/tooltip it, instead of a bare hint."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_doc", [("col", "str")])

    result = suggest_bindings(db, source_id, function_id=fn_id)
    assert result["params"][0]["function_doc"] == "Doc for fn_doc"


@pytest.mark.integration
def test_suggest_returns_current_scalar_value_for_str(db):
    """Guarantee (#191/#192): a str param persisted in plain-string mode round-trips
    its value through suggest_bindings as current_scalar_value, so re-opening the step
    restores the typed text (previously hard-coded None for str → value disappeared)."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_str_scalar", [("label", "str")])

    result = attach_function(db, source_id, [], function_id=fn_id, scalar_values={p_id: "hello"})
    assert result["ok"] is True

    res = suggest_bindings(db, source_id, function_id=fn_id)
    p = res["params"][0]
    assert p["param_type"] == "str"
    assert p["current_scalar_value"] == "hello"


@pytest.mark.integration
def test_suggest_returns_current_bindings_for_attached_step(db):
    """Guarantee (#191): suggest_bindings returns the param's existing alias_map rows on
    THIS source as current_bindings, so re-opening a placed step restores its column
    checkbox selections (drives edit pre-fill)."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (p_id,) = _make_function(db, "fn_cur_bind", [("col", "str")])

    attach_function(
        db, source_id,
        [AttachBinding(param_id=p_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )

    res = suggest_bindings(db, source_id, function_id=fn_id)
    cur = res["params"][0]["current_bindings"]
    assert len(cur) == 1
    assert cur[0]["column_id"] == str(col_ids[0])


@pytest.mark.integration
def test_suggest_returns_current_bindings_in_saved_position_order(db):
    """#260 / Principle 7 (edits preserve persisted values): current_bindings must come
    back in saved alias_map.position order, NOT alphabetical by column_name — otherwise
    re-opening a multi-column step for edit silently resets the user's column order, and
    saving re-persists the wrong order."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    fn_id, (p_id,) = _make_function(db, "fn_colorder", [("col", "str")])
    # Bind in REVERSE so saved position order (col_2, col_1, col_0) differs from the
    # alphabetical column_name order (col_0, col_1, col_2).
    reversed_cols = [col_ids[2], col_ids[1], col_ids[0]]
    attach_function(
        db, source_id,
        [AttachBinding(param_id=p_id, column_ids=reversed_cols)],
        function_id=fn_id,
    )

    res = suggest_bindings(db, source_id, function_id=fn_id)
    got = [b["column_id"] for b in res["params"][0]["current_bindings"]]
    assert got == [str(c) for c in reversed_cols], (
        f"current_bindings must round-trip in saved position order {reversed_cols}, got {got}"
    )


@pytest.mark.integration
def test_suggest_current_scalar_value_none_when_unset(db):
    """A str param with no persisted plain-string value reports current_scalar_value=None
    and empty current_bindings (initial attach state)."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_str_unset", [("label", "str")])

    res = suggest_bindings(db, source_id, function_id=fn_id)
    p = res["params"][0]
    assert p["current_scalar_value"] is None
    assert p["current_bindings"] == []


@pytest.mark.integration
def test_patch_clears_scalar_on_blank(db):
    """Guarantee (#191): PATCH with a blank scalar value clears the source_scalar_map row
    so the param reverts to its Python default (edit can un-set a value, not just change it)."""
    source_id, _ = make_registered_source(db, n_columns=1)
    fn_id, (p_id,) = _make_function(db, "fn_clear_scalar", [("n", "int")])
    result = attach_function(db, source_id, [], function_id=fn_id, scalar_values={p_id: "7"})
    sfm_id = uuid.UUID(result["source_function_map_id"])
    assert db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchone() is not None

    ok = patch_pipeline_step(db, source_id, sfm_id, scalar_values={p_id: ""})
    assert ok is True
    assert db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchone() is None


@pytest.mark.integration
def test_patch_str_text_to_column_clears_scalar_and_binds(db):
    """Finding 1: switching a str param from plain-string to column mode in one PATCH
    (bindings + blank scalar) leaves the column binding and clears the stale scalar, so
    the step keeps a single source of truth (no orphaned source_scalar_map value)."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (p_id,) = _make_function(db, "fn_str_switch", [("label", "str")])
    # Attach in plain-string mode with a literal "foo".
    result = attach_function(db, source_id, [], function_id=fn_id, scalar_values={p_id: "foo"})
    sfm_id = uuid.UUID(result["source_function_map_id"])

    # Switch to column mode: bind a column AND clear the scalar in the same call.
    ok = patch_pipeline_step(
        db, source_id, sfm_id,
        bindings={p_id: [col_ids[0]]},
        scalar_values={p_id: ""},
    )
    assert ok is True

    alias_rows = db.execute(
        "SELECT column_id FROM alias_map WHERE parameter_id = ? AND source_id = ?",
        [p_id, source_id],
    ).fetchall()
    assert len(alias_rows) == 1
    assert str(alias_rows[0][0]) == str(col_ids[0])
    assert db.execute(
        "SELECT value FROM source_scalar_map WHERE source_id = ? AND param_id = ?",
        [source_id, p_id],
    ).fetchone() is None


# ---------------------------------------------------------------------------
# get_pipeline — built-in steps in the unified canvas list (#209)
# ---------------------------------------------------------------------------

def _make_right_source(conn):
    """Register a second source with a uniquely-named column, to avoid the
    column_registry content_hash_id collision a second make_registered_source
    would cause. Returns source_id."""
    src_id = uuid.uuid4()
    db_ch = content_hash_id("source_registry", f"right_{src_id}", "id", "upsert")
    conn.execute(
        "INSERT INTO source_registry VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL)",
        [src_id, db_ch, f"right_{src_id}", datetime.date.today(), "upsert", "id"],
    )
    col_id = uuid.uuid4()
    col_name = f"rcol_{src_id.hex[:6]}"
    col_ch = content_hash_id("column_registry", col_name, "INTEGER")
    conn.execute("INSERT INTO column_registry VALUES (?, ?, ?, ?)", [col_id, col_ch, col_name, "INTEGER"])
    map_id = content_hash_id("source_column_map", str(src_id), str(col_id))
    conn.execute("INSERT INTO source_column_map VALUES (?, ?, ?)", [map_id, col_id, src_id])
    return src_id, col_name


@pytest.mark.integration
def test_get_pipeline_emits_builtin_step_with_discriminator(db):
    """#209 AC1: get_pipeline returns a placed built-in step in steps[] with
    step_type='builtin', carrying builtin_type and builtin_config."""
    from pipeui.backend.domain.functions.builtins import attach_builtin

    source_id, col_ids = make_registered_source(db, n_columns=1)
    right_source_id, rcol = _make_right_source(db)
    cfg = {
        "right_source_id": str(right_source_id),
        "join_type": "inner",
        "on": [{"left_col": "col_0", "right_col": rcol}],
        "keep_columns": "all",
    }
    res = attach_builtin(db, source_id, "join", cfg)
    assert res["ok"] is True

    pipeline = get_pipeline(db, source_id)
    steps = pipeline["steps"]
    assert len(steps) == 1
    bstep = steps[0]
    assert bstep["step_type"] == "builtin"
    assert bstep["builtin_type"] == "join"
    assert bstep["step_id"] == res["step_id"]
    assert bstep["builtin_config"]["right_source_id"] == str(right_source_id)


@pytest.mark.integration
def test_get_pipeline_function_steps_carry_step_type_and_functions(db):
    """#209 AC2: function steps carry step_type='function' and still include their
    full nested functions[] payload — no regression."""
    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, (param_id,) = _make_function(db, "fn_step_type", [("col", "str")])
    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=[col_ids[0]])],
        function_id=fn_id,
    )
    assert result["ok"] is True

    pipeline = get_pipeline(db, source_id)
    fstep = pipeline["steps"][0]
    assert fstep["step_type"] == "function"
    # functions[] nested payload preserved
    assert fstep["functions"][0]["function_name"] == "fn_step_type"
    assert fstep["functions"][0]["params"][0]["param_name"] == "col"


@pytest.mark.integration
def test_get_pipeline_orders_builtin_among_function_steps_by_position(db):
    """#209 AC1: a built-in step is position-ordered among function steps. Function
    at position 0, built-in at position 1 → function first, built-in second."""
    from pipeui.backend.domain.functions.builtins import attach_builtin

    source_id, col_ids = make_registered_source(db, n_columns=1)
    fn_id, _ = _make_function(db, "fn_ordered", [("df", "pd.DataFrame")])
    fres = attach_function(db, source_id, [], function_id=fn_id)
    assert fres["ok"] is True

    right_source_id, rcol = _make_right_source(db)
    cfg = {
        "right_source_id": str(right_source_id),
        "join_type": "left",
        "on": [{"left_col": "col_0", "right_col": rcol}],
        "keep_columns": "all",
    }
    bres = attach_builtin(db, source_id, "join", cfg)
    assert bres["ok"] is True

    steps = get_pipeline(db, source_id)["steps"]
    types = [s["step_type"] for s in steps]
    assert types == ["function", "builtin"]


# ---------------------------------------------------------------------------
# Slice 2 — alias_map.position (add-order + ordered reads)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_attach_writes_alias_map_position_in_add_order(db):
    """Slice 2 #1: attach writes alias_map.position in the order columns were provided."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    fn_id, (param_id,) = _make_function(db, "fn_pos", [("cols", "pd.Series")])

    # Provide columns in a NON-alphabetical order: col_2, col_0, col_1
    ordered = [col_ids[2], col_ids[0], col_ids[1]]
    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=ordered)],
        function_id=fn_id,
    )
    assert result["ok"] is True

    rows = db.execute(
        "SELECT column_id, position FROM alias_map WHERE parameter_id = ? AND source_id = ? ORDER BY position",
        [param_id, source_id],
    ).fetchall()
    got = [(str(cid), pos) for cid, pos in rows]
    assert got == [(str(ordered[0]), 0), (str(ordered[1]), 1), (str(ordered[2]), 2)]


@pytest.mark.integration
def test_get_pipeline_reads_bindings_in_position_order(db):
    """Slice 2 #2: get_pipeline reads a param's bound columns ORDER BY position,
    not alphabetically by column_name."""
    source_id, col_ids = make_registered_source(db, n_columns=3)
    fn_id, (param_id,) = _make_function(db, "fn_pos_get", [("cols", "pd.Series")])

    # Attach in reverse-alphabetical add order: col_2, col_1, col_0
    ordered = [col_ids[2], col_ids[1], col_ids[0]]
    attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=ordered)],
        function_id=fn_id,
    )

    pipe = get_pipeline(db, source_id)
    fn = pipe["steps"][0]["functions"][0]
    param = next(p for p in fn["params"] if p["param_id"] == str(param_id))
    names = [b["column_name"] for b in param["bindings"]]
    # add-order is col_2, col_1, col_0 — must NOT be the alphabetical col_0, col_1, col_2
    assert names == ["col_2", "col_1", "col_0"]


# ---------------------------------------------------------------------------
# Slice 3 (#234) — equal-length-among-varying guard rejected at attach
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_unequal_varying_bindings_rejected_as_structured_failure(db):
    """Slice 3 #1: two varying params binding different column counts (3,2) is
    rejected at attach with a structured failure (ok=False), NOT a raised 500."""
    source_id, col_ids = make_registered_source(db, n_columns=5)
    fn_id, (p_a, p_b) = _make_function(
        db, "fn_two_varying", [("a", "pd.Series"), ("b", "pd.Series")]
    )

    result = attach_function(
        db,
        source_id,
        [
            AttachBinding(param_id=p_a, column_ids=col_ids[:3]),  # 3 columns
            AttachBinding(param_id=p_b, column_ids=col_ids[3:5]),  # 2 columns
        ],
        function_id=fn_id,
    )

    assert result["ok"] is False
    # Structured failure carries a human-readable detail naming the conflict.
    assert "3" in result["detail"] and "2" in result["detail"]


@pytest.mark.integration
def test_unequal_varying_attach_writes_no_rows(db):
    """Slice 3 #1: a rejected unequal attach leaves no source_function_map and no
    alias_map rows — the guard fires before the write transaction."""
    source_id, col_ids = make_registered_source(db, n_columns=5)
    fn_id, (p_a, p_b) = _make_function(
        db, "fn_two_varying_atomic", [("a", "pd.Series"), ("b", "pd.Series")]
    )

    attach_function(
        db,
        source_id,
        [
            AttachBinding(param_id=p_a, column_ids=col_ids[:3]),
            AttachBinding(param_id=p_b, column_ids=col_ids[3:5]),
        ],
        function_id=fn_id,
    )

    sfm_count = db.execute(
        "SELECT COUNT(*) FROM source_function_map WHERE source_id = ?", [source_id]
    ).fetchone()[0]
    alias_count = db.execute(
        "SELECT COUNT(*) FROM alias_map WHERE source_id = ?", [source_id]
    ).fetchone()[0]
    assert sfm_count == 0
    assert alias_count == 0


@pytest.mark.integration
def test_varying_plus_static_attach_is_allowed(db):
    """Slice 3 #1: 3 varying + 1 static (length-1 broadcast) attaches cleanly —
    the static param does not trip the equal-length rule."""
    source_id, col_ids = make_registered_source(db, n_columns=4)
    fn_id, (p_a, p_b) = _make_function(
        db, "fn_varying_static", [("a", "pd.Series"), ("b", "pd.Series")]
    )

    result = attach_function(
        db,
        source_id,
        [
            AttachBinding(param_id=p_a, column_ids=col_ids[:3]),  # varying, N=3
            AttachBinding(param_id=p_b, column_ids=[col_ids[3]]),  # static — broadcasts
        ],
        function_id=fn_id,
    )
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Slice 4 (#238/#240) — output-target map written on replace attach
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_replace_attach_writes_output_target_map_in_order(db):
    """Slice 4 #2: a replace attach with N ordered target columns writes
    output_target_map rows the runner reads in order (bundle i -> target i)."""
    source_id, col_ids = make_registered_source(db, n_columns=4)
    fn_id, (param_id,) = _make_function(db, "fn_replace_targets", [("cols", "pd.Series")])

    # Bind 2 varying columns (N=2 bundles); choose 2 ordered targets in a NON-input
    # order so we can prove the persisted order is the chosen one.
    targets = [col_ids[3], col_ids[2]]
    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=col_ids[:2])],
        function_id=fn_id,
        output_mode="replace",
        output_targets=targets,
    )
    assert result["ok"] is True, result
    sfm_id = result["source_function_map_id"]

    rows = db.execute(
        "SELECT column_id, position FROM output_target_map WHERE source_function_map_id = ? ORDER BY position",
        [sfm_id],
    ).fetchall()
    got = [(str(cid), pos) for cid, pos in rows]
    assert got == [(str(targets[0]), 0), (str(targets[1]), 1)]


@pytest.mark.integration
def test_replace_target_count_mismatch_rejected_as_structured_failure(db):
    """Slice 4 #1: replace target count must equal the bundle count. 2 bundles + 3
    targets is rejected as a structured failure (ok=False), not a raised 500, and
    no map rows are written."""
    source_id, col_ids = make_registered_source(db, n_columns=5)
    fn_id, (param_id,) = _make_function(db, "fn_replace_mismatch", [("cols", "pd.Series")])

    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=col_ids[:2])],  # N=2 bundles
        function_id=fn_id,
        output_mode="replace",
        output_targets=col_ids[2:5],  # 3 targets — mismatch
    )
    assert result["ok"] is False
    assert "2" in result["detail"] and "3" in result["detail"]

    sfm_count = db.execute(
        "SELECT COUNT(*) FROM source_function_map WHERE source_id = ?", [source_id]
    ).fetchone()[0]
    otm_count = db.execute("SELECT COUNT(*) FROM output_target_map").fetchone()[0]
    assert sfm_count == 0
    assert otm_count == 0


@pytest.mark.integration
def test_append_attach_writes_no_output_target_rows(db):
    """Slice 4 #0: an append step writes no output_target_map rows — output targets
    are a replace-only concept."""
    source_id, col_ids = make_registered_source(db, n_columns=2)
    fn_id, (param_id,) = _make_function(db, "fn_append_no_targets", [("cols", "pd.Series")])

    result = attach_function(
        db, source_id,
        [AttachBinding(param_id=param_id, column_ids=col_ids[:2])],
        function_id=fn_id,
        output_mode="append",
    )
    assert result["ok"] is True
    otm_count = db.execute(
        "SELECT COUNT(*) FROM output_target_map WHERE source_function_map_id = ?",
        [result["source_function_map_id"]],
    ).fetchone()[0]
    assert otm_count == 0
