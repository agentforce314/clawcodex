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
import os
import sys
from pathlib import Path
from typing import Any


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
    tail_parser.add_argument(
        "--turn",
        type=int,
        default=None,
        metavar="N",
        help="Filter to show only events from turn number N",
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

    # --- issue diff ---
    diff_parser = issue_sub.add_parser(
        "diff",
        help="Show code changes for a completed or pending_review issue",
        description="Display a summary or full diff of changes made by the agent. "
                    "Shows stats by default, use --full for complete diff output.",
    )
    diff_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to show diff for",
    )
    diff_parser.add_argument(
        "--full",
        action="store_true",
        help="Show complete diff output (not just summary stats)",
    )
    diff_parser.add_argument(
        "--stat",
        action="store_true",
        help="Show only file change statistics (default when no --full)",
    )
    diff_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path (optional auto-detection override)",
    )

    # --- issue retry (F-39 Sub-E: CLI 兜底命令) ---
    retry_parser = issue_sub.add_parser(
        "retry",
        help="Retry/follow-up/unblock an issue via the CLI fallback",
        description="Operator-driven fallback for F-39 intents when label / "
                    "comment paths are inconvenient. Records the action in "
                    "~/.clawcodex/orchestrator/audit.jsonl and updates the "
                    "local issue registry so the next daemon poll picks up "
                    "the new intent.",
    )
    retry_parser.add_argument(
        "--id",
        type=str,
        required=True,
        metavar="ISSUE_ID",
        help="Issue identifier to retry / follow-up / unblock",
    )
    retry_parser.add_argument(
        "--mode",
        type=str,
        choices=["reset", "followup", "unblock"],
        required=True,
        help="Intent mode: 'reset' clears state and re-runs (agent:retry), "
             "'followup' appends a commit to the existing branch "
             "(agent:follow-up), 'unblock' rolls an abandoned issue back "
             "to pending so the daemon reconsiders it.",
    )
    retry_parser.add_argument(
        "--reason",
        type=str,
        default="",
        metavar="TEXT",
        help="Free-form reason recorded in audit.jsonl",
    )
    retry_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the max_retries_per_issue rate limit (CLI-only "
             "override; logged as a high-priority audit entry).",
    )
    retry_parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        metavar="N",
        help="Operator override for max_retries_per_issue (default: 3). "
             "Has no effect unless --force is also set; the audit "
             "log records both the configured limit and the actual "
             "retry_count when --force triggers a bypass.",
    )
    retry_parser.add_argument(
        "--operator",
        type=str,
        default=None,
        metavar="LOGIN",
        help="Operator login recorded in audit.jsonl "
             "(defaults to $USER / os.getlogin())",
    )
    retry_parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Explicit workspace root path "
             "(optional auto-detection override)",
    )
    retry_parser.add_argument(
        "--workflow",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to WORKFLOW.md (resolution hint when metadata is missing)",
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
    elif cmd == "diff":
        return _run_diff(registry_path, args)
    elif cmd == "retry":
        return _run_retry(registry_path, args)

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

        # 当 --workspace / --workflow 都没传时，检查是否有多个活跃 orch 项目
        if not ws and not wf:
            from extensions.orchestrator.workspace_locator import (
                get_live_projects,
                print_multi_project_hint,
            )
            live = get_live_projects()
            if len(live) > 1:
                print_multi_project_hint(live, "orchestrator issue list")
                return 0

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
    print(f"  {'STATUS':<15} {'ISSUE ID':<20} {'TURN/TOOL':<9} {'LAST EVENT':<18} {'BRANCH':<30}")
    print(f"  {'-'*15} {'-'*20} {'-'*9} {'-'*18} {'-'*30}")
    for r in records:
        s = _get_status_str(r.status)
        branch = r.branch_name or "-"
        turn_tool = f"{getattr(r, 'run_turn_count', 0)}/{getattr(r, 'run_tool_count', 0)}"
        last_event = getattr(r, "run_last_event", None) or "-"
        print(f"  {s:<15} {r.issue_id:<20} {turn_tool:<9} {last_event:<18} {branch:<30}")

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
    record = registry.get_by_issue_ref(issue_id)
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
    print(f"  Run ID         : {getattr(record, 'run_id', None) or '-'}")
    print(f"  Turns / Tools  : {getattr(record, 'run_turn_count', 0)} / {getattr(record, 'run_tool_count', 0)}")
    print(f"  Last Event     : {getattr(record, 'run_last_event', None) or '-'}")
    print(f"  Last Tool      : {getattr(record, 'run_last_tool', None) or '-'}")
    print(f"  Output Chars   : {getattr(record, 'run_output_len', 0)}")
    deadline = getattr(record, 'run_timeout_deadline_at', None)
    if deadline:
        deadline_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(deadline))
    else:
        deadline_text = "-"
    print(f"  Timeout By     : {deadline_text}")
    workspace_dirty = getattr(record, 'run_workspace_dirty', None)
    dirty_text = "-" if workspace_dirty is None else str(workspace_dirty).lower()
    print(f"  Workspace Dirty: {dirty_text}")
    print(f"  Debug Log      : {getattr(record, 'debug_log_path', None) or '-'}")
    print(f"  Created        : {created}")
    print(f"  Updated        : {updated}")
    if record.clarification_status:
        print(f"  Clarification  : {record.clarification_status}")
    return 0


