"""
Tests for pipeui.backend.domain.runner.worker — process-isolated function execution.

Guards (CLAUDE_REFERENCE.md §13 behavioral-guarantee pattern):
- unit:  harness only constructs Arrow IPC payloads from scalar/Series/DataFrame
         (never pickle, never connection/path/app objects)
- integration (subprocess):
  - timeout: looping function killed within wall-clock bound; FailedFunctionEntry returned
  - crash:   raising function takes only the worker; app survives; FailedFunctionEntry returned
  - OOM:     memory-allocating function killed by setrlimit; FailedFunctionEntry returned
  - happy:   scalar / Series / DataFrame round-trips through Arrow IPC correctly

Memory notes:
  pandas + pyarrow initialisation requires ~512 MiB virtual address space on this
  platform; happy-path tests therefore use 512 MiB.  The OOM test uses a 600 MiB
  cap and a function that tries to allocate an additional 200 MiB after the runtime
  has used ~512 MiB, which causes a MemoryError inside the worker subprocess while
  the app process itself survives.
"""
from __future__ import annotations

import io
import struct

import pandas as pd
import pyarrow as pa
import pytest

from pipeui.backend.data.base.fails import FailedFunctionEntry
from pipeui.backend.domain.runner.worker import (
    _SENTINEL_FRAME,
    _SENTINEL_SCALAR,
    _SENTINEL_SERIES,
    _SENTINEL_LEN,
    _pack_argument,
    _unpack_argument,
    call_function,
)

# ---------------------------------------------------------------------------
# Memory caps (see module docstring for rationale)
# ---------------------------------------------------------------------------
_RUNTIME_MEM = 512 * 1024 * 1024   # minimum for pandas/pyarrow on this platform
_OOM_CAP_MEM = 600 * 1024 * 1024   # tight cap for OOM test: runtime fits; big alloc fails


