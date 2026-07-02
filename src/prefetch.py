"""Module-level fire-and-forget prefetch (WI-4.1).

Mirrors TS ``utils/secureStorage/keychainPrefetch.ts`` and
``utils/settings/mdm/rawRead.ts``. The TS reference fires
``security find-generic-password`` and ``plutil`` subprocesses at module
scope so the ~65ms wall-clock cost overlaps with the rest of module
loading; the consumer awaits the result later.

**Why subprocess.Popen, not asyncio.** TS runs all top-level code in an
ambient event loop. Python doesn't — calling an ``async def`` at module
level returns an unawaited coroutine and emits ``RuntimeWarning``.
``subprocess.Popen`` returns immediately (the OS schedules the child
process), giving the same overlap-with-module-loading semantics without
needing an event loop.

The handles returned here are awaited later via ``wait_and_read_*``
helpers when the consumer actually needs the data (e.g., the trust-gate
needs the keychain values).

**Singleton semantics.** ``cli.py`` fires keychain + MDM at module import
time so the wall-clock cost overlaps with argparse / config plumbing.
``setup.run_setup()`` then needs the *same* handles — re-firing would
double the cost and orphan the cli.py-spawned children. ``get_or_start_*``
caches per-process: first call fires the subprocess, subsequent callers
get the same ``PrefetchHandle``. An ``atexit`` hook drains any handle
that was started but never consumed so we don't leave zombies behind.
"""

from __future__ import annotations

import atexit
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PrefetchHandle:
    """Fire-and-forget subprocess handle.

    ``process is None`` means the prefetch was skipped (non-macOS, etc.);
    ``wait_and_read_*`` returns ``None`` for these cases so consumers
    don't need to special-case the platform.

    Backward-compat: legacy consumers (``src/setup.py``) read ``.name``
    and ``.detail`` from the old ``PrefetchResult`` shape. Those are
    provided as derived properties so the existing call sites keep
    working without code changes.
    """

    process: subprocess.Popen | None
    label: str

    @property
    def name(self) -> str:
        """Legacy alias for ``.label``."""
        return self.label

    @property
    def started(self) -> bool:
        """Legacy: True when the prefetch actually fired a child process.

        False on skipped paths (non-macOS, ``security`` unavailable, etc.).
        """
        return self.process is not None

    @property
    def detail(self) -> str:
        """Legacy: short human-readable summary of the prefetch state."""
        if self.process is None:
            return f"skipped ({self.label}: platform/availability)"
        return f"in-flight subprocess pid={self.process.pid}"


# Legacy compatibility — some callers may still construct PrefetchResult
# expecting a "started" bool. Keep the old name as an alias so the
# Phase 4 commit doesn't break unrelated test fixtures.
PrefetchResult = PrefetchHandle


_singleton_lock = threading.Lock()
_singletons: dict[str, PrefetchHandle] = {}


def _register_atexit_drain(handle: PrefetchHandle) -> None:
    """Make sure a Popen child is reaped on interpreter exit.

    If the handle was started but no consumer ever called ``wait_and_read_*``,
    the child becomes a zombie. ``atexit`` drains it so we don't leave
    descriptors / subprocess state hanging.
    """
    proc = handle.process
    if proc is None:
        return

    def _drain() -> None:
        if proc.poll() is None:
            try:
                proc.kill()
            except (OSError, ProcessLookupError):  # pragma: no cover
                return
        try:
            proc.communicate(timeout=0.1)
        except Exception:  # pragma: no cover
            pass

    atexit.register(_drain)