def _resolve_issue_workspace_path(issue_id: str) -> Path | None:
    """Resolve an issue workspace, including sequential registry layouts."""
    from extensions.orchestrator.workspace_locator import get_registry_path, get_workspace_root

    workspace_root = get_workspace_root(workspace_arg=os.environ.get("CLAWCODEX_WORKSPACE_ROOT"))
    registry_path = get_registry_path(workspace_arg=str(workspace_root)) if workspace_root else None
    if registry_path and registry_path.exists():
        import json

        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            record = registry.get(issue_id)
            if record:
                root = Path(record.get("workspace_path") or workspace_root)
                candidates = []
                identifier = record.get("issue_identifier")
                if identifier:
                    candidates.append(root / identifier)
                candidates.append(root)
                for candidate in candidates:
                    if candidate.exists():
                        return candidate
        except Exception:
            pass

    base = workspace_root or Path.home() / ".clawcodex" / "workspace"
    if not base.exists():
        return None
    for wd in base.iterdir():
        if not wd.is_dir():
            continue
        metadata_file = wd / ".metadata"
        if metadata_file.exists():
            import json
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                if metadata.get("issue_id") == issue_id:
                    return wd
            except Exception:
                pass
        if wd.name == issue_id or issue_id in wd.name:
            return wd
    return None


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

    from extensions.orchestrator.workspace_locator import get_workspace_root

    workspace_arg = getattr(args, "workspace", None)
    previous_workspace = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_arg:
        os.environ["CLAWCODEX_WORKSPACE_ROOT"] = str(workspace_arg)
    try:
        ws = _resolve_issue_workspace_path(issue_id)
    finally:
        if workspace_arg:
            if previous_workspace is None:
                os.environ.pop("CLAWCODEX_WORKSPACE_ROOT", None)
            else:
                os.environ["CLAWCODEX_WORKSPACE_ROOT"] = previous_workspace

    workspace_root = get_workspace_root(
        workspace_arg=workspace_arg,
        workflow_path=None,
    )
    if ws is None:
        ws = workspace_root
    if ws is None:
        print("Cannot resolve workspace root.", file=sys.stderr)
        return 1

    candidates = [ws / ".event_logs" / f"{issue_id}.ndjson"]
    if workspace_root is not None and workspace_root != ws:
        candidates.append(workspace_root / ".event_logs" / f"{issue_id}.ndjson")
        candidates.append(workspace_root / f"_{issue_id}" / ".event_logs" / f"{issue_id}.ndjson")
    else:
        candidates.append(ws / f"_{issue_id}" / ".event_logs" / f"{issue_id}.ndjson")

    log_file = next((candidate for candidate in candidates if candidate.exists()), None)
    if log_file is None:
        print(f"No event log found for issue {issue_id}.", file=sys.stderr)
        return 1

    turn_filter = getattr(args, "turn", None)
    if turn_filter is not None:
        print(f"Filtering to turn {turn_filter} only.")
    print(f"Tailing events for issue {issue_id} (Ctrl+C to stop)...")
    try:
        last_size = log_file.stat().st_size
        pending = ""
        while True:
            current_size = log_file.stat().st_size
            if current_size <= last_size:
                time.sleep(0.5)
                continue

            with open(log_file, "r", encoding="utf-8") as f:
                f.seek(last_size)
                chunk = f.read()

            lines = (pending + chunk).splitlines(keepends=True)
            if lines and not lines[-1].endswith("\n"):
                pending = lines.pop()
            else:
                pending = ""

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    # Turn filter
                    event_turn = event.get("turn")
                    if turn_filter is not None and event_turn != turn_filter:
                        continue
                    turn_prefix = f"[T{event_turn}] " if event_turn is not None else ""
                    ts = event.get("timestamp", "")[-8:] if event.get("timestamp") else ""
                    etype = event.get("type", "?")
                    if etype == "tool_call":
                        tool_name = event.get("tool_name", "?")
                        print(f"  [{ts}] {turn_prefix}CALL  {tool_name}")
                    elif etype == "tool_result":
                        err = " [ERR]" if event.get("is_error") else ""
                        print(f"  [{ts}] {turn_prefix}RESULT{err} {event.get('tool_name', '?')}")
                    elif etype == "text_delta":
                        content = event.get("content", "")
                        if content:
                            text = content[:80].replace("\n", " ")
                            print(f"  [{ts}] {turn_prefix}TEXT  {text}")
                    else:
                        print(f"  [{ts}] {turn_prefix}{etype}")
                except json.JSONDecodeError as exc:
                    print(f"[tail] warning: malformed event in {log_file}: {exc}", file=sys.stderr)
            last_size = current_size
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

    ws_path = _resolve_issue_workspace_path(issue_id)
    hints_file = ws_path / ".operator_hints.md" if ws_path else None
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

    ws_path = _resolve_issue_workspace_path(issue_id)
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

