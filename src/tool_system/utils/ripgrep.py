"""Ripgrep subprocess wrapper shared by Grep and Glob tools."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys


class RipgrepTimeoutError(Exception):
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


def ripgrep(
    args: list[str],
    target: str,
    timeout: float | None = None,
    single_thread: bool = False,
) -> list[str]:
    """Run ripgrep and return output lines.

    Exit code 0 = matches found, 1 = no matches (both are success).
    Exit code >= 2 = actual error.
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

    try:
        result = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as e:
        partial = []
        if e.stdout:
            stdout_text = e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", errors="replace")
            partial = [line for line in stdout_text.splitlines() if line]
        raise RipgrepTimeoutError(
            f"ripgrep timed out after {effective_timeout}s", partial
        ) from e

    if result.returncode >= 2:
        stderr = result.stderr.strip()
        if _is_eagain_error(stderr) and not single_thread:
            return ripgrep(args, target, timeout=timeout, single_thread=True)
        raise RuntimeError(f"ripgrep error (exit {result.returncode}): {stderr}")

    if not result.stdout:
        return []
    return [line for line in result.stdout.splitlines() if line]