def start_keychain_prefetch() -> PrefetchHandle:
    """Fire macOS keychain reads as a child process; return immediately.

    On non-macOS, returns a sentinel handle with ``process=None`` so the
    consumer can call ``wait_and_read_keychain`` uniformly.

    The Popen call returns in microseconds — the OS schedules the child
    process, which runs in parallel with the rest of the Python interpreter's
    module-loading work. Consumer awaits via ``wait_and_read_keychain``
    when the value is actually needed (typically post-trust-gate).

    ``stderr`` is discarded to ``DEVNULL`` — we don't read it, so leaving
    it on a 64 KB pipe could block the child if ``security`` were verbose
    (failure logs etc.). The keychain stdout payload is a short single
    line; PIPE is safe.
    """
    if sys.platform != "darwin":
        return PrefetchHandle(process=None, label="keychain_prefetch")
    try:
        process = subprocess.Popen(
            ["security", "find-generic-password", "-s", "Anthropic OAuth", "-w"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):
        # ``security`` not on PATH — degrade gracefully; consumer falls
        # back to interactive auth.
        return PrefetchHandle(process=None, label="keychain_prefetch")
    handle = PrefetchHandle(process=process, label="keychain_prefetch")
    _register_atexit_drain(handle)
    return handle


def get_or_start_keychain_prefetch() -> PrefetchHandle:
    """Process-wide singleton: fire once per interpreter, return same handle.

    Resolves the double-fire between call sites: ``cli.py`` fires this in
    ``main()`` once the invocation is known to need the full pipeline
    (ch02 round-4 WI-3 — fast paths like ``--version``/``mcp`` no longer
    spawn it); ``init()`` later awaits the same getter and gets the
    in-flight handle (or starts it, self-healing) instead of spawning a
    second subprocess.
    """
    with _singleton_lock:
        cached = _singletons.get("keychain")
        if cached is not None:
            return cached
        handle = start_keychain_prefetch()
        _singletons["keychain"] = handle
        return handle


def wait_and_read_keychain(
    handle: PrefetchHandle, timeout: float = 5.0
) -> str | None:
    """Block until the prefetch child process exits; return stdout text.

    Returns ``None`` for the skipped-prefetch case (non-macOS or
    ``security`` unavailable) AND for any failure (timeout, non-zero
    exit). Callers fall back to interactive credential resolution.
    """
    if handle.process is None:
        return None
    try:
        stdout, _stderr = handle.process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        handle.process.kill()
        return None
    if handle.process.returncode != 0:
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    return text or None


def start_mdm_raw_read() -> PrefetchHandle:
    """Fire macOS MDM-config read as a child process; return immediately.

    Mirrors TS ``startMdmRawRead`` which spawns ``plutil`` subprocesses
    in parallel for managed-config plists. Non-macOS platforms get a
    sentinel handle. ``stderr`` is discarded — see the keychain note for
    rationale.
    """
    if sys.platform != "darwin":
        return PrefetchHandle(process=None, label="mdm_raw_read")
    # Read the managed-app plist for clawcodex if it exists; ignore
    # errors (the absence of the file is normal for unmanaged users).
    plist_path = "/Library/Managed Preferences/com.anthropic.claude-code.plist"
    try:
        process = subprocess.Popen(
            ["plutil", "-convert", "json", "-o", "-", plist_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):
        return PrefetchHandle(process=None, label="mdm_raw_read")
    handle = PrefetchHandle(process=process, label="mdm_raw_read")
    _register_atexit_drain(handle)
    return handle


def get_or_start_mdm_raw_read() -> PrefetchHandle:
    """Process-wide singleton for ``start_mdm_raw_read``. See keychain getter."""
    with _singleton_lock:
        cached = _singletons.get("mdm")
        if cached is not None:
            return cached
        handle = start_mdm_raw_read()
        _singletons["mdm"] = handle
        return handle


def wait_and_read_mdm(handle: PrefetchHandle, timeout: float = 2.0) -> str | None:
    """Block until the MDM-read child process exits; return stdout JSON or None."""
    if handle.process is None:
        return None
    try:
        stdout, _stderr = handle.process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        handle.process.kill()
        return None
    if handle.process.returncode != 0:
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    return text or None


def start_project_scan(root: Path) -> PrefetchHandle:
    """Stub: project scan would walk the workspace at startup. Not yet wired.

    Returns a sentinel handle so the call site is parity-stable. A future
    WI may wire a real walk if profiler data shows it would help cold-start.
    """
    return PrefetchHandle(process=None, label="project_scan")
