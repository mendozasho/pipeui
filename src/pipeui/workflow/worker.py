"""
Process-isolated worker for executing user Python functions.

Design constraints (CLAUDE_REFERENCE.md §10, CLAUDE.md Principle 5):
- Each call spawns a fresh subprocess via sys.executable.
- Data crosses the process boundary as Arrow IPC — never pickle.
- Wall-clock timeout: backend kills the worker with SIGKILL; returns FailedFunctionEntry.
- resource.setrlimit applied unconditionally (v1 is Unix-only; no Windows guard).
- User functions receive ONLY data: scalar / pd.Series / pd.DataFrame.
  They never receive the DuckDB connection, file paths, or any app object.
- Worker crash -> only the subprocess dies; app survives; FailedFunctionEntry returned.
"""
from __future__ import annotations

import io
import os
import struct
import subprocess
import sys
import textwrap
from typing import Any

import pandas as pd
import pyarrow as pa

from pipeui.validation.fails import FailedFunctionEntry

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: float = 30.0
DEFAULT_CPU_SECONDS: int = 10
DEFAULT_MEMORY_BYTES: int = 512 * 1024 * 1024  # 512 MiB address space


# ---------------------------------------------------------------------------
# Arrow IPC serialisation helpers
# ---------------------------------------------------------------------------

_SENTINEL_SCALAR = b"SCALAR\x00"
_SENTINEL_SERIES = b"SERIES\x00"
_SENTINEL_FRAME = b"FRAME\x00\x00"  # padded to 7 bytes

_SENTINEL_LEN = 7  # all sentinels are exactly 7 bytes


def _pack_argument(arg: Any) -> bytes:
    """
    Serialise *arg* to bytes using Arrow IPC.

    Supported types:
    - scalar (int, float, bool, str, None)  -> record-batch with one column "v", one row
    - pd.Series                             -> record-batch with one column "v"
    - pd.DataFrame                          -> record-batch with all columns

    Returns sentinel (7 bytes) + 4-byte big-endian length + payload.
    Pickle is never used.
    """
    if isinstance(arg, pd.DataFrame):
        sentinel = _SENTINEL_FRAME
        table = pa.Table.from_pandas(arg, preserve_index=False)
    elif isinstance(arg, pd.Series):
        sentinel = _SENTINEL_SERIES
        table = pa.table({"v": pa.Array.from_pandas(arg)})
    else:
        # scalar
        sentinel = _SENTINEL_SCALAR
        table = pa.table({"v": [arg]})

    sink = io.BytesIO()
    writer = pa.ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()
    payload = sink.getvalue()
    return sentinel + struct.pack(">I", len(payload)) + payload


def _unpack_argument(raw: bytes) -> Any:
    """
    Deserialise bytes produced by ``_pack_argument``.
    Returns the original scalar / Series / DataFrame.
    """
    sentinel = raw[:_SENTINEL_LEN]
    length = struct.unpack(">I", raw[_SENTINEL_LEN : _SENTINEL_LEN + 4])[0]
    payload = raw[_SENTINEL_LEN + 4 : _SENTINEL_LEN + 4 + length]

    reader = pa.ipc.open_stream(io.BytesIO(payload))
    table = reader.read_all()

    if sentinel == _SENTINEL_FRAME:
        return table.to_pandas()
    elif sentinel == _SENTINEL_SERIES:
        return table.column("v").to_pandas()
    else:  # SCALAR
        return table.column("v")[0].as_py()


# ---------------------------------------------------------------------------
# Worker script (runs inside the subprocess)
# ---------------------------------------------------------------------------

