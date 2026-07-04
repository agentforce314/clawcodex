"""autoFix runner — port of autoFixRunner.ts.

Runs the configured lint command first, then the test command only if lint
passed (or none configured), each as a shell subprocess bounded by a
timeout. On timeout the whole process GROUP is killed (the TS ``detached`` +
``killTree`` analog) so shell-spawned children don't leak. Never raises — a
spawn failure degrades to ``has_errors=False`` (auto-fix is non-critical,
matching the TS runner's resolve-on-error).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AutoFixResult:
    has_errors: bool = False
    lint_output: str | None = None
    lint_exit_code: int | None = None
    test_output: str | None = None
    test_exit_code: int | None = None
    timed_out: bool = False
    error_summary: str | None = None


@dataclass
class _CmdResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


_OUTPUT_CAP = 10000  # TS slices stdout/stderr to 10k before combining.


async def _run_command(
    command: str, cwd: str, timeout_ms: float, abort_signal: Any = None
) -> _CmdResult:
    """Spawn ``command`` via the shell, bounded by ``timeout_ms`` AND a
    mid-flight abort signal. On either, kill the process group (the TS
    ``detached`` + ``killTree`` analog) and reap. Never raises — a spawn
    failure is a non-zero exit."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group so a timeout/abort kills the whole tree (the
            # shell + its children), not just the shell.
            start_new_session=True,
        )
    except Exception:  # noqa: BLE001 — spawn failure is non-critical
        logger.debug("autofix: failed to spawn %r", command, exc_info=True)
        return _CmdResult(stdout="", stderr="spawn failed", exit_code=1, timed_out=False)

    comm = asyncio.ensure_future(proc.communicate())
    timeout_s = timeout_ms / 1000.0

    async def _abort_watch() -> None:
        # Poll the abort signal (TS wires an abort listener → killTree); exit
        # early if the command finishes on its own.
        while not getattr(abort_signal, "aborted", False):
            if comm.done():
                return
            await asyncio.sleep(0.05)

    waiters = {comm}
    watcher = None
    if abort_signal is not None:
        watcher = asyncio.ensure_future(_abort_watch())
        waiters.add(watcher)
    try:
        done, _pending = await asyncio.wait(
            waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if watcher is not None:
            watcher.cancel()

    # A timeout is the wait elapsing with NOTHING done. If the watcher
    # completed (abort fired) while comm did not, that is an abort — not a
    # timeout.
    timed_out = len(done) == 0
    aborted_mid = (
        not timed_out
        and comm not in done
        and abort_signal is not None
        and getattr(abort_signal, "aborted", False)
    )
    if timed_out or aborted_mid:
        _kill_group(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        comm.cancel()
        return _CmdResult(stdout="", stderr="", exit_code=1, timed_out=timed_out)

    stdout_b, stderr_b = await comm
    stdout = stdout_b.decode("utf-8", "replace")[:_OUTPUT_CAP]
    stderr = stderr_b.decode("utf-8", "replace")[:_OUTPUT_CAP]
    return _CmdResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode if proc.returncode is not None else 1,
        timed_out=False,
    )


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.pid is None:
        return
    try:
        # SIGTERM to match TS (graceful group terminate).
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        # Already-exited race (TS catches the same) or no group — fall back.
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def _build_error_summary(result: AutoFixResult) -> str | None:
    """Verbatim autoFixRunner.ts:118-133."""
    if not result.has_errors:
        return None
    parts: list[str] = []
    if result.timed_out:
        parts.append("Command timed out.")
    if result.lint_exit_code is not None and result.lint_exit_code != 0:
        parts.append(
            f"Lint errors (exit code {result.lint_exit_code}):\n"
            f"{result.lint_output or ''}"
        )
    if result.test_exit_code is not None and result.test_exit_code != 0:
        parts.append(
            f"Test failures (exit code {result.test_exit_code}):\n"
            f"{result.test_output or ''}"
        )
    return "\n\n".join(parts)


async def run_auto_fix_check(
    *,
    lint: str | None,
    test: str | None,
    timeout_ms: int,
    cwd: str,
    abort_signal: Any = None,
) -> AutoFixResult:
    """Run lint (then test if lint passed) — port of ``runAutoFixCheck``."""
    if not lint and not test:
        return AutoFixResult(has_errors=False)
    if abort_signal is not None and getattr(abort_signal, "aborted", False):
        return AutoFixResult(has_errors=False)

    result = AutoFixResult(has_errors=False)

    def _is_aborted() -> bool:
        return abort_signal is not None and getattr(abort_signal, "aborted", False)

    if lint:
        lint_r = await _run_command(lint, cwd, timeout_ms, abort_signal)
        # An abort that killed the command mid-flight is NOT a lint failure —
        # degrade to no-errors (no feedback injected), matching TS.
        if _is_aborted():
            return AutoFixResult(has_errors=False)
        result.lint_output = (lint_r.stdout + "\n" + lint_r.stderr).strip()
        result.lint_exit_code = lint_r.exit_code
        if lint_r.timed_out:
            result.has_errors = True
            result.timed_out = True
            result.error_summary = _build_error_summary(result)
            return result
        if lint_r.exit_code != 0:
            result.has_errors = True
            result.error_summary = _build_error_summary(result)
            return result

    # Tests only if lint passed (or no lint configured).
    if test:
        test_r = await _run_command(test, cwd, timeout_ms, abort_signal)
        if _is_aborted():
            return AutoFixResult(has_errors=False)
        result.test_output = (test_r.stdout + "\n" + test_r.stderr).strip()
        result.test_exit_code = test_r.exit_code
        if test_r.timed_out:
            result.has_errors = True
            result.timed_out = True
        elif test_r.exit_code != 0:
            result.has_errors = True

    result.error_summary = _build_error_summary(result)
    return result
