"""Behavioral guarantees for attach_function workflow (Phase E1 / §13).

Guarantees under test:
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
"""
from __future__ import annotations

import uuid

import pytest

import datetime

from pipeui.ids import content_hash_id
from pipeui.duckdb import create_schema, get_connection
from pipeui.workflow.attach import AttachBinding, attach_function
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
            "INSERT INTO parameter VALUES (?, ?, ?, ?, ?)",
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
    from pipeui.ids import content_hash_id as chid
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
