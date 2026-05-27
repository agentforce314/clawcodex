"""orchestrator server — manage the orchestrator daemon process.

Usage (noun-verb):
  clawcodex orchestrator server status          Show orchestrator daemon status
  clawcodex orchestrator server stop            Stop the orchestrator daemon gracefully
  clawcodex orchestrator server start [--workflow PATH]  Start the orchestrator daemon

All commands are idempotent:
  - status: pure read, always safe
  - stop: stopping an already-stopped daemon succeeds silently
  - start: starting an already-running daemon shows its status and exits 0
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def add_server_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``server`` sub-subcommands (status | stop | start)."""
    server_parser = subparsers.add_parser(
        "server",
        help="Manage the orchestrator daemon process",
        description="Start, stop, or check the status of the orchestrator daemon. "
                    "All commands are idempotent — running them multiple times "
                    "has no ill effect.",
    )
    server_sub = server_parser.add_subparsers(
        dest="server_subcommand",
        required=True,
    )

    # --- server status ---
    status_parser = server_sub.add_parser(
        "status",
        help="Show orchestrator daemon status",
        description="Display whether the orchestrator daemon is running, its PID, "
                    "uptime, workspace root, and project slug. Idempotent (pure read).",
    )
    status_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path (optional auto-detection override)",
    )
    status_parser.add_argument(
        "--workflow",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to WORKFLOW.md (helps resolve workspace when metadata is missing)",
    )

    # --- server stop ---
    stop_parser = server_sub.add_parser(
        "stop",
        help="Stop the orchestrator daemon gracefully",
        description="Send SIGTERM to the orchestrator process and clean up metadata. "
                    "Idempotent: if the daemon is already stopped, exits 0 silently.",
    )
    stop_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path (optional auto-detection override)",
    )
    stop_parser.add_argument(
        "--force",
        action="store_true",
        help="Use SIGKILL instead of SIGTERM (force immediate termination)",
    )
    stop_parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Seconds to wait after SIGTERM before SIGKILL (default: 5.0)",
    )

    # --- server start ---
    start_parser = server_sub.add_parser(
        "start",
        help="Start the orchestrator daemon",
        description="Launch the orchestrator with a workflow file. "
                    "Idempotent: if the daemon is already running, shows status instead.",
    )
    start_parser.add_argument(
        "--workflow",
        type=str,
        required=False,
        metavar="PATH",
        help="Path to WORKFLOW.md file",
    )
    start_parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Show embedded status dashboard",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="LiveView dashboard port",
    )


# ---------------------------------------------------------------------------
# Run dispatch
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate server subcommand."""
    cmd = args.server_subcommand
    if cmd == "status":
        return _run_status(args)
    elif cmd == "stop":
        return _run_stop(args)
    elif cmd == "start":
        return _run_start(args)
    print(f"error: unknown server subcommand '{cmd}'", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

def _find_metadata(args: argparse.Namespace) -> tuple[Path | None, dict | None]:
    """Resolve orchestrator metadata.

    Returns (metadata_path, metadata_dict) or (None, None) if not found.
    """
    from extensions.orchestrator.workspace_locator import (
        _find_latest_metadata,
        get_workspace_root,
    )

    # Priority: explicit --workspace > --workflow > env var > latest metadata
    workspace_root = get_workspace_root(
        workspace_arg=getattr(args, "workspace", None),
        workflow_path=getattr(args, "workflow", None),
    )
    if workspace_root:
        slug = _slug_from_workspace(str(workspace_root))
        metadata_path = Path.home() / ".clawcodex" / "orchestrator" / slug / "metadata.json"
        if metadata_path.exists():
            import json
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                return metadata_path, data
            except Exception:
                pass
        # Fallback: search by workspace_root matching
        metadata_dir = Path.home() / ".clawcodex" / "orchestrator"
        if metadata_dir.exists():
            for md_dir in metadata_dir.iterdir():
                mf = md_dir / "metadata.json"
                if mf.exists():
                    import json
                    try:
                        data = json.loads(mf.read_text(encoding="utf-8"))
                        if data.get("workspace_root") == str(workspace_root):
                            return mf, data
                    except Exception:
                        pass

    # Fallback: latest metadata
    latest = _find_latest_metadata()
    if latest and latest.exists():
        import json
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            return latest, data
        except Exception:
            pass

    return None, None


def _slug_from_workspace(ws_str: str) -> str:
    """Generate a deterministic slug from a workspace path string."""
    parts = [p for p in ws_str.strip().replace("/", "-").replace("\\", "-").split("-")
             if p and p not in ("tmp", ".clawcodex", "~")]
    return "-".join(parts[-3:]) if parts else "default"


def _is_pid_alive(pid: int) -> bool:
    """Check whether a PID is still alive (no-side-effect signal 0 test)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _format_uptime(started_at: float) -> str:
    """Format uptime as human-readable string."""
    elapsed = time.time() - started_at
    if elapsed < 60:
        return f"{int(elapsed)}s"
    elif elapsed < 3600:
        return f"{int(elapsed / 60)}m {int(elapsed % 60)}s"
    else:
        hours = int(elapsed / 3600)
        minutes = int((elapsed % 3600) / 60)
        return f"{hours}h {minutes}m"