def _tracker_from_workflow_arg(args: argparse.Namespace) -> Any | None:
    workflow_path = getattr(args, "workflow", None)
    if not workflow_path:
        return None
    try:
        from extensions.orchestrator.tracker import create_tracker_adapter
        from extensions.orchestrator.workflow import WorkflowLoader

        workflow, _ = WorkflowLoader.load(workflow_path)
        return create_tracker_adapter(workflow.tracker)
    except Exception as exc:
        print(f"Warning: could not initialize tracker from workflow: {exc}", file=sys.stderr)
        return None


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

        # F-?? Fix 3: mark the issue as completed and make the CLI's
        # write authoritative against the daemon's stale in-memory
        # state.  ``mark_completed`` writes the file but if the daemon
        # re-saves with its in-memory copy before we exit, the change
        # is clobbered.  We re-read the file after the save and, if
        # the status is back to pending_review, force a second write
        # so the operator's approval decision is the last word.
        registry.mark_completed(issue_id)
        try:
            _verify_registry = IssueRegistry(registry_path)
            _verify_record = _verify_registry._records.get(issue_id)
            if (
                _verify_record is not None
                and _verify_record.status != IssueStatus.COMPLETED
            ):
                # Daemon (or another writer) overwrote between our
                # save and re-read; force the completion back.
                _verify_registry.mark_completed(issue_id)
        except Exception as _exc:  # noqa: BLE001
            print(
                f"warning: post-approval verification failed: {_exc}",
                file=sys.stderr,
            )

        tracker = _tracker_from_workflow_arg(args)
        if tracker is not None:
            try:
                import asyncio

                async def update_tracker() -> None:
                    await tracker.update_issue_state(issue_id, "completed")
                    if comment:
                        await tracker.create_comment(issue_id, f"## Approved\n\n{comment}")

                asyncio.run(update_tracker())
            except Exception as exc:
                print(f"Warning: could not update tracker: {exc}", file=sys.stderr)

        print(f"Issue {issue_id} approved and marked as completed.")
        return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
