"""Ripgrep subprocess wrapper shared by Grep and Glob tools."""

from __future__ import annotations

import os
import platform
import shutil
import signal as _signal_mod
import subprocess
import sys
import threading as _threading
import time as _time_mod
from typing import Any

from src.utils.abort_controller import AbortSignal


# Poll interval for the abort/timeout watcher. 50 ms keeps ESC perceptibly
# instant (well under the ~100 ms threshold where humans notice latency)
# while costing ~20 wakeups/sec for a long search — negligible. Mirrors
# the same constant in ``bash_tool`` so abort latency is consistent
# across long-running tool surfaces.
_ABORT_POLL_INTERVAL_S = 0.05

# Grace period between SIGTERM and SIGKILL after an abort/timeout. Lets
# ripgrep flush any buffered output before we forcibly tear it down.
_KILL_GRACE_S = 2.0


class RipgrepTimeoutError(Exception):
    def __init__(self, message: str, partial_results: list[str] | None = None):
        super().__init__(message)
        self.partial_results = partial_results or []


class RipgrepAbortedError(Exception):
    """Raised when ``abort_signal`` fired before ripgrep produced a result.

    Distinct from ``RipgrepTimeoutError`` so callers can decide whether
    to surface partial results (timeout: yes, useful) or unwind without
    them (abort: caller already decided to cancel — emitting "partial
    results" would be noise that the agent has to re-read).
    """

    def __init__(self, message: str, partial_results: list[str] | None = None):
        super().__init__(message)
        self.partial_results = partial_results or []


class RipgrepUnavailableError(Exception):
    pass


_MAX_BUFFER = 20_000_000  # 20 MB

_SENTINEL = object()
_rg_path: str | None | object = _SENTINEL


def find_ripgrep() -> str | None:
    global _rg_path
    if _rg_path is not _SENTINEL:
        return _rg_path  # type: ignore[return-value]
    _rg_path = shutil.which("rg")
    return _rg_path


def _get_timeout() -> float:
    env_val = os.environ.get("CLAUDE_CODE_GLOB_TIMEOUT_SECONDS", "")
    try:
        parsed = int(env_val)
        if parsed > 0:
            return float(parsed)
    except ValueError:
        pass
    if "microsoft" in platform.uname().release.lower():
        return 60.0
    return 20.0


def _is_eagain_error(stderr: str) -> bool:
    return "os error 11" in stderr or "Resource temporarily unavailable" in stderr


def _get_install_hint() -> str:
    if sys.platform == "darwin":
        return "macOS: `brew install ripgrep`."
    if sys.platform == "win32":
        return "Windows: `winget install BurntSushi.ripgrep.MSVC` or `choco install ripgrep`."
    return "Linux: use your distro package manager, e.g. `apt install ripgrep`."


def _kill_process_group(pid: int, sig: int) -> None:
    try:
        if sys.platform == "win32":
            os.kill(pid, sig)
        else:
            os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone (race vs. natural exit) or insufficient privileges
        # — fall through to ``proc.wait()`` which will surface the right
        # state.
        pass


