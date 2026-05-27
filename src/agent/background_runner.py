"""Background agent runner — manages the forked child process that
continues the agent loop after Ctrl+B.

Lifecycle:
  1. Parent: ``launch_background_runner()`` forks a child that runs
     the agent loop headlessly, writing output to the session's
     JSONL transcript.
  2. Child:  ``_run_agent_headless()`` drives the agent loop with
     on_message/write_message callbacks (no TUI, no streaming).
  3. Resume: ``--resume`` attaches a TailFollower to the JSONL file
     for real-time output. When the child finishes, the JSONL stops
     growing and a completion marker is appended.
  4. Cleanup: ``cleanup_background_runner()`` removes the marker
     file after successful resume.

State file: ``~/.clawcodex/sessions/{session_id}/.background-runner.json``
  {
    "pid": 12345,
    "session_id": "abc123",
    "started_at": "2025-01-01T00:00:00",
    "status": "running" | "completed" | "failed"
  }
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _sessions_dir() -> Path:
    return Path.home() / ".clawcodex" / "sessions"


def _runner_marker_path(session_id: str) -> Path:
    return _sessions_dir() / session_id / ".background-runner.json"


def _runner_log_path(session_id: str) -> Path:
    return _sessions_dir() / session_id / "background-runner.log"


# ---------------------------------------------------------------------------
# Marker file I/O
# ---------------------------------------------------------------------------

def _write_runner_marker(session_id: str, pid: int) -> None:
    """Write the .background-runner.json marker (status=running)."""
    marker_path = _runner_marker_path(session_id)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "pid": pid,
        "session_id": session_id,
        "started_at": datetime.now().isoformat(),
        "status": "running",
    }
    try:
        marker_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to write runner marker for session %s", session_id)


def _update_runner_status(
    session_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Update the marker file's status field."""
    marker_path = _runner_marker_path(session_id)
    if not marker_path.exists():
        return
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["status"] = status
    data["updated_at"] = datetime.now().isoformat()
    if error is not None:
        data["error"] = error
    try:
        marker_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to update runner status for session %s", session_id)


# ---------------------------------------------------------------------------
# Public API — status / wait / cleanup
# ---------------------------------------------------------------------------

def get_background_runner_status(session_id: str) -> dict[str, Any] | None:
    """Read ``.background-runner.json`` and check whether the child is alive.

    Returns ``None`` if no marker file exists. Otherwise returns a dict
    with at least ``status`` (``"running"`` | ``"completed"`` | ``"failed"``).
    If the marker says *running* but the PID is no longer alive, the
    status is corrected to ``"failed"``.
    """
    marker_path = _runner_marker_path(session_id)
    if not marker_path.exists():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Stale-runner detection: if marker says "running" but PID is gone,
    # patch the status so callers don't wait forever.
    if data.get("status") == "running" and "pid" in data:
        try:
            pid = int(data["pid"])
            # os.kill with signal 0 checks existence without sending a signal
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError, ValueError):
            data["status"] = "failed"
            data["error"] = "Process no longer alive"
            _update_runner_status(session_id, "failed", error="Process no longer alive")

    return data


def wait_for_background_runner(session_id: str, timeout: float | None = None) -> str:
    """Block until the background runner finishes or *timeout* expires.

    Returns the final status string (``"completed"`` | ``"failed"``).
    """
    deadline = None
    if timeout is not None:
        deadline = time.monotonic() + timeout

    while True:
        info = get_background_runner_status(session_id)
        if info is None or info.get("status") != "running":
            return (info or {}).get("status", "unknown")
        if deadline is not None and time.monotonic() >= deadline:
            return "running"
        time.sleep(0.5)


def cleanup_background_runner(session_id: str) -> None:
    """Remove the marker file after a successful resume."""
    marker_path = _runner_marker_path(session_id)
    try:
        marker_path.unlink(missing_ok=True)
    except Exception:
        logger.debug("Could not remove marker for session %s", session_id, exc_info=True)

    log_path = _runner_log_path(session_id)
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Headless agent loop (runs in the forked child)
# ---------------------------------------------------------------------------

def _run_agent_headless(
    session,
    provider,
    tool_registry,
    tool_context,
    max_turns: int,
) -> None:
    """Child process entry: drive the agent loop, writing output to JSONL."""
    import asyncio

    from src.query.agent_loop_compat import (
        build_effective_system_prompt,
        run_query_as_agent_loop,
    )
    from src.outputStyles import resolve_output_style
    from src.services.session_storage import SessionStorage

    storage = SessionStorage(session_id=session.session_id)

    # Build effective system prompt (same as TUI/headless paths)
    style_prompt = resolve_output_style(
        getattr(tool_context, "output_style_name", None),
        getattr(tool_context, "output_style_dir", None),
    ).prompt
    effective_system_prompt = build_effective_system_prompt(style_prompt, tool_context)

    # on_message: persist every message to the JSONL transcript
    def _on_message(msg: Any) -> None:
        try:
            storage.write_message(msg)
            storage.flush()
        except Exception:
            pass

    # Background mode: auto-approve all permissions.
    # The user's Ctrl+B action is explicit consent for the agent to
    # continue running autonomously.
    try:
        tool_context.permission_context.mode = "bypassPermissions"  # type: ignore[union-attr]
    except Exception:
        pass

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            run_query_as_agent_loop(
                initial_messages=list(session.conversation.messages),
                provider=provider,
                tool_registry=tool_registry,
                tool_context=tool_context,
                system_prompt=effective_system_prompt,
                max_turns=max_turns,
                on_message=_on_message,
            )
        )
        _update_runner_status(session.session_id, "completed")
    except Exception as exc:
        logger.exception("Headless agent loop failed for session %s", session.session_id)
        _update_runner_status(session.session_id, "failed", error=str(exc))
    finally:
        loop.close()
        # Write completion marker so the resume side can detect it
        try:
            storage.write_raw({"role": "system", "content": "__background_complete__"})
            storage.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Launch — the public fork/subprocess entry point