# issue diff
# ---------------------------------------------------------------------------

def _run_diff(registry_path: Path | None, args: argparse.Namespace) -> int:
    """Show code changes for an issue using git diff."""
    from pathlib import Path
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required", file=sys.stderr)
        return 2

    if not registry_path or not registry_path.exists():
        print(f"No registry found. Cannot show diff for issue {issue_id}.", file=sys.stderr)
        return 1

    from extensions.orchestrator.issue_registry import IssueRegistry
    registry = IssueRegistry(registry_path)
    record = registry.get(issue_id)
    if record is None:
        print(f"Issue {issue_id} not found in registry.", file=sys.stderr)
        return 1

    branch_name = record.branch_name
    if not branch_name:
        print(f"Issue {issue_id} has no branch name recorded.", file=sys.stderr)
        return 1

    # Resolve workspace path
    workspace_root = getattr(args, "workspace", None)
    if workspace_root is None:
        workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")

    if not workspace_root:
        print("Cannot resolve workspace root. Set CLAWCODEX_WORKSPACE_ROOT or use --workspace.", file=sys.stderr)
        return 1

    ws_path = Path(workspace_root)
    if not ws_path.exists():
        print(f"Workspace not found: {ws_path}", file=sys.stderr)
        return 1

    previous_workspace = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    os.environ["CLAWCODEX_WORKSPACE_ROOT"] = str(ws_path)
    try:
        issue_ws = _resolve_issue_workspace_path(issue_id)
    finally:
        if previous_workspace is None:
            os.environ.pop("CLAWCODEX_WORKSPACE_ROOT", None)
        else:
            os.environ["CLAWCODEX_WORKSPACE_ROOT"] = previous_workspace

    if issue_ws is None:
        for wd in ws_path.iterdir():
            if not wd.is_dir():
                continue
            metadata_file = wd / ".metadata"
            if metadata_file.exists():
                import json
                try:
                    metadata = json.loads(metadata_file.read_text())
                    if metadata.get("issue_id") == issue_id:
                        issue_ws = wd
                        break
                except Exception:
                    pass
            if wd.name == issue_id or issue_id in wd.name:
                issue_ws = wd
                break

    if issue_ws is None:
        print(f"Workspace not found for issue {issue_id}.", file=sys.stderr)
        return 1

    # Check if it's a git repository
    git_dir = issue_ws / ".git"
    if not git_dir.exists():
        # Not a git repo — show file tree instead
        return _show_diff_non_git(issue_ws, issue_id, args)

    import subprocess

    base_branch = record.base_branch or "main"

    # Get agent's run summary from comments (if available)
    agent_summary = _fetch_agent_summary(issue_id, ws_path)

    # Get diff compared to parent commit (this is what the agent actually changed)
    diff_target = _get_diff_target(issue_ws)

    # Get diff stat (summary)
    stat_result = subprocess.run(
        ["git", "diff", "--stat", diff_target],
        cwd=str(issue_ws),
        capture_output=True,
        text=True,
    )

    # Also get the actual diff content
    diff_result = subprocess.run(
        ["git", "diff", "--no-color", diff_target],
        cwd=str(issue_ws),
        capture_output=True,
        text=True,
    )

    show_full = getattr(args, "full", False)
    show_stat_only = getattr(args, "stat", False) and not show_full

    print(f"Issue {issue_id} — Changes")
    print(f"  Branch    : {branch_name}")
    print(f"  Base      : {base_branch}")
    if record.commit_sha:
        print(f"  Commit    : {record.commit_sha[:12]}")
    print()

    # Show agent summary if available
    if agent_summary:
        print("## Agent Summary")
        print(agent_summary)
        print()

    if stat_result.stdout.strip():
        print(stat_result.stdout)

    if show_full and diff_result.stdout.strip():
        print("--- Full Diff ---")
        print(diff_result.stdout)
    elif show_stat_only:
        pass  # stat already printed above
    else:
        # Default: show stat + first 50 lines of diff
        print("--- Diff Preview (use --full for complete output) ---")
        diff_lines = diff_result.stdout.strip().split("\n")
        if len(diff_lines) > 60:
            print("\n".join(diff_lines[:60]))
            print(f"\n  ... ({len(diff_lines) - 60} more lines, use --full to see all)")
        elif diff_lines:
            print("\n".join(diff_lines))

    return 0