# ---------------------------------------------------------------------------
# server status
# ---------------------------------------------------------------------------

def _run_status(args: argparse.Namespace) -> int:
    """Show orchestrator daemon status. Idempotent — pure read."""
    meta_path, meta = _find_metadata(args)

    if meta is None:
        print("Orchestrator daemon: NOT RUNNING")
        print("  No orchestrator metadata found.")
        print("  Hint: Start with 'clawcodex orchestrator server start --workflow WORKFLOW.md'")
        return 0  # idempotent: not-running is a valid status, not an error

    pid = meta.get("pid")
    started_at = meta.get("started_at", 0)
    project_slug = meta.get("project_slug", "unknown")
    workspace_root = meta.get("workspace_root", "unknown")
    workflow_path = meta.get("workflow_path")

    if pid and _is_pid_alive(pid):
        uptime = _format_uptime(started_at) if started_at else "unknown"
        print(f"Orchestrator daemon: RUNNING")
        print(f"  PID            : {pid}")
        print(f"  Uptime         : {uptime}")
        print(f"  Project        : {project_slug}")
        print(f"  Workspace root : {workspace_root}")
        if workflow_path:
            print(f"  Workflow       : {workflow_path}")
        print(f"  Metadata       : {meta_path}")
    else:
        stale_age = _format_uptime(started_at) if started_at else "unknown"
        print(f"Orchestrator daemon: STOPPED (stale metadata from {stale_age} ago)")
        print(f"  Project        : {project_slug}")
        print(f"  Workspace root : {workspace_root}")
        print(f"  Metadata       : {meta_path} (stale — clean up with 'server stop')")
        # Auto-clean stale metadata
        if meta_path and meta_path.exists():
            meta_path.unlink()
            print(f"  -> Stale metadata cleaned up.")

    return 0


# ---------------------------------------------------------------------------
# server stop
# ---------------------------------------------------------------------------