# ---------------------------------------------------------------------------

def launch_background_runner(
    session,
    provider,
    tool_registry,
    tool_context,
    max_turns: int,
) -> int | None:
    """Fork (or spawn) a child process that continues the agent loop.

    Returns the child PID on Unix, or ``None`` if the platform doesn't
    support ``os.fork()`` (Windows graceful degradation).
    """
    # Ensure the JSONL transcript is on disk before forking
    try:
        session.save()
    except Exception:
        pass

    # ---- Check for an existing runner on the same session ----
    existing = get_background_runner_status(session.session_id)
    if existing is not None and existing.get("status") == "running":
        logger.warning(
            "Session %s already has a running background agent (pid %s); "
            "skipping duplicate fork",
            session.session_id,
            existing.get("pid"),
        )
        return existing.get("pid")

    # ---- Unix: os.fork() ----
    if hasattr(os, "fork"):
        return _launch_via_fork(session, provider, tool_registry, tool_context, max_turns)

    # ---- Windows: subprocess.Popen ----
    return _launch_via_subprocess(session, provider, tool_registry, tool_context, max_turns)


def _launch_via_fork(session, provider, tool_registry, tool_context, max_turns: int) -> int:
    """Fork a child that runs the headless agent loop (Unix)."""
    pid = os.fork()
    if pid > 0:
        # Parent: record child info and return immediately
        _write_runner_marker(session.session_id, pid)
        return pid

    # ----- child process -----
    try:
        # Detach from the parent's terminal session
        os.setsid()

        # Close stdin so the child doesn't compete for terminal input
        try:
            sys.stdin.close()
        except Exception:
            pass

        # Redirect stdout/stderr to a log file
        log_path = _runner_log_path(session.session_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
            sys.stdout = log_file
            sys.stderr = log_file
        except Exception:
            pass

        # Reset signal handlers so the child doesn't react to
        # terminal-originated signals meant for the parent
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, signal.SIG_DFL)
            except (OSError, ValueError):
                pass

        _run_agent_headless(session, provider, tool_registry, tool_context, max_turns)
    except Exception:
        logger.exception("Forked child failed for session %s", session.session_id)
        _update_runner_status(session.session_id, "failed", error="Child process crashed")
    finally:
        os._exit(0)

    # unreachable — keeps the type checker happy
    return 0  # type: ignore[unreachable]


def _launch_via_subprocess(
    session, provider, tool_registry, tool_context, max_turns: int,
) -> int | None:
    """Launch headless runner via subprocess (Windows fallback)."""
    import subprocess

    log_path = _runner_log_path(session.session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "src.agent.background_runner",
                "--session-id", session.session_id,
                "--max-turns", str(max_turns),
            ],
            stdout=open(log_path, "a", encoding="utf-8"),  # noqa: SIM115
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        _write_runner_marker(session.session_id, proc.pid)
        return proc.pid
    except Exception:
        logger.exception("Failed to launch background runner via subprocess")
        return None


# ---------------------------------------------------------------------------
# __main__ entry point for Windows subprocess mode
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Headless background agent runner")
    parser.add_argument("--session-id", required=True, help="Session ID to resume")
    parser.add_argument("--max-turns", type=int, default=20, help="Max agent turns")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [bg-runner] %(levelname)s %(message)s",
    )

    # Reconstruct session + provider + tool context from saved state
    from src.agent.session import Session as AgentSession
    from src.services.session_storage import SessionStorage

    restored = AgentSession.resume(args.session_id)
    if restored is None:
        logger.error("Cannot resume session %s", args.session_id)
        sys.exit(1)

    # Re-create provider from session metadata
    storage = SessionStorage(session_id=args.session_id)
    meta = storage.get_metadata()
    provider_name = "anthropic"
    model = ""
    if meta:
        provider_name = getattr(meta, "provider", provider_name) or provider_name
        model = getattr(meta, "model", "") or ""

    try:
        from src.config import get_provider_config
        from src.providers import get_provider_class

        cfg = get_provider_config(provider_name)
        provider_cls = get_provider_class(provider_name)
        provider = provider_cls(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url"),
            model=model,
        )
    except Exception:
        logger.exception("Failed to create provider for background runner")
        _update_runner_status(args.session_id, "failed", error="Provider init failed")
        sys.exit(1)

    from src.tool_system.defaults import build_default_registry
    from src.tool_system.context import ToolContext
    from src.permissions.types import ToolPermissionContext

    tool_registry = build_default_registry(provider=provider)
    tool_context = ToolContext(
        workspace_root=Path.cwd(),
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    _run_agent_headless(restored, provider, tool_registry, tool_context, args.max_turns)
