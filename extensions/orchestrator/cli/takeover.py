"""F-49 takeover: socket-aligned stop-agent-and-spawn-REPL flow.

Replaces the legacy control-file path with the Phase 1 Unix
control socket. The agent is paused (not killed) at the next
turn boundary; the operator's REPL then resumes the same
``run_id`` so inputs flow into the existing
``transcript.jsonl``.

    clawcodex orchestrator issue takeover --id ISSUE-1
        │
        ├─ IssueRegistry.get_by_issue_ref(issue_id)
        │   → (run_id, workspace_path)             # authoritative
        │
        ├─ compute socket: {workspace}/.run_control/{run_id}.sock
        │
        ├─ if socket exists:
        │   open socket → send "pause" + "takeover" → close
        │   wait 1.5s for the runner to reach the next turn
        │     boundary and call _flush_turn_transcript() + flush()
        │
        └─ subprocess: python3 -m src.cli --resume <run_id> --workspace <ws>
              │
              └─ ClawCodexExtREPL(resume_session_id=run_id)
                    → Session.resume(run_id)            # directory format
                    → REPL inputs write to the same transcript.jsonl
                      via Session._save_to_session_storage()
                      (session_id == run_id)

Reads only; no orchestrator coupling beyond :class:`IssueRegistry`.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


# Default grace period for the runner to reach the next turn
# boundary and flush the JSONL. Tuned to the runner's per-turn
# tail latency (see ``agent_runner.py:_flush_turn_transcript`` at
# the ``SessionComplete`` branch). The runner's per-turn flush
# is the canonical moment the JSONL becomes consistent.
_DEFAULT_TAKEOVER_QUIET_SECONDS = 1.5


@dataclass
class _TakeoverTarget:
    """Resolved target for the takeover flow."""

    run_id: str
    workspace_path: Path
    issue_id: str  # for header / log lines


def _resolve_target(
    registry_path: Path | None,
    workspace_root: Path | None,
    issue_id: str | None,
    run_id: str | None,
) -> _TakeoverTarget | None:
    """Look up ``(run_id, workspace_path)`` via :class:`IssueRegistry`
    or accept ``--run`` + ``--workspace`` directly.

    Lookup priority (mirrors
    :func:`extensions.orchestrator.cli.attach._resolve_attach_target`):
      1. ``--id <issue_id>`` via
         :meth:`IssueRegistry.get_by_issue_ref`. Returns the
         record's ``run_id`` and ``workspace_path``.
      2. ``--run <run_id>`` + ``--workspace <path>`` (or resolved
         ``workspace_root``). The caller is responsible for the
         workspace because there is no inverse index from
         ``run_id`` → ``workspace_path`` in the registry.
      3. Otherwise ``None`` — caller emits a usage / lookup error.
    """
    from extensions.orchestrator.issue_registry import IssueRegistry

    if issue_id:
        if registry_path is None or not registry_path.exists():
            return None
        try:
            registry = IssueRegistry(registry_path)
        except Exception:
            return None
        record = registry.get_by_issue_ref(issue_id)
        if record is None or record.run_id is None:
            return None
        if record.workspace_path is None:
            return None
        return _TakeoverTarget(
            run_id=record.run_id,
            workspace_path=Path(record.workspace_path),
            issue_id=record.issue_identifier or record.issue_id,
        )

    if run_id and workspace_root is not None:
        # ``--run`` mode: workspace comes from ``--workspace`` or
        # the resolved ``workspace_root``. We don't have the issue
        # identifier here, so use the run_id as a label.
        return _TakeoverTarget(
            run_id=run_id,
            workspace_path=Path(workspace_root),
            issue_id=f"run:{run_id}",
        )

    return None


async def _send_pause_and_takeover(sock_path: Path) -> bool:
    """Open the control socket, send ``pause`` + ``takeover``,
    close. Returns ``True`` on success, ``False`` on connection
    error / socket missing.

    The two commands have different effects at the runner drain
    loop (``agent_runner.py:_drain_control_loop``):
      * ``pause`` sets ``session.paused = True`` and clears
        ``pause_resume_event`` so the runner blocks on
        ``pause_resume_event.wait()`` at the next tool-call
        boundary.
      * ``takeover`` annotates ``session_end_reason`` and
        ``session_end_summary`` for diagnostics.
    Neither cancels the agent task — the runner stays blocked on
    the pause event until ``resume`` is sent.

    Reuses :func:`extensions.orchestrator.cli.attach._send_cmd`
    (the canonical one-shot sender for the Phase 1 protocol)
    rather than duplicating the JSON-line writer.
    """
    from extensions.orchestrator.cli.attach import _send_cmd

    try:
        reader, writer = await asyncio.open_unix_connection(
            str(sock_path),
        )
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    try:
        await _send_cmd(writer, "pause")
        await _send_cmd(writer, "takeover")
    except Exception:
        try:
            writer.close()
        except Exception:
            pass
        return False
    try:
        writer.close()
    except Exception:
        pass
    return True


def _wait_for_quiet_period(seconds: float) -> None:
    """Block briefly so the runner can reach a turn boundary and
    flush the JSONL via :func:`_flush_turn_transcript` +
    :meth:`SessionStorage.flush`.

    This is a coarse synchronization — the runner's
    ``_flush_turn_transcript`` is called at every turn boundary, so
    the JSONL is always internally consistent. The wait exists to
    make the race window between CLI socket-close and REPL read
    shorter in the common case (turn takes < 1.5s).
    """
    time.sleep(seconds)


def _spawn_resume_repl(
    run_id: str, workspace_path: Path,
) -> int:
    """Spawn ``python3 -m src.cli --resume <run_id> --workspace <ws>``
    and block on its exit code. The REPL inherits stdout/stderr so
    the operator sees the same UX as a direct ``--resume`` call.

    Returns the REPL's exit code.
    """
    try:
        return subprocess.call(
            [
                "python3", "-m", "src.cli",
                "--resume", run_id,
                "--workspace", str(workspace_path),
            ],
            cwd=str(workspace_path),
        )
    except FileNotFoundError as exc:
        print(
            f"error: failed to spawn REPL — {exc}. "
            f"Check that `python3 -m src.cli` is on PATH.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"error: REPL spawn failed: {exc}", file=sys.stderr)
        return 1


def _run_takeover(
    registry_path: Path | None,
    workspace_root: Path | None,
    args: argparse.Namespace,
) -> int:
    """Sync wrapper around :func:`_run_takeover_async` for the CLI
    dispatcher.

    The dispatcher at ``extensions.orchestrator/cli/issue.py``
    runs in the top-level (non-async) ``run()`` context, so it
    cannot ``await`` directly. This wrapper calls
    :func:`asyncio.run` on the async core. Tests that need to
    drive the full flow inside an existing event loop can
    ``await _run_takeover_async(...)`` instead — same logic, no
    ``asyncio.run`` conflict.
    """
    return asyncio.run(
        _run_takeover_async(registry_path, workspace_root, args),
    )


async def _run_takeover_async(
    registry_path: Path | None,
    workspace_root: Path | None,
    args: argparse.Namespace,
) -> int:
    """Async core of the takeover flow. See :func:`_run_takeover`
    for the contract; this is the version that callers inside an
    existing event loop (e.g. async tests) should ``await``.
    """
    issue_id = getattr(args, "id", None) or getattr(args, "issue_id", None)
    run_id = getattr(args, "run", None) or getattr(args, "run_id", None)
    workspace_arg = getattr(args, "workspace", None)

    if not issue_id and not run_id:
        print(
            "error: --id <issue_id> or --run <run_id> is required",
            file=sys.stderr,
        )
        return 2

    if run_id and not workspace_root and not workspace_arg:
        print(
            "error: --run requires --workspace "
            "(or a resolved workspace root)",
            file=sys.stderr,
        )
        return 2

    effective_workspace = (
        Path(workspace_arg) if workspace_arg else workspace_root
    )

    target = _resolve_target(
        registry_path, effective_workspace, issue_id, run_id,
    )
    if target is None:
        if issue_id:
            print(
                f"error: no active run found for issue {issue_id!r}. "
                f"Nothing to take over.",
                file=sys.stderr,
            )
        else:
            print(
                f"error: could not resolve target for run {run_id!r}",
                file=sys.stderr,
            )
        return 1

    sock_path = (
        target.workspace_path
        / ".run_control"
        / f"{target.run_id}.sock"
    )

    # Send pause + takeover if the socket is alive. The agent
    # might already have ended (socket cleaned up at
    # ``SessionComplete``) — in that case the operator is taking
    # over a finished session. The takeover is still valid; the
    # ``--resume`` REPL will read whatever the headless agent
    # left on disk.
    if sock_path.exists():
        sent = await _send_pause_and_takeover(sock_path)
        if sent:
            print(
                f"Pausing agent for {target.issue_id} "
                f"(run {target.run_id})…",
                file=sys.stderr,
            )
            _wait_for_quiet_period(_DEFAULT_TAKEOVER_QUIET_SECONDS)
        else:
            print(
                f"warning: could not reach control socket at "
                f"{sock_path}. Agent may have already ended — "
                f"spawning REPL against the on-disk transcript.",
                file=sys.stderr,
            )
    else:
        print(
            f"No active control socket at {sock_path}. "
            f"Agent may have already ended — spawning REPL against "
            f"the on-disk transcript.",
            file=sys.stderr,
        )

    print(
        f"Starting takeover REPL for {target.issue_id} "
        f"(run {target.run_id}) in {target.workspace_path}",
        file=sys.stderr,
    )
    return _spawn_resume_repl(target.run_id, target.workspace_path)