def _run_stop(args: argparse.Namespace) -> int:
    """Stop the orchestrator daemon. Idempotent — already-stopped → exit 0."""
    meta_path, meta = _find_metadata(args)

    if meta is None:
        print("Orchestrator daemon: already stopped (no metadata found)")
        return 0  # idempotent

    pid = meta.get("pid")
    started_at = meta.get("started_at", 0)
    project_slug = meta.get("project_slug", "unknown")
    workspace_root = meta.get("workspace_root", "unknown")

    if pid is None or not _is_pid_alive(pid):
        print(f"Orchestrator daemon: already stopped (PID {pid or 'N/A'} not running)")
        # Clean up stale metadata
        if meta_path and meta_path.exists():
            meta_path.unlink()
            print(f"  Stale metadata cleaned up.")
        return 0  # idempotent

    # Send stop signal
    sig = signal.SIGKILL if args.force else signal.SIGTERM
    sig_name = "SIGKILL" if args.force else "SIGTERM"
    print(f"Stopping orchestrator daemon (PID {pid}, project: {project_slug})...")
    print(f"  Sending {sig_name}...")

    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        print(f"  Process {pid} already exited.")
    except PermissionError:
        print(f"  Permission denied: cannot signal PID {pid}.", file=sys.stderr)
        print(f"  Try running with elevated privileges or kill manually: kill {pid}", file=sys.stderr)
        return 1

    # If not force, wait for graceful shutdown
    if not args.force:
        timeout = args.timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _is_pid_alive(pid):
                break
            time.sleep(0.2)
        else:
            # Timed out — process still alive
            print(f"  Process did not exit within {timeout}s timeout. Use --force for SIGKILL.")
            print(f"  You may also kill manually: kill -9 {pid}")
            return 1

    # Clean up metadata
    if meta_path and meta_path.exists():
        meta_path.unlink()
        print(f"  Metadata cleaned up: {meta_path}")

    print(f"Orchestrator daemon stopped.")
    return 0


# ---------------------------------------------------------------------------
# server start
# ---------------------------------------------------------------------------

def _run_start(args: argparse.Namespace) -> int:
    """Start the orchestrator daemon. Idempotent — already-running → show status."""
    # Check if already running
    _, meta = _find_metadata(args)
    if meta:
        pid = meta.get("pid")
        if pid and _is_pid_alive(pid):
            print(f"Orchestrator daemon is already running (PID {pid}).")
            print("Showing current status:")
            return _run_status(args)

    # Launch the orchestrator directly
    return _run_orchestrator(
        workflow_path=args.workflow,
        dashboard=getattr(args, "dashboard", False),
        port=getattr(args, "port", None),
    )


# ---------------------------------------------------------------------------
# orchestrator launch
# ---------------------------------------------------------------------------


def _run_orchestrator(
    workflow_path: str | None,
    dashboard: bool = False,
    port: int | None = None,
) -> int:
    """Launch the orchestrator with a workflow file.

    This is the core launch entry point. Supports optional embedded
    dashboard status printing.
    """
    import asyncio
    import logging

    from extensions.orchestrator.tracker import TrackerConfigError, validate_tracker_config
    from extensions.orchestrator.workflow import WorkflowLoader, WorkflowParseError

    if not workflow_path:
        print("error: --workflow is required", file=sys.stderr)
        return 2

    try:
        config, _prompt = WorkflowLoader.load(workflow_path)
    except WorkflowParseError as exc:
        print(f"error: failed to parse workflow: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: workflow file not found: {workflow_path}", file=sys.stderr)
        return 2

    try:
        validate_tracker_config(config.tracker)
    except TrackerConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    from extensions.api.orchestration import OrchestrationSubsystem

    subsystem = OrchestrationSubsystem(config)

    async def _run() -> None:
        try:
            await subsystem.run()
        except asyncio.CancelledError:
            await subsystem.shutdown()
            raise

    if dashboard:
        dashboard_task = asyncio.create_task(
            _dashboard_loop(subsystem.status_dashboard, port)
        )
        try:
            asyncio.run(_run())
        finally:
            dashboard_task.cancel()
    else:
        asyncio.run(_run())

    return 0


async def _dashboard_loop(dashboard, port: int | None) -> None:
    """Periodic dashboard status print loop."""
    import time

    while True:
        await asyncio.sleep(5)
        try:
            state = dashboard.state()
            running_ids = list(state.get("running", {}).keys())
            print(
                f"[dashboard] running={len(running_ids)} "
                f"completed={state.get('completed_count', 0)} "
                f"failed={state.get('failed_count', 0)}",
                file=sys.stderr,
            )
        except Exception:
            pass