def _get_diff_target(ws_path: Path) -> str:
    """Get the diff target (compare HEAD vs its parent commit)."""
    import subprocess

    # Get the parent commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=str(ws_path),
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        parent = result.stdout.strip()
        return f"{parent}...HEAD"

    # If no parent (first commit), show diff of working tree vs empty
    return "HEAD"


def _fetch_agent_summary(issue_id: str, ws_path: Path) -> str | None:
    """Fetch the agent's run summary from issue comments.

    Returns the first "## ClawCodex Run Complete" comment if found,
    otherwise returns None.
    """
    import json
    import re
    from pathlib import Path

    # Pattern to find safe stem for issue
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", issue_id.strip()).strip("-._")

    # Search in multiple possible locations for comments
    search_dirs = [
        ws_path,  # workspace root
        ws_path.parent / ".clawcodex_local_issues",
        ws_path.parent / ".clawcodex",
    ]

    for comments_dir in search_dirs:
        if not comments_dir.exists():
            continue

        # Find comment files matching this issue
        comment_files = list(comments_dir.glob(f"{safe_stem}*.comments.ndjson"))
        if not comment_files:
            # Also try with the issue directory name
            comment_files = list(comments_dir.glob(f"*{issue_id}*.comments.ndjson"))

        for cf in comment_files:
            try:
                for line in cf.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    body = payload.get("body", "")
                    if "## ClawCodex Run Complete" in body:
                        # Extract the output excerpt section
                        if "**Output excerpt:**" in body:
                            idx = body.index("**Output excerpt:**")
                            return body[idx:]
                        elif body:
                            # Return the whole body as summary
                            return body[:500] if len(body) > 500 else body
            except Exception:
                pass

    return None


def _has_origin(ws_path: Path) -> bool:
    """Check if the workspace has an origin remote."""
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
        cwd=str(ws_path),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _show_diff_non_git(ws_path: Path, issue_id: str, args: argparse.Namespace) -> int:
    """Show file tree for non-git workspace."""
    print(f"Issue {issue_id} — Workspace Files (not a git repository)")
    print(f"  Workspace: {ws_path}")
    print()

    exclude = {".metadata", ".orchestrator_control", ".operator_hints.md", ".event_logs"}

    files: list[tuple[str, str, int]] = []
    dirs: list[str] = []

    for item in sorted(ws_path.iterdir()):
        if item.name in exclude:
            continue
        if item.is_dir():
            dirs.append(item.name + "/")
        else:
            size = item.stat().st_size
            rel_path = item.relative_to(ws_path)
            files.append((str(rel_path), "file", size))

    if not files and not dirs:
        print("  (empty workspace)")
        return 0

    print(f"  {'FILE':<50} {'SIZE':>10}")
    print(f"  {'-'*50} {'-'*10}")

    for name, _, size in sorted(files):
        size_str = _format_size(size)
        print(f"  {name:<50} {size_str:>10}")

    for d in dirs:
        print(f"  {d:<50} {'[DIR]':>10}")

    print(f"\n  {len(files)} files, {len(dirs)} directories")
    print("\n  Note: This workspace is not a git repository — no diff available.")
    print("  Use 'clawcodex orchestrator issue workspace --id {} --cat <file>' to view file contents.".format(issue_id))
    return 0


