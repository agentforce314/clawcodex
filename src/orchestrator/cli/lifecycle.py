"""orchestrator lifecycle commands — pause / resume / stop / takeover.

Usage:
  clawcodex orchestrator pause <issue_id>
  clawcodex orchestrator resume <issue_id>
  clawcodex orchestrator stop <issue_id>
  clawcodex orchestrator takeover <issue_id>

Lifecycle control:
  pause     - Pause agent at next tool call boundary (no new tool calls until resume)
  resume    - Resume a paused agent
  stop      - Force-terminate a running agent (marks as failed)
  takeover  - Terminate agent and start a REPL for manual intervention
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
from pathlib import Path


def add_lifecycle_parser(subparsers: argparse._SubParsersAction) -> None:
    lifecycle_parser = subparsers.add_parser(
        "pause",
        help="Pause a running agent at the next tool call boundary",
    )
    lifecycle_parser.add_argument("issue_id", help="Issue ID to pause")
    lifecycle_parser.add_argument(
        "--reason",
        default="",
        help="Reason for pausing (visible to agent)",
    )

    resume_parser = subparsers.add_parser(
        "resume",
        help="Resume a paused agent",
    )
    resume_parser.add_argument("issue_id", help="Issue ID to resume")

    stop_parser = subparsers.add_parser(
        "stop",
        help="Force-terminate a running agent",
    )
    stop_parser.add_argument("issue_id", help="Issue ID to stop")

    takeover_parser = subparsers.add_parser(
        "takeover",
        help="Take over an issue (terminate agent + start REPL)",
    )
    takeover_parser.add_argument("issue_id", help="Issue ID to take over")


def run(args: argparse.Namespace) -> int:
    """Execute lifecycle commands."""
    issue_id = args.issue_id

    if args.subcommand == "pause":
        return _do_pause(issue_id, args)
    elif args.subcommand == "resume":
        return _do_resume(issue_id)
    elif args.subcommand == "stop":
        return _do_stop(issue_id)
    elif args.subcommand == "takeover":
        return _do_takeover(issue_id)
    return 0


def _control_path() -> Path:
    """Path to the orchestrator control directory."""
    base = Path(os.environ.get("CLAWCODEX_WORKSPACE_ROOT", Path.home() / ".clawcodex"))
    return base / ".orchestrator_control"


def _write_control(cmd: str, issue_id: str, extra: str = "") -> int:
    """Write a control command to be picked up by the orchestrator."""
    control_dir = _control_path()
    control_dir.mkdir(parents=True, exist_ok=True)

    # Use issue_id as part of the filename for atomicity
    control_file = control_dir / f"{cmd}_{issue_id}.control"
    payload = f"{cmd}\n{issue_id}\n{extra}\n"
    try:
        control_file.write_text(payload, encoding="utf-8")
        print(f"Control command '{cmd}' sent for issue {issue_id}")
        print(f"  The orchestrator will pick this up on its next poll cycle.")
        return 0
    except Exception as exc:
        print(f"Failed to send '{cmd}' for issue {issue_id}: {exc}", file=sys.stderr)
        return 1


def _do_pause(issue_id: str, args: argparse.Namespace) -> int:
    reason = args.reason or "operator requested pause"
    return _write_control("pause", issue_id, reason)


def _do_resume(issue_id: str) -> int:
    return _write_control("resume", issue_id)


def _do_stop(issue_id: str) -> int:
    return _write_control("stop", issue_id)


def _do_takeover(issue_id: str) -> int:
    # Takeover is more involved — it terminates the agent and starts a REPL.
    # For now, send the stop signal and inform the operator.
    print(
        f"Takeover for issue {issue_id}: The agent will be terminated and a REPL "
        f"will be started in the workspace.\n"
        f"Warning: This feature requires LiveView to be enabled (--port).\n",
        file=sys.stderr,
    )
    return _write_control("takeover", issue_id)