"""Function registration (functions domain) — the DB transaction + scan entry point.

register_function_entry(conn, file_path, fn_name, data)
    Write one function_registry row + all its parameter rows in a single
    transaction (§10), collapsing on content_hash_id (Principle 2).

scan_functions(conn, functions_paths)
    Discover (.py/.sql) every function under the given dirs, register each, and
    deactivate registry rows whose source file vanished. Returns a scan log.

Split out of the monolithic discovery+classification+registration module (#47):
this is the transaction owner — it holds the DuckDB connection. Classification
(``classification``) and discovery/parsing (``discovery``) are DB-free modules it
sits above; the function read-API lives in ``function_read``.

§10: function_registry row + all parameter rows = one transaction per function.
§2:  content_hash_id = uuid5(table_namespace, function_name|function_class|function_return_type)
Principle 2: collapse on content_hash_id — preserve surrogate function_id, overwrite mutables.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from pipeui.backend.data.base.ids import content_hash_id, new_id
from pipeui.backend.domain.functions.discovery import extract_contracts


def register_function_entry(
    conn: duckdb.DuckDBPyConnection,
    file_path: Path,
    fn_name: str,
    data: dict,
) -> str:
    """Register a single function entry in one transaction.  Returns 'added' or 're-registered'.

    Shared helper used by both .py and .sql scanners.

    ``data`` must contain:
      - function_class (str)
      - function_return_type (str | None)
      - function_doc (str | None)
      - function_signature (str)
      - function_type (str)
      - param_names (list[str])
      - param_types (list[str])

    Implements §10 registration transaction + Principle 2 collapse logic.
    Raises on unexpected error (caller catches and logs skip).
    """
    fn_class = data["function_class"]
    fn_return_type = data["function_return_type"]

    # §2 content_hash_id: uuid5(table_namespace("function_registry"), name|class|return_type)
    chid = content_hash_id("function_registry", fn_name, fn_class, str(fn_return_type))

    # Principle 2: check for existing row with same content_hash_id
    existing = conn.execute(
        "SELECT function_id FROM function_registry WHERE content_hash_id = ?",
        [chid],
    ).fetchone()

    if existing:
        function_id = existing[0]
        status = "re-registered"
    else:
        function_id = new_id()
        status = "added"

    param_names: list[str] = data["param_names"]
    param_types: list[str] = data["param_types"]
    # #258: defaults captured at inspection (empty/absent for the SQL pd.DataFrame path)
    param_has_default: list[bool] = data.get("param_has_default") or [False] * len(param_names)
    param_default_values: list[str | None] = data.get("param_default_values") or [None] * len(param_names)
    # #134: signature-order positions; absent (legacy caller) → list order
    param_positions: list[int] = data.get("param_positions") or list(range(len(param_names)))
    engine: str = data.get("engine") or "python"
    function_body: str | None = data.get("function_body")

    conn.execute("BEGIN")
    try:
        if status == "added":
            conn.execute(
                """
                INSERT INTO function_registry
                    (function_id, content_hash_id, function_class, function_name,
                     function_doc, function_return_type, function_signature,
                     function_type, module_path, is_active, engine, function_body)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
                """,
                [
                    function_id,
                    chid,
                    fn_class,
                    fn_name,
                    data["function_doc"],
                    fn_return_type,
                    data["function_signature"],
                    data["function_type"],
                    str(file_path),
                    engine,
                    function_body,
                ],
            )
        else:
            # Overwrite mutable columns only; preserve function_id (Principle 2)
            conn.execute(
                """
                UPDATE function_registry SET
                    function_doc = ?,
                    function_signature = ?,
                    function_type = ?,
                    module_path = ?,
                    is_active = TRUE,
                    engine = ?,
                    function_body = ?
                WHERE function_id = ?
                """,
                [
                    data["function_doc"],
                    data["function_signature"],
                    data["function_type"],
                    str(file_path),
                    engine,
                    function_body,
                    function_id,
                ],
            )
            # Remove old parameter rows so we can re-insert the current ones
            conn.execute("DELETE FROM parameter WHERE function_id = ?", [function_id])

        # Write parameter rows (one transaction with function_registry row — §10)
        for param_name, param_type, p_has_default, p_default, p_position in zip(
            param_names, param_types, param_has_default, param_default_values, param_positions
        ):
            # §2 exception: the parameter surrogate is DERIVED from
            # (function_id, param_name), not random. A rescan re-registers the
            # function (DELETE + reinsert parameter rows); a random param_id would
            # change every time and orphan every alias_map.parameter_id binding.
            # param names are unique within a function, so this stays unique, and it
            # is stable even when param_type changes (which recomputes content_hash_id).
            param_id = content_hash_id("parameter", "param_id", str(function_id), param_name)
            param_chid = content_hash_id(
                "parameter", param_name, str(function_id), param_type
            )
            conn.execute(
                """
                INSERT INTO parameter
                    (param_id, content_hash_id, param_name, param_type, function_id,
                     has_default, default_value, position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [param_id, param_chid, param_name, param_type, function_id,
                 p_has_default, p_default, p_position],
            )

        conn.execute("COMMIT")
        return status

    except Exception:
        conn.execute("ROLLBACK")
        raise