def _format_size(size: int) -> str:
    """Format file size in human-readable form."""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size // 1024}KB"
    else:
        return f"{size // (1024 * 1024)}MB"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_status_str(status) -> str:
    """Normalize status to string."""
    if hasattr(status, 'value'):
        return status.value
    return str(status)


# ---------------------------------------------------------------------------
# issue retry  (F-39 Sub-E: CLI 兜底命令)
# ---------------------------------------------------------------------------

# Single source of truth for the on-disk audit log location. Tests
# override this by monkey-patching `_DEFAULT_AUDIT_LOG_PATH` to a
# tempdir, so the production path is the only constant we expose.
_DEFAULT_AUDIT_LOG_PATH = Path.home() / ".clawcodex" / "orchestrator" / "audit.jsonl"


def _resolve_operator(explicit: str | None) -> str:
    """Resolve the operator login for audit logging.

    Priority: explicit --operator arg > $USER env > os.getlogin() > 'unknown'.
    """
    if explicit:
        return explicit
    env_user = os.environ.get("USER") or os.environ.get("USERNAME")
    if env_user:
        return env_user
    try:
        return os.getlogin()
    except Exception:
        return "unknown"


def _append_audit_log(
    *,
    issue_id: str,
    mode: str,
    reason: str,
    operator: str,
    force: bool,
    extra: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path | None:
    """Append a single JSONL line to the local audit log.

    F-39 design: "~/.clawcodex/orchestrator/audit.jsonl 记录
    {ts, operator, issue_id, mode, reason} 便于追溯".

    Returns the path written, or None on I/O failure (the CLI surfaces
    audit failures to the operator as a warning but does not abort —
    the registry update is the user-visible side-effect).
    """
    import json
    import time

    target = path or _DEFAULT_AUDIT_LOG_PATH
    payload: dict[str, Any] = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operator": operator,
        "issue_id": issue_id,
        "mode": mode,
        "reason": reason,
        "force": force,
        "priority": "high" if force else "normal",
    }
    if extra:
        payload.update(extra)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return target
    except Exception as exc:
        print(
            f"warning: failed to write audit log {target}: {exc}",
            file=sys.stderr,
        )
        return None


