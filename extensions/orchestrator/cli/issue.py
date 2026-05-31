"""orchestrator issue — manage individual issues handled by the orchestrator.

Usage (noun-verb, all using self-describing ``--id`` parameters):

  # Query
  clawcodex orchestrator issue list [--status <filter>]
  clawcodex orchestrator issue show --id <id>
  clawcodex orchestrator issue tail --id <id>

  # Lifecycle
  clawcodex orchestrator issue stop --id <id>
  clawcodex orchestrator issue pause --id <id> [--reason <text>]
  clawcodex orchestrator issue resume --id <id>
  clawcodex orchestrator issue takeover --id <id>

  # Operator interaction
  clawcodex orchestrator issue clarify --id <id> --answer <text> [--forward-to-author]
  clawcodex orchestrator issue inject --id <id> <hint> [--list] [--remove N]

  # Workspace
  clawcodex orchestrator issue workspace --id <id> [--ls] [--cat FILE] [--edit FILE --with CONTENT]

Design principles:
  - Self-describing parameters: use ``--id <id>`` instead of positional ``issue_id``
  - All commands are idempotent where possible
  - Stable behaviour: same args produce same outcome (or equivalent no-op)
"""

from __future__ import annotations

import argparse
import sys


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def add_issue_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``issue`` sub-subcommands."""
    issue_parser = subparsers.add_parser(
        "issue",
        help="Manage individual issues handled by the orchestrator",
        description="List, show, tail, stop, pause, resume, takeover, clarify, "
                    "inject, or view workspace of issues managed by the orchestrator. "
                    "All issue-level commands use --id for self-describing parameters "
                    "and are designed to be idempotent.",
    )
    issue_sub = issue_parser.add_subparsers(
        dest="issue_subcommand",
        required=True,
    )

    # --- issue list ---
    list_parser = issue_sub.add_parser(
        "list",
        help="List all issues with their status",
        description="Display all issues known to the orchestrator, optionally "
                    "filtered by status. Idempotent (pure read).",
    )
    list_parser.add_argument(
        "--status",
        choices=["pending", "running", "synced", "completed", "failed", "abandoned"],
        help="Filter by issue status",
    )
    list_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path (optional auto-detection override)",
    )
    list_parser.add_argument(
        "--workflow",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to WORKFLOW.md (resolution hint when metadata is missing)",
    )

    # --- issue show ---
    show_parser = issue_sub.add_parser(
        "show",
        help="Show details for a specific issue",
        description="Display issue metadata: status, branch, PR, token usage, "
                    "and workspace path. Idempotent (pure read).",
    )
    show_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier (e.g. 42 or owner/repo#42)",
    )
    show_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path",
    )
    show_parser.add_argument(
        "--workflow",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to WORKFLOW.md (resolution hint when metadata is missing)",
    )

    # --- issue tail ---
    tail_parser = issue_sub.add_parser(
        "tail",
        help="Tail tool call logs for a running issue in real-time",
        description="Stream tool call events from a running issue's event log. "
                    "Idempotent (pure read, non-destructive).",
    )
    tail_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to tail",
    )
    tail_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path",
    )

    # --- issue stop ---
    stop_parser = issue_sub.add_parser(
        "stop",
        help="Force-terminate a running agent for an issue",
        description="Write a stop control command for the orchestrator to pick up "
                    "on its next poll cycle. The agent will be marked as failed. "
                    "Idempotent: stopping an already-stopped issue succeeds silently.",
    )
    stop_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to stop",
    )

    # --- issue pause ---
    pause_parser = issue_sub.add_parser(
        "pause",
        help="Pause a running agent at the next tool call boundary",
        description="Write a pause control command. The agent will complete its "
                    "current tool call then pause (no new tool calls until resume). "
                    "Idempotent: pausing an already-paused issue succeeds silently.",
    )
    pause_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to pause",
    )
    pause_parser.add_argument(
        "--reason",
        type=str,
        default="",
        help="Reason for pausing (visible to the agent)",
    )

    # --- issue resume ---
    resume_parser = issue_sub.add_parser(
        "resume",
        help="Resume a paused agent",
        description="Write a resume control command to allow the agent to continue. "
                    "Idempotent: resuming a running (non-paused) issue succeeds silently.",
    )
    resume_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to resume",
    )

    # --- issue takeover ---
    takeover_parser = issue_sub.add_parser(
        "takeover",
        help="Take over an issue manually (terminate agent + start REPL)",
        description="Stop the running agent and start an interactive clawcodex REPL "
                    "in the issue's workspace directory for manual intervention. "
                    "Idempotent: if the issue has no running agent, shows a warning.",
    )
    takeover_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to take over",
    )

    # --- issue clarify ---
    clarify_parser = issue_sub.add_parser(
        "clarify",
        help="Answer a clarification request from the orchestrator",
        description="Record an operator answer for a pending clarification. "
                    "The orchestrator picks up the answer on its next poll cycle. "
                    "Idempotent: answering an already-answered clarification "
                    "updates the answer in place.",
    )
    clarify_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue ID being clarified",
    )
    clarify_parser.add_argument(
        "--answer",
        type=str,
        default=None,
        help="Operator's answer to the clarification question",
    )
    clarify_parser.add_argument(
        "--forward-to-author",
        action="store_true",
        help="Skip local answer, forward directly to author (@mention)",
    )

    # --- issue inject ---
    inject_parser = issue_sub.add_parser(
        "inject",
        help="Inject operator hints into a running agent",
        description="Write a hint to .operator_hints.md in the issue workspace. "
                    "The agent reads and displays these hints at each tool call boundary. "
                    "Idempotent: re-injecting the same hint is a no-op.",
    )
    inject_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to inject hint for",
    )
    inject_parser.add_argument(
        "hint",
        nargs="?",
        default=None,
        help="Hint text to inject (omit to just list existing hints)",
    )
    inject_parser.add_argument(
        "--list",
        dest="list_hints",
        action="store_true",
        help="List existing hints for this issue",
    )
    inject_parser.add_argument(
        "--remove",
        dest="remove_hint",
        type=int,
        metavar="N",
        help="Remove hint number N",
    )

    # --- issue workspace ---
    ws_parser = issue_sub.add_parser(
        "workspace",
        help="View and modify files in an issue's workspace",
        description="List, view, or edit files in an issue's workspace directory. "
                    "Use with caution — concurrent edits may conflict with agent changes. "
                    "Idempotent: listing and viewing are pure reads; editing overwrites.",
    )
    ws_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier whose workspace to operate on",
    )
    ws_parser.add_argument(
        "--ls",
        action="store_true",
        help="List files in the workspace",
    )
    ws_parser.add_argument(
        "--cat",
        metavar="FILE",
        help="Show contents of a file in the workspace",
    )
    ws_parser.add_argument(
        "--edit",
        metavar="FILE",
        help="Edit a file (requires --with)",
    )
    ws_parser.add_argument(
        "--with",
        dest="content",
        metavar="CONTENT",
        help="New file content (for use with --edit)",
    )

    # --- issue review ---
    review_parser = issue_sub.add_parser(
        "review",
        help="Approve or reject a completed issue's changes (LocalTracker)",
        description="Review a LocalTracker issue after agent completes git commit. "
                    "Approve to mark as completed, or reject to inject feedback and retry.",
    )
    review_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to review",
    )
    review_parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve the changes — mark issue as completed",
    )
    review_parser.add_argument(
        "--reject",
        action="store_true",
        help="Reject the changes — inject feedback and retry",
    )
    review_parser.add_argument(
        "--feedback",
        type=str,
        default=None,
        metavar="TEXT",
        help="Feedback for rejection (required with --reject)",
    )
    review_parser.add_argument(
        "--comment",
        type=str,
        default=None,
        metavar="TEXT",
        help="Optional comment for approval",
    )


# ---------------------------------------------------------------------------
# Run dispatch
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate issue subcommand."""
    cmd = args.issue_subcommand

    # Resolve workspace/registry helpers
    from extensions.orchestrator.workspace_locator import (
        get_registry_path,
        get_workspace_root,
    )
    ws = get_workspace_root(
        workspace_arg=getattr(args, "workspace", None),
        workflow_path=getattr(args, "workflow", None),
    )
    registry_path = get_registry_path(
        workspace_arg=getattr(args, "workspace", None),
        workflow_path=getattr(args, "workflow", None),
    )

    if cmd == "list":
        return _run_list(registry_path, args)
    elif cmd == "show":
        return _run_show(registry_path, args)
    elif cmd == "tail":
        return _run_tail(registry_path, args)
    elif cmd == "stop":
        return _run_stop(args)
    elif cmd == "pause":
        return _run_pause(args)
    elif cmd == "resume":
        return _run_resume(args)
    elif cmd == "takeover":
        return _run_takeover(args)
    elif cmd == "clarify":
        return _run_clarify(args)
    elif cmd == "inject":
        return _run_inject(args)
    elif cmd == "workspace":
        return _run_workspace(args)
    elif cmd == "review":
        return _run_review(registry_path, args)

    print(f"error: unknown issue subcommand '{cmd}'", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _control_path() -> Path:
    """Path to the orchestrator control directory."""
    from pathlib import Path
    import os
    base = Path(os.environ.get("CLAWCODEX_WORKSPACE_ROOT", Path.home() / ".clawcodex"))
    return base / ".orchestrator_control"


def _write_control(cmd: str, issue_id: str, extra: str = "") -> int:
    """Write a control command to be picked up by the orchestrator on next poll."""
    from pathlib import Path
    control_dir = _control_path()
    control_dir.mkdir(parents=True, exist_ok=True)

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


# ---------------------------------------------------------------------------
# issue list
# ---------------------------------------------------------------------------

def _run_list(registry_path: Path | None, args: argparse.Namespace) -> int:
    """List all issues with status. Idempotent — pure read."""
    if not registry_path or not registry_path.exists():
        ws = getattr(args, "workspace", None)
        wf = getattr(args, "workflow", None)
        from extensions.orchestrator.workspace_locator import (
            get_workspace_root,
            list_orchestrator_projects,
        )
        workspace_root = get_workspace_root(workspace_arg=ws, workflow_path=wf)
        projects = list_orchestrator_projects()

        if workspace_root and projects:
            p = projects[0]
            pid = p.get("pid", "?")
            print(f"Orchestrator is running (PID {pid}, {p.get('project_slug', '?')})")
            print(f"Workspace: {workspace_root}")
            print("No issues processed yet.")
        else:
            print("No orchestrator registry found. No issues to list.")
            print("Hint: Start with 'clawcodex orchestrator server start --workflow WORKFLOW.md'")
        return 0  # idempotent: no-issues is a valid state

    from extensions.orchestrator.issue_registry import IssueRegistry

    registry = IssueRegistry(registry_path)
    counts: dict[str, int] = {"PENDING": 0, "RUNNING": 0, "SYNCED": 0, "COMPLETED": 0, "FAILED": 0, "ABANDONED": 0}
    records = list(registry._records.values())

    # Filter by status
    status_filter = getattr(args, "status", None)
    if status_filter:
        records = [r for r in records if _get_status_str(r.status) == status_filter]

    if not records:
        print("No issues found.")
        if status_filter:
            print(f"  (filtered by status: {status_filter})")
        return 0

    # Print summary
    for r in records:
        s = _get_status_str(r.status)
        counts[s.upper()] = counts.get(s.upper(), 0) + 1

    print(f"Issues ({len(records)} total)")
    print(f"  {'STATUS':<15} {'ISSUE ID':<20} {'BRANCH':<30}")
    print(f"  {'-'*15} {'-'*20} {'-'*30}")
    for r in records:
        s = _get_status_str(r.status)
        branch = r.branch_name or "-"
        print(f"  {s:<15} {r.issue_id:<20} {branch:<30}")

    print()
    print(f"  PENDING  : {counts.get('PENDING', 0)}")
    print(f"  RUNNING  : {counts.get('RUNNING', 0)}")
    print(f"  SYNCED   : {counts.get('SYNCED', 0)}")
    print(f"  COMPLETED: {counts.get('COMPLETED', 0)}")
    print(f"  FAILED   : {counts.get('FAILED', 0)}")
    print(f"  ABANDONED: {counts.get('ABANDONED', 0)}")
    return 0


# ---------------------------------------------------------------------------
# issue show
# ---------------------------------------------------------------------------

def _run_show(registry_path: Path | None, args: argparse.Namespace) -> int:
    """Show details for a specific issue. Idempotent — pure read."""
    issue_id = getattr(args, "id", None) or getattr(args, "issue_id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    if not registry_path or not registry_path.exists():
        print(f"No registry found. Cannot show issue {issue_id}.", file=sys.stderr)
        return 1

    from extensions.orchestrator.issue_registry import IssueRegistry
    registry = IssueRegistry(registry_path)
    record = registry.get(issue_id)
    if record is None:
        print(f"Issue {issue_id} not found in registry.", file=sys.stderr)
        return 1

    import time
    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created_at))
    updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.updated_at))

    print(f"Issue: {record.issue_id}")
    print(f"  Identifier     : {record.issue_identifier}")
    print(f"  Status         : {record.status.value}")
    print(f"  Branch         : {record.branch_name or '-'}")
    print(f"  Base Branch    : {record.base_branch or 'main'}")
    print(f"  Commit SHA     : {record.commit_sha or '-'}")
    print(f"  PR Number      : {record.pr_number or '-'}")
    print(f"  PR URL         : {record.pr_url or '-'}")
    print(f"  Attempts       : {record.attempt_count}")
    print(f"  Created        : {created}")
    print(f"  Updated        : {updated}")
    if record.clarification_status:
        print(f"  Clarification  : {record.clarification_status}")
    return 0


