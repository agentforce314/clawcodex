"""Concurrent-session PID registry.

Port of ``typescript/src/utils/concurrentSessions.ts`` (#284): each
top-level session writes ``~/.clawcodex/sessions/<pid>.json`` so peers
can enumerate live sessions and — the bridge use case — dedup a session
reachable over both UDS and bridge (local wins). The bridge handle
publishes its compat ID here via :func:`update_session_bridge_id`;
cleared on teardown so a stale ID doesn't suppress a legitimately-remote
session after reconnect.

Deliberate subsetting vs TS: the ``BG_SESSIONS`` / ``UDS_INBOX``
feature-gated fields (session names, log paths, messaging sockets,
activity pushes) are omitted — those subsystems aren't ported.
Everything is best-effort and fail-soft: registry problems must never
break a session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PID_FILE_RE = re.compile(r"^\d+\.json$")

_registered = False
_unsubscribe_session_switch = None


def _sessions_dir() -> Path:
    # Resolve through the module attribute at call time so test isolation
    # that re-points GLOBAL_CONFIG_DIR covers this module too.
    from src import config as config_mod

    return Path(config_mod.GLOBAL_CONFIG_DIR) / "sessions"


def _pid_file() -> Path:
    return _sessions_dir() / f"{os.getpid()}.json"


def _get_agent_id() -> str | None:
    """Teammate/subagent marker. This port's teammates run in-process
    today and never reach the session entrypoints, so the skip is a
    forward-compat guard: if process-spawned teammates land, set
    ``CLAUDE_CODE_AGENT_ID`` in their environment (the TS env mechanism
    for process-based teammates) and they'll be excluded here."""
    return os.environ.get("CLAUDE_CODE_AGENT_ID") or None


def register_session() -> bool:
    """Write this session's PID file and register cleanup-on-exit.

    Registers top-level sessions only — teammates/subagents are skipped
    (they'd conflate swarm usage with genuine concurrency). Returns True
    if registered. Errors are logged at debug, never raised.
    """
    global _registered, _unsubscribe_session_switch
    if _get_agent_id() is not None:
        return False
    if _registered:
        return True

    from src.bootstrap.state import (
        get_original_cwd,
        get_session_id,
        on_session_switch,
    )
    from src.utils.graceful_shutdown import register_cleanup

    pid_file = _pid_file()

    def _cleanup() -> None:
        try:
            pid_file.unlink()
        except OSError:
            pass  # ENOENT is fine (already deleted or never written)

    # Registered BEFORE the write (TS ordering): if anything after the
    # write throws, the file is still reaped at exit.
    register_cleanup(_cleanup)

    try:
        directory = _sessions_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # No inner try/except (TS semantics): a directory whose perms
        # can't be tightened to 0700 must not receive the PID file —
        # the registry carries sessionId/cwd/bridgeSessionId.
        os.chmod(directory, 0o700)
        payload = {
            "pid": os.getpid(),
            "sessionId": get_session_id(),
            "cwd": get_original_cwd(),
            "startedAt": int(time.time() * 1000),
            "kind": "interactive",
            "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT"),
        }
        pid_file.write_text(json.dumps(payload), encoding="utf-8")
        # --resume / /resume mutates get_session_id() via the session
        # switch; without this, the PID file's sessionId goes stale.
        _unsubscribe_session_switch = on_session_switch(
            lambda sid: _update_pid_file({"sessionId": sid})
        )
        _registered = True
        # TS sweeps stale files at startup right after registering
        # (main.tsx chains countConcurrentSessions); without it,
        # hard-crashed sessions' PID files would accumulate forever.
        count_concurrent_sessions()
        return True
    except Exception as exc:  # noqa: BLE001 — registry is best-effort
        logger.debug("[concurrent_sessions] register failed: %s", exc)
        return False


def _update_pid_file(patch: dict) -> None:
    """Merge ``patch`` into this session's PID file. Best-effort: silently
    no-op when the file doesn't exist (session not registered) or
    read/write fails."""
    try:
        pid_file = _pid_file()
        data = json.loads(pid_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        data.update(patch)
        pid_file.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — Signal.emit propagates
        # listener exceptions, so this must be literally best-effort
        logger.debug("[concurrent_sessions] update_pid_file failed: %s", exc)


def update_session_name(name: str | None) -> None:
    if not name:
        return
    _update_pid_file({"name": name})


def update_session_bridge_id(bridge_session_id: str | None) -> None:
    """Record this session's Remote Control session ID so peer
    enumeration can dedup: a session reachable over both UDS and bridge
    should only appear once (local wins). Cleared (``None``) on bridge
    teardown so stale IDs don't suppress a legitimately-remote session
    after reconnect."""
    _update_pid_file({"bridgeSessionId": bridge_session_id})


def _is_process_running(pid: int) -> bool:
    # TS isProcessRunning returns false for pid <= 1: signal 0 to pid 0
    # targets our own process group (always "alive"), and init/launchd
    # would otherwise be counted as a live session forever.
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Deliberate divergence from TS (which maps EPERM to "not
        # running"): EPERM proves the PID exists, just owned by someone
        # else — counting it live avoids sweeping a real session's file.
        return True
    except (OSError, OverflowError):
        # OverflowError: a regex-valid but absurd PID exceeds C long.
        return False


def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as fh:
            version = fh.read().lower()
            # TS getPlatform matches either marker; custom WSL2 kernels
            # often carry only "wsl".
            return "microsoft" in version or "wsl" in version
    except OSError:
        return False


def count_concurrent_sessions() -> int:
    """Count live concurrent sessions (including this one), sweeping
    stale PID files from crashed sessions. Returns 0 on any error."""
    directory = _sessions_dir()
    try:
        files = os.listdir(directory)
    except OSError as exc:
        logger.debug("[concurrent_sessions] readdir failed: %s", exc)
        return 0

    count = 0
    for name in files:
        # Strict filename guard: only ``<pid>.json`` is a candidate — a
        # lenient prefix-parse would sweep unrelated files as "stale"
        # (TS issue #34210: silent user data loss).
        if not _PID_FILE_RE.match(name):
            continue
        pid = int(name[:-5])
        if pid == os.getpid():
            count += 1
            continue
        if _is_process_running(pid):
            count += 1
        elif not _is_wsl():
            # Stale file from a crashed session — sweep it. Skip on WSL:
            # a Windows PID isn't probeable from WSL and we'd falsely
            # delete a live session's file (conservative undercount is
            # acceptable; this is telemetry).
            try:
                (directory / name).unlink()
            except OSError:
                pass
    return count


def reset_for_testing() -> None:
    global _registered, _unsubscribe_session_switch
    _registered = False
    if _unsubscribe_session_switch is not None:
        _unsubscribe_session_switch()
        _unsubscribe_session_switch = None