def _run_rg_with_abort(
    argv: list[str],
    *,
    timeout_s: float,
    abort_signal: AbortSignal | None,
) -> tuple[int, str, str, bool, bool]:
    """Run ripgrep with abort + timeout supervision.

    Returns ``(returncode, stdout, stderr, aborted, timed_out)``.

    Replaces ``subprocess.run(..., timeout=...)`` so a tripped
    ``abort_signal`` can tear the ripgrep process down within
    ``_ABORT_POLL_INTERVAL_S`` rather than waiting out the entire
    ``timeout_s`` deadline. This mirrors ``bash_tool._run_bash_with_abort``
    — long-running search and long-running shell share the same
    abort-latency contract.
    """

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        # Strict decoding inside the drain threads would convert one
        # non-UTF8 matched line anywhere into silent truncation (the
        # drain dies, the pipe refills, the child stalls to timeout) —
        # rg emits matched content in the file's original bytes.
        "errors": "replace",
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(argv, **popen_kwargs)

    # Drain both pipes CONCURRENTLY with the poll loop (C5 fix): the loop
    # below never read the pipes, so output beyond the OS pipe buffer
    # (~64KB) blocked ripgrep on write and the call sat until the full
    # timeout. `rg --files` on a normal repo was the first caller to trip
    # it; large content searches had the same latent stall.
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def _drain(stream: Any, buf: list[str]) -> None:
        try:
            for chunk in iter(lambda: stream.read(8192), ""):
                buf.append(chunk)
        except Exception:
            pass

    drains = []
    for stream, buf in ((proc.stdout, out_chunks), (proc.stderr, err_chunks)):
        thread = _threading.Thread(target=_drain, args=(stream, buf), daemon=True)
        thread.start()
        drains.append(thread)

    deadline = _time_mod.monotonic() + timeout_s
    aborted = False
    timed_out = False

    while True:
        if proc.poll() is not None:
            break
        if abort_signal is not None and abort_signal.aborted:
            aborted = True
            break
        if _time_mod.monotonic() >= deadline:
            timed_out = True
            break
        _time_mod.sleep(_ABORT_POLL_INTERVAL_S)

    if aborted or timed_out:
        _kill_process_group(proc.pid, _signal_mod.SIGTERM)
        try:
            proc.wait(timeout=_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc.pid, _signal_mod.SIGKILL)
            try:
                proc.wait(timeout=_KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                pass

    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    for thread in drains:
        thread.join(timeout=_KILL_GRACE_S)

    return (
        proc.returncode if proc.returncode is not None else -1,
        "".join(out_chunks),
        "".join(err_chunks),
        aborted,
        timed_out,
    )


def ripgrep(
    args: list[str],
    target: str,
    timeout: float | None = None,
    single_thread: bool = False,
    abort_signal: AbortSignal | None = None,
) -> list[str]:
    """Run ripgrep and return output lines.

    Exit code 0 = matches found, 1 = no matches (both are success).
    Exit code >= 2 = actual error.

    When ``abort_signal`` is provided and trips mid-run, the subprocess
    is torn down within ``_ABORT_POLL_INTERVAL_S`` and a
    :class:`RipgrepAbortedError` is raised so the caller can unwind
    (typically by re-raising ``AbortError`` from the agent loop).
    Without the signal parameter the previous ``subprocess.run`` style
    is preserved, so SDK consumers that pass ``abort_signal=None``
    behave exactly as before.
    """
    rg = find_ripgrep()
    if rg is None:
        raise RipgrepUnavailableError(
            "ripgrep (rg) is required for file search but could not be found on PATH. "
            f"Install ripgrep and confirm `rg --version` works. {_get_install_hint()}"
        )

    thread_args = ["-j", "1"] if single_thread else []
    full_args = [rg, *thread_args, *args, target]
    effective_timeout = timeout if timeout is not None else _get_timeout()

    returncode, stdout, stderr, aborted, timed_out = _run_rg_with_abort(
        full_args, timeout_s=effective_timeout, abort_signal=abort_signal
    )

    if aborted:
        partial = [line for line in stdout.splitlines() if line] if stdout else []
        raise RipgrepAbortedError("ripgrep cancelled by abort signal", partial)

    if timed_out:
        partial = [line for line in stdout.splitlines() if line] if stdout else []
        raise RipgrepTimeoutError(
            f"ripgrep timed out after {effective_timeout}s", partial
        )

    if returncode >= 2:
        stderr_text = stderr.strip()
        if _is_eagain_error(stderr_text) and not single_thread:
            return ripgrep(
                args, target, timeout=timeout, single_thread=True,
                abort_signal=abort_signal,
            )
        raise RuntimeError(f"ripgrep error (exit {returncode}): {stderr_text}")

    if not stdout:
        return []
    return [line for line in stdout.splitlines() if line]