def _run_retry(registry_path: Path | None, args: argparse.Namespace) -> int:
    """F-39 Sub-E: CLI 兜底命令 — record an operator-driven retry intent.

    Behaviour (per the design doc):

      * ``--mode reset``    — mark intent=RETRY, reset the registry record
                              to PENDING, and reopen the workflow tracker
                              issue so the daemon can pick it up.
      * ``--mode followup`` — mark intent=FOLLOWUP so Sub-C reuses the
                              existing branch.
      * ``--mode unblock``  — call IssueRegistry.unblock() to roll an
                              ABANDONED issue back to PENDING.

    All three branches append a JSONL entry to the local audit log
    (~/.clawcodex/orchestrator/audit.jsonl) so the action is
    traceable. ``--force`` flags the audit entry as high-priority
    and signals that the rate limit (Sub-F) was bypassed.
    """
    issue_id = getattr(args, "id", None)
    if not issue_id:
        print("error: --id is required for retry", file=sys.stderr)
        return 2
    mode = getattr(args, "mode", None)
    if mode not in {"reset", "followup", "unblock"}:
        print(f"error: --mode must be reset|followup|unblock, got {mode!r}",
              file=sys.stderr)
        return 2
    reason = getattr(args, "reason", "") or ""
    force = bool(getattr(args, "force", False))
    operator = _resolve_operator(getattr(args, "operator", None))
    max_retries = int(getattr(args, "max_retries", 3) or 3)

    if registry_path is None or not registry_path.exists():
        print(
            "error: no issue registry found for this workspace.\n"
            "hint: run from a project root or pass --workspace / --workflow.",
            file=sys.stderr,
        )
        return 1

    from extensions.orchestrator.issue_registry import IssueRegistry
    from extensions.orchestrator.tracker import Intent

    registry = IssueRegistry(registry_path)
    record = registry.get_by_issue_ref(issue_id)
    if record is None:
        # Auto-register so the daemon can find the record on its next
        # poll. CLI retry is a legitimate way to bootstrap an issue
        # record when the local daemon hasn't seen the issue yet.
        registry.register(
            issue_id=issue_id,
            issue_identifier=issue_id,
        )
        record = registry.get(issue_id)
        assert record is not None  # just registered
    registry_issue_id = record.issue_id

    # F-39 Sub-F: rate-limit guard for --mode reset. The CLI path is
    # the only one with a --force escape hatch, and any bypass MUST
    # be recorded as a high-priority audit entry per the design doc:
    # "限频与人工 bypass:CLI 兜底命令的 --force 参数可绕过
    # max_retries_per_issue 限频,需写 audit.jsonl 高优条目".
    rate_limited = False
    if mode == "reset" and record.retry_count >= max_retries and not force:
        rate_limited = True

    if rate_limited:
        action = "rate-limited (--force required)"
        audit_priority = "high"
        audit_event = "retry_rejected"
    else:
        if mode == "reset":
            registry.mark_intent(
                registry_issue_id, Intent.RETRY,
                source="cli", command=f"cli:reset:{reason[:64]}",
            )
            registry.reset_for_retry(registry_issue_id)
            tracker = _tracker_from_workflow_arg(args)
            if tracker is not None:
                try:
                    import asyncio

                    async def reopen_tracker_issue() -> None:
                        try:
                            await tracker.update_issue_state(issue_id, "open")
                        except FileNotFoundError:
                            if registry_issue_id == issue_id:
                                raise
                            await tracker.update_issue_state(registry_issue_id, "open")

                    asyncio.run(reopen_tracker_issue())
                except Exception as exc:
                    print(f"Warning: could not update tracker: {exc}", file=sys.stderr)
            action = "marked for reset"
        elif mode == "followup":
            registry.mark_intent(
                registry_issue_id, Intent.FOLLOWUP,
                source="cli", command=f"cli:followup:{reason[:64]}",
            )
            action = "marked for follow-up"
        else:  # mode == "unblock"
            registry.unblock(registry_issue_id)
            action = "unblocked"
        audit_priority = "high" if force else "normal"
        audit_event = "retry" if mode == "reset" else mode

    audit_path = _append_audit_log(
        issue_id=issue_id,
        mode=mode,
        reason=reason,
        operator=operator,
        force=force,
        extra={
            "issue_identifier": record.issue_identifier,
            "event": audit_event,
            "priority": audit_priority,
            "retry_count": record.retry_count,
            "max_retries_per_issue": max_retries,
            "rate_limited": rate_limited,
        },
    )

    print(f"Issue {issue_id} ({record.issue_identifier}): {action}.")
    if reason:
        print(f"  reason: {reason}")
    print(f"  operator: {operator}")
    if rate_limited:
        print(
            f"  rate limit: retry_count={record.retry_count} >= "
            f"max_retries_per_issue={max_retries}.\n"
            f"  Re-run with --force to bypass (logged as high-priority audit).",
            file=sys.stderr,
        )
    if force and not rate_limited:
        print("  (--force set: rate limit bypassed, audit entry marked high-priority)")
    if audit_path is not None:
        print(f"  audit log: {audit_path}")
    print("  The orchestrator will pick this up on its next poll cycle.")
    return 0 if not rate_limited else 3
