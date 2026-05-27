"""orchestrator issues — list / show / tail issues.

Usage:
  clawcodex orchestrator issues list
  clawcodex orchestrator issues show <issue_id>
  clawcodex orchestrator issues tail <issue_id>

Options:
  list                   List all issues with status
  show <issue_id>        Show issue details (context, token usage, workspace)
  tail <issue_id>        Real-time tail of tool call logs (streaming)
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def add_issues_parser(subparsers: argparse._SubParsersAction) -> None:
    issues_parser = subparsers.add_parser(
        "issues",
        help="Issue-related operations",
        description="List, show, or tail issues being handled by the orchestrator.",
    )
    issues_sub = issues_parser.add_subparsers(dest="issues_subcommand", required=True)

    # list
    list_parser = issues_sub.add_parser("list", help="List all issues")
    list_parser.add_argument(
        "--status",
        choices=["pending", "synced", "completed", "failed", "abandoned"],
        help="Filter by status",
    )

    # show
    show_parser = issues_sub.add_parser("show", help="Show issue details")
    show_parser.add_argument("issue_id", help="Issue identifier (e.g. 42 or owner/repo#42)")

    # tail
    tail_parser = issues_sub.add_parser("tail", help="Tail tool call logs in real-time")
    tail_parser.add_argument("issue_id", help="Issue identifier")


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator issues command."""
    from extensions.orchestrator.issue_registry import IssueRegistry
    from extensions.orchestrator.workspace_locator import get_registry_path

    registry_path = get_registry_path(
        workspace_arg=args.workspace,
        workflow_path=args.workflow,
    )
    if not registry_path or not registry_path.exists():
        ws_info = ""
        if args.workspace:
            ws_info = f" (workspace: {args.workspace})"
        elif args.workflow:
            ws_info = f" (workflow: {args.workflow})"
        print(f"No orchestrator registry found{ws_info}. Is the orchestrator running?", file=sys.stderr)
        return 1

    registry = IssueRegistry(registry_path)

    if args.issues_subcommand == "list":
        return _run_list(registry, args)
    elif args.issues_subcommand == "show":
        return _run_show(registry, args.issue_id)
    elif args.issues_subcommand == "tail":
        return _run_tail(registry, args.issue_id)
    return 0


def _run_list(registry, args) -> int:
    from extensions.orchestrator.issue_registry import IssueStatus

    if not registry._records:
        print("No issues in registry.")
        return 0

    print(f"{'Issue ID':<30} {'Identifier':<25} {'Status':<12} {'Branch'}")
    print("-" * 85)
    for record in registry._records.values():
        if args.status and record.status.value != args.status:
            continue
        print(
            f"{record.issue_id:<30} "
            f"{record.issue_identifier or '':<25} "
            f"{record.status.value:<12} "
            f"{record.branch_name or '-'}"
        )
    return 0


def _run_show(registry, issue_id: str) -> int:
    record = registry.get(issue_id)
    if record is None:
        print(f"Issue {issue_id} not found in registry.", file=sys.stderr)
        return 1

    print(f"Issue: {record.issue_identifier or record.issue_id}")
    print(f"  Status     : {record.status.value}")
    print(f"  Branch     : {record.branch_name or '-'}")
    print(f"  Commit SHA : {record.commit_sha or '-'}")
    print(f"  PR Number  : {record.pr_number or '-'}")
    print(f"  PR URL     : {record.pr_url or '-'}")
    print(f"  Base Branch: {record.base_branch}")
    print(f"  Created at : {record.created_at}")
    print(f"  Updated at : {record.updated_at}")
    print(f"  Attempts   : {record.attempt_count}")
    return 0


def _run_tail(_registry, issue_id: str) -> int:
    """Tail the event log file for a running issue."""
    import json
    import sys
    import time

    workspace_root = _resolve_workspace_root()
    if workspace_root is None:
        print(
            "Could not determine workspace root.\n"
            "Set CLAWCODEX_WORKSPACE_ROOT or run the orchestrator with --workflow.",
            file=sys.stderr,
        )
        return 1

    event_log_path = workspace_root / ".event_logs" / f"{issue_id}.ndjson"

    if not event_log_path.exists():
        print(
            f"No event log found for issue {issue_id}.\n"
            f"Ensure the orchestrator is running and issue {issue_id} is active.\n"
            f"Run 'clawcodex orchestrator issues list' to check active issues.",
            file=sys.stderr,
        )
        return 1

    print(f"[tail] Streaming events for issue {issue_id}... (Ctrl+C to stop)")
    print("-" * 60)

    # Tail the ndjson file using inotifywait-like polling
    last_size = 0
    try:
        while True:
            if event_log_path.stat().st_size > last_size:
                with open(event_log_path, "r", encoding="utf-8") as f:
                    f.seek(last_size)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            ts = event.get("timestamp", "")
                            etype = event.get("type", "")
                            if etype == "tool_call":
                                params_str = str(event.get("params", {}))[:60]
                                print(f"  [{ts}] TOOL  {event.get('tool_name', '?')}  {params_str}")
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
                    last_size = f.tell()
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[tail] stopped")
    except Exception as exc:
        print(f"[tail] error: {exc}", file=sys.stderr)
        return 1

    return 0


def _resolve_registry_path():
    import os
    from pathlib import Path

    workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_root:
        return Path(workspace_root) / ".clawcodex_issue_registry.json"

    for candidate in [
        Path.cwd() / ".clawcodex_issue_registry.json",
        Path.home() / ".clawcodex" / "workspace" / ".clawcodex_issue_registry.json",
    ]:
        if candidate.exists():
            return candidate
    return None


def _resolve_workspace_root():
    """Resolve the workspace root directory."""
    import os
    from pathlib import Path

    workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_root:
        return Path(workspace_root)

    for candidate in [
        Path.cwd(),
        Path.home() / ".clawcodex" / "workspace",
    ]:
        if candidate.exists():
            return candidate
    return Path.cwd()