# ---------------------------------------------------------------------------
# issue tail
# ---------------------------------------------------------------------------

def _run_tail(registry_path: Path | None, args: argparse.Namespace) -> int:
    """Tail tool call logs for a running issue. Idempotent — pure read."""
    issue_id = getattr(args, "id", None) or getattr(args, "issue_id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    import time
    import json
    from pathlib import Path

    # Find the issue workspace
    from extensions.orchestrator.workspace_locator import get_workspace_root
    ws = get_workspace_root(
        workspace_arg=getattr(args, "workspace", None),
        workflow_path=None,
    )
    if ws is None:
        print("Cannot resolve workspace root.", file=sys.stderr)
        return 1

    event_log_dir = ws / ".event_logs"
    log_file = event_log_dir / f"{issue_id}.ndjson"

    if not log_file.exists():
        print(f"No event log found for issue {issue_id}.", file=sys.stderr)
        return 1

    print(f"Tailing events for issue {issue_id} (Ctrl+C to stop)...")
    try:
        last_size = log_file.stat().st_size
        while True:
            current_size = log_file.stat().st_size
            if current_size > last_size:
                with open(log_file, "r", encoding="utf-8") as f:
                    f.seek(last_size)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            ts = event.get("timestamp", "")[-8:] if event.get("timestamp") else ""
                            etype = event.get("type", "?")
                            if etype == "tool_call":
                                tool_name = event.get("tool_name", "?")
                                print(f"  [{ts}] CALL  {tool_name}")
                            elif etype == "tool_result":
                                err = " [ERR]" if event.get("is_error") else ""
                                print(f"  [{ts}] RESULT{err} {event.get('tool_name', '?')}")
                            elif etype == "text_delta":
                                content = event.get("content", "")
                                if content:
                                    text = content[:80].replace("\n", " ")
                                    print(f"  [{ts}] TEXT  {text}")
                            else:
                                print(f"  [{ts}] {etype}")
                        except json.JSONDecodeError:
                            pass
                last_size = current_size
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[tail] stopped")
    except Exception as exc:
        print(f"[tail] error: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# issue stop
# ---------------------------------------------------------------------------

def _run_stop(args: argparse.Namespace) -> int:
    """Stop a running issue agent. Idempotent — already-stopped → success."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2
    print(f"Issue stop: sending stop command for {issue_id}")
    return _write_control("stop", issue_id)


# ---------------------------------------------------------------------------
# issue pause
# ---------------------------------------------------------------------------

def _run_pause(args: argparse.Namespace) -> int:
    """Pause a running issue agent. Idempotent — already-paused → success."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2
    reason = getattr(args, "reason", "") or "operator requested pause"
    print(f"Issue pause: sending pause command for {issue_id}")
    return _write_control("pause", issue_id, reason)


# ---------------------------------------------------------------------------
# issue resume
# ---------------------------------------------------------------------------

def _run_resume(args: argparse.Namespace) -> int:
    """Resume a paused issue agent. Idempotent — running → success."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2
    print(f"Issue resume: sending resume command for {issue_id}")
    return _write_control("resume", issue_id)


# ---------------------------------------------------------------------------
# issue takeover
# ---------------------------------------------------------------------------

def _run_takeover(args: argparse.Namespace) -> int:
    """Take over an issue (terminate + start REPL)."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    import os
    import subprocess
    from pathlib import Path

    # Resolve workspace path
    workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_root:
        base = Path(workspace_root)
    else:
        base = Path.home() / ".clawcodex" / "workspace"

    workspace_path = None
    if base.exists():
        for wd in base.iterdir():
            if wd.is_dir():
                metadata = wd / ".metadata"
                if metadata.exists():
                    import json
                    try:
                        m = json.loads(metadata.read_text())
                        if m.get("issue_id") == issue_id:
                            workspace_path = wd
                            break
                    except Exception:
                        pass

    if workspace_path is None:
        print(
            f"Could not find workspace for issue {issue_id}.\n"
            "Cannot takeover — issue may not be active.",
            file=sys.stderr,
        )
        return 1

    # Send stop control
    _write_control("stop", issue_id)

    print(
        f"[takeover] Stopping agent for issue {issue_id}...\n"
        f"Starting REPL in workspace: {workspace_path}\n"
        f"Type /done when finished to commit and push.",
        file=sys.stderr,
    )

    env = os.environ.copy()
    env["CLAWCODEX_WORKSPACE"] = str(workspace_path)
    try:
        subprocess.run(
            ["python3", "-m", "src.cli", "--workspace", str(workspace_path)],
            cwd=str(workspace_path),
            env=env,
        )
    except Exception as exc:
        print(f"[takeover] Failed to start REPL: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# issue clarify
# ---------------------------------------------------------------------------

def _run_clarify(args: argparse.Namespace) -> int:
    """Answer a clarification request. Idempotent — re-answering updates in place."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    answer = getattr(args, "answer", None)
    forward = getattr(args, "forward_to_author", False)

    if not answer and not forward:
        print("error: --answer is required unless --forward-to-author is used", file=sys.stderr)
        return 2

    from extensions.orchestrator.clarification_queue import ClarificationQueue

    queue = ClarificationQueue()
    resolved = queue.resolve(issue_id, answer or "", source="clarification_queue")
    if resolved is None:
        print(f"Failed to write answer for issue {issue_id}.", file=sys.stderr)
        return 1

    print(f"Answer recorded for issue {issue_id}: {answer or '(forwarded to author)'}")
    print(f"Status: {resolved.status.value}")
    print(f"The orchestrator will pick this up on its next poll cycle.")
    return 0


# ---------------------------------------------------------------------------
# issue inject
# ---------------------------------------------------------------------------

def _run_inject(args: argparse.Namespace) -> int:
    """Inject operator hints. Idempotent — listing/removal are safe."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    import os
    from pathlib import Path

    def _resolve_hints_file(issue_id: str) -> Path | None:
        ws_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
        if ws_root:
            base = Path(ws_root)
        else:
            base = Path.home() / ".clawcodex" / "workspace"

        if not base.exists():
            return None
        for wd in base.iterdir():
            if not wd.is_dir():
                continue
            metadata_file = wd / ".metadata"
            if metadata_file.exists():
                import json
                try:
                    metadata = json.loads(metadata_file.read_text())
                    if metadata.get("issue_id") == issue_id:
                        return wd / ".operator_hints.md"
                except Exception:
                    pass
            if wd.name == issue_id or issue_id in wd.name:
                return wd / ".operator_hints.md"
        return None

    hints_file = _resolve_hints_file(issue_id)
    if hints_file is None:
        print(
            f"Could not find workspace for issue {issue_id}.\n"
            "Hints are stored in the issue's workspace directory.\n"
            "Set CLAWCODEX_WORKSPACE_ROOT or run the orchestrator with --workflow.",
            file=sys.stderr,
        )
        return 1

    hint = getattr(args, "hint", None)
    list_hints = getattr(args, "list_hints", False)
    remove_hint = getattr(args, "remove_hint", None)

    if list_hints or (not hint and remove_hint is None):
        # List hints
        return _list_hints(issue_id, hints_file)
    elif remove_hint is not None:
        return _remove_hint(issue_id, hints_file, remove_hint)
    elif hint:
        return _inject_hint(issue_id, hints_file, hint)
    else:
        return _list_hints(issue_id, hints_file)


def _parse_hints_file(hints_file: Path) -> list[tuple[float, str]]:
    """Parse hints file into list of (timestamp, hint) tuples."""
    import time
    if not hints_file.exists():
        return []

    hints: list[tuple[float, str]] = []
    content = hints_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("--- Operator Hint #"):
            ts_str = ""
            try:
                parts = line.split("(injected at ")
                if len(parts) > 1:
                    ts_str = parts[1].rstrip(") ---")
                    from datetime import datetime
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    ts = dt.timestamp()
                else:
                    ts = time.time()
            except Exception:
                ts = time.time()

            hint_lines: list[str] = []
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith("-" * 45):
                    break
                hint_lines.append(lines[i])
                i += 1
            hint = "\n".join(hint_lines).strip()
            if hint:
                hints.append((ts, hint))
        i += 1
    return hints


def _inject_hint(issue_id: str, hints_file: Path, hint: str) -> int:
    """Append a hint to the .operator_hints.md file."""
    import time
    hints = _parse_hints_file(hints_file)
    next_num = len(hints) + 1
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"--- Operator Hint #{next_num} (injected at {timestamp}) ---\n"
    separator = "-" * 50 + "\n"
    try:
        with open(hints_file, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(hint + "\n")
            f.write(separator)
        print(f"Hint #{next_num} injected for issue {issue_id}")
        print(f"  The agent will see this on its next tool call.")
        return 0
    except Exception as exc:
        print(f"Failed to inject hint: {exc}", file=sys.stderr)
        return 1


def _list_hints(issue_id: str, hints_file: Path) -> int:
    """List all hints for an issue."""
    hints = _parse_hints_file(hints_file)
    if not hints:
        print(f"No hints for issue {issue_id}.")
        return 0
    print(f"Hints for issue {issue_id}:")
    for i, (ts, hint) in enumerate(hints, 1):
        import time
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        preview = hint[:60].replace("\n", " ")
        print(f"  #{i}: [{ts_str}] {preview}")
    return 0


def _remove_hint(issue_id: str, hints_file: Path, hint_num: int) -> int:
    """Remove a hint by number."""
    hints = _parse_hints_file(hints_file)
    if hint_num < 1 or hint_num > len(hints):
        print(f"Hint #{hint_num} not found (have {len(hints)} hints).", file=sys.stderr)
        return 1

    hints.pop(hint_num - 1)
    # Rebuild file
    import time
    content = ""
    for i, (ts, hint) in enumerate(hints, 1):
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        header = f"--- Operator Hint #{i} (injected at {ts_str}) ---\n"
        separator = "-" * 50 + "\n"
        content += header + hint + "\n" + separator
    hints_file.write_text(content, encoding="utf-8")
    print(f"Removed hint #{hint_num} for issue {issue_id}.")
    return 0


# ---------------------------------------------------------------------------
# issue workspace
# ---------------------------------------------------------------------------

def _run_workspace(args: argparse.Namespace) -> int:
    """View or modify workspace files. Workspace listing/view are pure reads."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    import os
    from pathlib import Path

    def _resolve_workspace_path(issue_id: str) -> Path | None:
        ws_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
        if ws_root:
            base = Path(ws_root)
        else:
            base = Path.home() / ".clawcodex" / "workspace"
        if not base.exists():
            return None
        for wd in base.iterdir():
            if not wd.is_dir():
                continue
            metadata_file = wd / ".metadata"
            if metadata_file.exists():
                import json
                try:
                    metadata = json.loads(metadata_file.read_text())
                    if metadata.get("issue_id") == issue_id:
                        return wd
                except Exception:
                    pass
            if wd.name == issue_id or issue_id in wd.name:
                return wd
        return None

    ws_path = _resolve_workspace_path(issue_id)
    if ws_path is None:
        print(f"Could not find workspace for issue {issue_id}.", file=sys.stderr)
        return 1

    ls_flag = getattr(args, "ls", False)
    cat_flag = getattr(args, "cat", None)
    edit_flag = getattr(args, "edit", None)
    content = getattr(args, "content", None)

    if ls_flag:
        return _workspace_list_files(issue_id, ws_path)
    elif cat_flag:
        return _workspace_cat_file(issue_id, ws_path, cat_flag)
    elif edit_flag:
        if not content:
            print("error: --edit requires --with <content>", file=sys.stderr)
            return 2
        return _workspace_edit_file(issue_id, ws_path, edit_flag, content)
    else:
        return _workspace_list_files(issue_id, ws_path)


def _workspace_list_files(issue_id: str, ws_path: Path) -> int:
    """List files in workspace. Idempotent — pure read."""
    if not ws_path.exists():
        print(f"Workspace for issue {issue_id} not found.", file=sys.stderr)
        return 1

    exclude = {".metadata", ".orchestrator_control", ".operator_hints.md"}
    print(f"Workspace for issue {issue_id}: {ws_path}")
    print("-" * 60)

    files: list[str] = []
    dirs: list[str] = []
    for item in sorted(ws_path.iterdir()):
        if item.name in exclude:
            continue
        if item.is_dir():
            dirs.append(item.name + "/")
        else:
            size = item.stat().st_size
            files.append(f"{item.name} ({size} bytes)")

    for d in dirs:
        print(f"  [DIR]  {d}")
    for f in files:
        print(f"  {f}")
    if not files and not dirs:
        print("  (empty workspace)")
    return 0


def _workspace_cat_file(issue_id: str, ws_path: Path, filename: str) -> int:
    """Show file contents. Idempotent — pure read."""
    file_path = ws_path / filename
    if not file_path.exists():
        print(f"File not found: {filename}", file=sys.stderr)
        return 1
    if not file_path.is_file():
        print(f"Not a file: {filename}", file=sys.stderr)
        return 1
    try:
        content = file_path.read_text(encoding="utf-8")
        print(f"=== {filename} ===")
        print(content)
    except Exception as exc:
        print(f"Failed to read {filename}: {exc}", file=sys.stderr)
        return 1
    return 0


def _workspace_edit_file(issue_id: str, ws_path: Path, filename: str, content: str) -> int:
    """Write new content to a file."""
    file_path = ws_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        file_path.write_text(content, encoding="utf-8")
        print(f"Updated {filename} in issue {issue_id} workspace.")
        print(f"  The agent will see this change on its next tool call.")
        return 0
    except Exception as exc:
        print(f"Failed to write {filename}: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# issue review
# ---------------------------------------------------------------------------

def _run_review(registry_path: Path | None, args: argparse.Namespace) -> int:
    """Approve or reject a LocalTracker issue's changes."""
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    if not registry_path or not registry_path.exists():
        print(f"No registry found. Cannot review issue {issue_id}.", file=sys.stderr)
        return 1

    from extensions.orchestrator.issue_registry import IssueRegistry, IssueStatus

    registry = IssueRegistry(registry_path)
    record = registry.get(issue_id)
    if record is None:
        print(f"Issue {issue_id} not found in registry.", file=sys.stderr)
        return 1

    if record.status != IssueStatus.PENDING_REVIEW:
        print(f"Issue {issue_id} is not pending review (status: {record.status.value}).", file=sys.stderr)
        print("Only issues with 'pending_review' status can be reviewed.", file=sys.stderr)
        return 1

    approve = getattr(args, "approve", False)
    reject = getattr(args, "reject", False)

    if not approve and not reject:
        print("error: specify --approve or --reject", file=sys.stderr)
        return 2

    if reject:
        feedback = getattr(args, "feedback", None)
        if not feedback:
            print("error: --reject requires --feedback", file=sys.stderr)
            return 2

        # Inject feedback as clarification request to trigger retry
        from extensions.orchestrator.clarification_queue import ClarificationQueue
        queue = ClarificationQueue()
        question = f"[Human Review Rejected] {feedback}"
        resolved = queue.inject_feedback(issue_id, question)
        if resolved is None:
            print(f"Failed to inject feedback for issue {issue_id}.", file=sys.stderr)
            return 1

        # Reset issue status to pending for retry
        registry._records[issue_id].status = IssueStatus.PENDING
        registry._save()

        # Write control command to retry the issue
        _write_control("retry", issue_id, feedback)

        print(f"Issue {issue_id} rejected with feedback:")
        print(f"  \"{feedback}\"")
        print(f"Feedback queued — orchestrator will retry this issue.")
        return 0

    if approve:
        comment = getattr(args, "comment", None)

        # Mark issue as completed
        registry.mark_completed(issue_id)

        # Post optional comment to local tracker
        if comment:
            try:
                from extensions.orchestrator.local_tracker.adapter import LocalTrackerAdapter
                from extensions.orchestrator.workspace_locator import get_workspace_root

                ws_root = get_workspace_root(
                    workspace_arg=getattr(args, "workspace", None),
                    workflow_path=getattr(args, "workflow", None),
                )
                if ws_root and ws_root.exists():
                    issues_path = ws_root / ".clawcodex_local_issues"
                    if issues_path.exists():
                        tracker = LocalTrackerAdapter(issues_path=str(issues_path))
                        import asyncio
                        asyncio.get_event_loop().run_until_complete(
                            tracker.create_comment(issue_id, f"## Approved\n\n{comment}")
                        )
            except Exception as exc:
                print(f"Warning: could not post comment: {exc}", file=sys.stderr)

        print(f"Issue {issue_id} approved and marked as completed.")
        return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_status_str(status) -> str:
    """Normalize status to string."""
    if hasattr(status, 'value'):
        return status.value
    return str(status)