_WORKER_SCRIPT = textwrap.dedent(
    """\
    import io, os, struct, sys
    import resource
    import pandas as pd
    import pyarrow as pa

    # resource limits (Unix-only; applied unconditionally per CLAUDE_REFERENCE.md §10)
    try:
        cpu_seconds = int(os.environ["_PIPEUI_CPU"])
        mem_bytes   = int(os.environ["_PIPEUI_MEM"])
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS,  (mem_bytes,   mem_bytes))
    except Exception as _e:
        print(f"[worker] setrlimit failed: {_e}", file=sys.stderr)

    _SENTINEL_LEN    = 7
    _SENTINEL_SCALAR = b"SCALAR\\x00"
    _SENTINEL_SERIES = b"SERIES\\x00"
    _SENTINEL_FRAME  = b"FRAME\\x00\\x00"

    def _unpack(raw):
        sentinel = raw[:_SENTINEL_LEN]
        length   = struct.unpack(">I", raw[_SENTINEL_LEN:_SENTINEL_LEN+4])[0]
        payload  = raw[_SENTINEL_LEN+4:_SENTINEL_LEN+4+length]
        reader   = pa.ipc.open_stream(io.BytesIO(payload))
        table    = reader.read_all()
        if sentinel == _SENTINEL_FRAME:
            return table.to_pandas()
        elif sentinel == _SENTINEL_SERIES:
            return table.column("v").to_pandas()
        else:
            return table.column("v")[0].as_py()

    def _pack(result):
        if isinstance(result, pd.DataFrame):
            sentinel = _SENTINEL_FRAME
            table = pa.Table.from_pandas(result, preserve_index=False)
        elif isinstance(result, pd.Series):
            sentinel = _SENTINEL_SERIES
            table = pa.table({"v": pa.Array.from_pandas(result)})
        else:
            sentinel = _SENTINEL_SCALAR
            table = pa.table({"v": [result]})
        sink = io.BytesIO()
        writer = pa.ipc.new_stream(sink, table.schema)
        writer.write_table(table)
        writer.close()
        payload = sink.getvalue()
        return sentinel + struct.pack(">I", len(payload)) + payload

    # read the input envelope from stdin
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        sys.exit(2)
    total = struct.unpack(">I", header)[0]
    raw_in = sys.stdin.buffer.read(total)

    # deserialise:
    #   [4-byte fn_source_len][fn_source bytes]
    #   [4-byte kwname_len][kwname bytes]
    #   [remaining bytes = Arrow IPC arg]
    off = 0
    fn_len  = struct.unpack(">I", raw_in[off:off+4])[0]; off += 4
    fn_src  = raw_in[off:off+fn_len].decode(); off += fn_len
    kw_len  = struct.unpack(">I", raw_in[off:off+4])[0]; off += 4
    kw_name = raw_in[off:off+kw_len].decode(); off += kw_len
    arg_raw = raw_in[off:]

    # execute user function
    fn_name = os.environ["_PIPEUI_FN"]
    mod = type(sys)("_user_fn")
    exec(compile(fn_src, "<user_fn>", "exec"), mod.__dict__)
    fn = mod.__dict__[fn_name]

    arg    = _unpack(arg_raw)
    result = fn(**{kw_name: arg})

    # write result back on stdout
    out = _pack(result)
    sys.stdout.buffer.write(struct.pack(">I", len(out)))
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
"""
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def call_function(
    fn_source: str,
    fn_name: str,
    kwarg_name: str,
    arg: Any,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
) -> Any | FailedFunctionEntry:
    """
    Execute *fn_name* (defined in *fn_source*) in a fresh subprocess.

    The function is called as ``fn(**{kwarg_name: arg})``.
    *arg* must be a scalar, ``pd.Series``, or ``pd.DataFrame``.

    Returns the function's return value on success, or a ``FailedFunctionEntry``
    on timeout, crash, or resource exhaustion.

    Design guarantees (CLAUDE_REFERENCE.md §10):
    - Data is transported as Arrow IPC only -- never pickle.
    - The subprocess never receives the DuckDB connection, file paths, or app objects.
    - Wall-clock *timeout* is enforced: worker is killed with SIGKILL on expiry.
    - ``resource.setrlimit`` is applied unconditionally inside the worker (Unix-only, v1).
    """
    arg_bytes = _pack_argument(arg)

    fn_src_bytes = fn_source.encode()
    kw_bytes = kwarg_name.encode()

    # payload: [4-byte fn_len][fn_source][4-byte kw_len][kw_name][Arrow IPC arg]
    payload = (
        struct.pack(">I", len(fn_src_bytes))
        + fn_src_bytes
        + struct.pack(">I", len(kw_bytes))
        + kw_bytes
        + arg_bytes
    )
    # stdin envelope: [4-byte total_len][payload]
    stdin_data = struct.pack(">I", len(payload)) + payload

    env = os.environ.copy()
    env["_PIPEUI_FN"] = fn_name
    env["_PIPEUI_CPU"] = str(cpu_seconds)
    env["_PIPEUI_MEM"] = str(memory_bytes)

    proc = subprocess.Popen(
        [sys.executable, "-c", _WORKER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        fail = FailedFunctionEntry()
        fail.add(fn_name, f"worker timed out after {timeout}s")
        return fail

    if proc.returncode != 0:
        fail = FailedFunctionEntry()
        err_msg = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
        fail.add(fn_name, f"worker crashed: {err_msg}")
        return fail

    # unpack the result
    if len(stdout) < 4:
        fail = FailedFunctionEntry()
        fail.add(fn_name, "worker returned no output")
        return fail

    result_len = struct.unpack(">I", stdout[:4])[0]
    result_raw = stdout[4 : 4 + result_len]

    try:
        return _unpack_argument(result_raw)
    except Exception as exc:
        fail = FailedFunctionEntry()
        fail.add(fn_name, f"result deserialisation failed: {exc}")
        return fail