# ---------------------------------------------------------------------------
# unit: Arrow IPC serialisation only (no subprocess, no DB connection)
# Guard: "harness only ever passes scalar/Series/DataFrame and never the
#         connection or app objects" (CLAUDE_REFERENCE.md §13)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestArrowIpcSerialisation:
    """Guarantee: data crossing the worker boundary is Arrow IPC — never pickle."""

    def _assert_no_pickle(self, packed: bytes) -> None:
        """Assert packed bytes start with a known sentinel and parse as Arrow IPC."""
        assert packed[:_SENTINEL_LEN] in (
            _SENTINEL_SCALAR,
            _SENTINEL_SERIES,
            _SENTINEL_FRAME,
        ), "packed bytes must start with a known sentinel"
        # The payload must be parseable as Arrow IPC (not pickle)
        length = struct.unpack(">I", packed[_SENTINEL_LEN : _SENTINEL_LEN + 4])[0]
        payload = packed[_SENTINEL_LEN + 4 : _SENTINEL_LEN + 4 + length]
        reader = pa.ipc.open_stream(io.BytesIO(payload))
        reader.read_all()  # must not raise

    def test_scalar_int_roundtrips_as_arrow_ipc(self):
        packed = _pack_argument(42)
        self._assert_no_pickle(packed)
        assert packed[:_SENTINEL_LEN] == _SENTINEL_SCALAR
        result = _unpack_argument(packed)
        assert result == 42

    def test_scalar_str_roundtrips_as_arrow_ipc(self):
        packed = _pack_argument("hello")
        self._assert_no_pickle(packed)
        result = _unpack_argument(packed)
        assert result == "hello"

    def test_scalar_float_roundtrips_as_arrow_ipc(self):
        packed = _pack_argument(3.14)
        self._assert_no_pickle(packed)
        result = _unpack_argument(packed)
        assert abs(result - 3.14) < 1e-9

    def test_scalar_none_roundtrips_as_arrow_ipc(self):
        packed = _pack_argument(None)
        self._assert_no_pickle(packed)
        result = _unpack_argument(packed)
        assert result is None

    def test_series_roundtrips_as_arrow_ipc(self):
        s = pd.Series([1, 2, 3], name="x")
        packed = _pack_argument(s)
        self._assert_no_pickle(packed)
        assert packed[:_SENTINEL_LEN] == _SENTINEL_SERIES
        result = _unpack_argument(packed)
        assert isinstance(result, pd.Series)
        assert list(result) == [1, 2, 3]

    def test_dataframe_roundtrips_as_arrow_ipc(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        packed = _pack_argument(df)
        self._assert_no_pickle(packed)
        assert packed[:_SENTINEL_LEN] == _SENTINEL_FRAME
        result = _unpack_argument(packed)
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["a", "b"]
        assert list(result["a"]) == [1, 2]

    def test_pack_argument_rejects_db_connection_type(self):
        """Guarantee: opaque app objects cannot be packed (Arrow cannot represent them)."""
        class FakeConn:
            pass
        with pytest.raises(Exception):
            _pack_argument(FakeConn())


# ---------------------------------------------------------------------------
# integration: real subprocess tests
# Guard: crash/timeout/OOM each produce FailedFunctionEntry; app survives
# (CLAUDE_REFERENCE.md §13 "Process isolation (§10)" integration bullets)
# ---------------------------------------------------------------------------


_LOOPING_FN = """\
def loop_forever(x):
    while True:
        pass
"""

_CRASHING_FN = """\
def crash_now(x):
    raise RuntimeError("intentional crash")
"""

_OOM_FN = """\
def eat_memory(x):
    # try to allocate 200 MiB in one shot; will fail under a 600 MiB cap
    # because the pandas/pyarrow runtime already uses ~512 MiB
    buf = bytearray(200 * 1024 * 1024)
    return len(buf)
"""

_DOUBLE_FN = """\
def double(x):
    return x * 2
"""

_DOUBLE_SERIES_FN = """\
import pandas as pd
def double_series(s):
    return s * 2
"""

_DOUBLE_DF_FN = """\
import pandas as pd
def double_df(df):
    return df * 2
"""


@pytest.mark.integration
def test_timeout_kills_looping_worker_and_returns_failed_entry():
    """
    Guard: a looping function is killed within the wall-clock timeout.
    CLAUDE_REFERENCE.md §13 — "timeout (looping function killed within bound)".
    Timeout is 2 s; test must complete well within that.
    """
    result = call_function(
        _LOOPING_FN, "loop_forever", "x", 1,
        timeout=2.0, cpu_seconds=10, memory_bytes=_RUNTIME_MEM,
    )
    assert isinstance(result, FailedFunctionEntry), (
        "expected FailedFunctionEntry from a looping worker"
    )
    assert result.has_failures()
    reason = result.failures[0][1]
    assert "timed out" in reason


@pytest.mark.integration
def test_crashing_worker_returns_failed_entry_and_app_survives():
    """
    Guard: a crashing function takes only the worker; app survives;
    FailedFunctionEntry returned with error message.
    CLAUDE_REFERENCE.md §13 — "crash (raising function -> worker dies, app survives)".
    """
    result = call_function(
        _CRASHING_FN, "crash_now", "x", 1,
        timeout=5.0, cpu_seconds=5, memory_bytes=_RUNTIME_MEM,
    )
    assert isinstance(result, FailedFunctionEntry), (
        "expected FailedFunctionEntry from a crashing worker"
    )
    assert result.has_failures()
    reason = result.failures[0][1]
    assert "crashed" in reason
    assert "intentional crash" in reason


@pytest.mark.integration
def test_oom_worker_killed_by_setrlimit_returns_failed_entry():
    """
    Guard: a memory-allocating function is killed by the setrlimit memory cap.
    CLAUDE_REFERENCE.md §13 — "setrlimit memory cap (allocate-big function killed)".

    Uses a 600 MiB AS cap.  The pandas/pyarrow runtime needs ~512 MiB; the function
    then tries to allocate 200 MiB more, exceeding the cap.
    """
    result = call_function(
        _OOM_FN, "eat_memory", "x", 1,
        timeout=5.0, cpu_seconds=5, memory_bytes=_OOM_CAP_MEM,
    )
    assert isinstance(result, FailedFunctionEntry), (
        "expected FailedFunctionEntry when worker hits memory cap"
    )
    assert result.has_failures()


@pytest.mark.integration
def test_scalar_happy_path_round_trips():
    """Scalar in, scalar out — Arrow IPC transport end-to-end."""
    result = call_function(
        _DOUBLE_FN, "double", "x", 21,
        timeout=10.0, cpu_seconds=10, memory_bytes=_RUNTIME_MEM,
    )
    assert result == 42, f"expected 42, got {result!r}"


@pytest.mark.integration
def test_call_function_passes_extra_scalar_kwargs():
    """#258: scalar params are passed by keyword alongside the bound arg, so a
    function like is_above_threshold(value, threshold) actually receives threshold."""
    src = "def above(value: int, threshold: int) -> bool:\n    return value > threshold\n"
    result = call_function(
        src, "above", "value", 10, extra_kwargs={"threshold": 5},
        timeout=10.0, cpu_seconds=10, memory_bytes=_RUNTIME_MEM,
    )
    assert result is True, f"expected True (10 > 5), got {result!r}"


@pytest.mark.integration
def test_call_function_tolerates_subscripted_generic_annotation():
    """#268: a function with a runtime-unevaluable annotation (pd.Series[bool]) and no
    `from __future__ import annotations` must still run — the worker must not evaluate
    annotations, else 'type Series is not subscriptable' crashes the whole module exec."""
    src = (
        "import pandas as pd\n"
        "def chk(col: pd.Series) -> pd.Series[bool]:\n"
        "    return col > 0\n"
    )
    result = call_function(
        src, "chk", "col", pd.Series([1, -2, 3]),
        timeout=10.0, cpu_seconds=10, memory_bytes=_RUNTIME_MEM,
    )
    assert isinstance(result, pd.Series), result
    assert list(result) == [True, False, True]


@pytest.mark.integration
def test_series_happy_path_round_trips():
    """pd.Series in, pd.Series out — Arrow IPC transport end-to-end."""
    s = pd.Series([1, 2, 3])
    result = call_function(
        _DOUBLE_SERIES_FN, "double_series", "s", s,
        timeout=10.0, cpu_seconds=10, memory_bytes=_RUNTIME_MEM,
    )
    assert isinstance(result, pd.Series)
    assert list(result) == [2, 4, 6]


@pytest.mark.integration
def test_dataframe_happy_path_round_trips():
    """pd.DataFrame in, pd.DataFrame out — Arrow IPC transport end-to-end."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    result = call_function(
        _DOUBLE_DF_FN, "double_df", "df", df,
        timeout=10.0, cpu_seconds=10, memory_bytes=_RUNTIME_MEM,
    )
    assert isinstance(result, pd.DataFrame)
    assert list(result["a"]) == [2, 4, 6]


def test_clean_worker_stderr_strips_setrlimit_noise():
    """#270: the macOS setrlimit warning must be stripped so the real error surfaces."""
    from pipeui.backend.domain.runner.worker import _clean_worker_stderr
    stderr = (
        "[worker] setrlimit failed: current limit exceeds maximum limit\n"
        "Traceback (most recent call last):\n"
        '  File "<user_fn>", line 22, in within_range\n'
        "TypeError: Invalid comparison between dtype=str and float\n"
    )
    cleaned = _clean_worker_stderr(stderr)
    assert "setrlimit" not in cleaned
    assert "Traceback" in cleaned
    assert "Invalid comparison between dtype=str and float" in cleaned


def test_clean_worker_stderr_without_noise_unchanged():
    """#270: a failure with no setrlimit line is preserved verbatim (stripped of edges)."""
    from pipeui.backend.domain.runner.worker import _clean_worker_stderr
    s = 'Traceback (most recent call last):\n  ...\nValueError: boom'
    assert _clean_worker_stderr(s) == s