# Keep the old name as an alias so existing internal callers remain unaffected.
_register_one_function = register_function_entry


def scan_functions(
    conn: duckdb.DuckDBPyConnection,
    functions_paths: list[str],
) -> list[dict]:
    """Scan all directories in functions_paths and register eligible functions.

    Discovers both .py and .sql files.

    Returns a session-only scan log: list of
      {"file": str, "function_name": str, "status": str}
    where status is "added", "re-registered", "skipped: <reason>",
    "flagged: <finding>" (guardrail screen, accepted), or "file_missing".
    """
    log: list[dict] = []

    # Track which files we actually visit so we can detect missing files afterward.
    seen_files: set[str] = set()

    for dir_str in functions_paths:
        dir_path = Path(dir_str)
        if not dir_path.is_dir():
            log.append({
                "file": dir_str,
                "function_name": "<directory>",
                "status": "skipped: path not found or not a directory",
            })
            continue

        # Collect both .py and .sql files, sorted together for determinism
        candidate_files: list[tuple[Path, str]] = []
        for py_file in dir_path.glob("*.py"):
            candidate_files.append((py_file, "py"))
        for sql_file in dir_path.glob("*.sql"):
            candidate_files.append((sql_file, "sql"))
        candidate_files.sort(key=lambda x: x[0].name)

        for src_file, _kind in candidate_files:
            seen_files.add(str(src_file))

            for item in extract_contracts(src_file):
                # Guardrail flags (accepted-but-surfaced) get their own log lines.
                for flag in item.flags:
                    log.append({
                        "file": str(src_file),
                        "function_name": item.function_name,
                        "status": f"flagged: {flag.detail} (line {flag.lineno})",
                    })

                if item.skip_reason is not None:
                    log.append({
                        "file": str(src_file),
                        "function_name": item.function_name,
                        "status": f"skipped: {item.skip_reason}",
                    })
                    continue
                if item.contract is None:
                    continue  # flag-only <module> entry

                try:
                    status = register_function_entry(
                        conn, src_file, item.function_name, item.contract.to_registry_dict()
                    )
                    log.append({
                        "file": str(src_file),
                        "function_name": item.function_name,
                        "status": status,
                    })
                except Exception as exc:
                    log.append({
                        "file": str(src_file),
                        "function_name": item.function_name,
                        "status": f"skipped: registration error: {exc}",
                    })

    # After processing all discovered files, mark functions whose module_path
    # was in one of the scanned directories but no longer exists on disk.
    # The registry row is never deleted — only is_active is set to false.
    scanned_dirs = {str(Path(d).resolve()) for d in functions_paths if Path(d).is_dir()}
    if scanned_dirs:
        active_rows = conn.execute(
            "SELECT function_id, function_name, module_path "
            "FROM function_registry WHERE is_active = TRUE"
        ).fetchall()
        for fn_id, fn_name, module_path in active_rows:
            if module_path is None:
                continue
            mp = Path(module_path)
            # Only consider functions whose module_path lives in one of the
            # scanned directories — skip functions from directories we didn't scan.
            if str(mp.parent.resolve()) not in scanned_dirs:
                continue
            if str(mp) not in seen_files:
                conn.execute(
                    "UPDATE function_registry SET is_active = FALSE WHERE function_id = ?",
                    [fn_id],
                )
                log.append({
                    "file": module_path,
                    "function_name": fn_name,
                    "status": "file_missing",
                })

    return log
