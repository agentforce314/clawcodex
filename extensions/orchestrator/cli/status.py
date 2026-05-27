"""orchestrator status — show global orchestrator status.

Usage:
  clawcodex orchestrator status [--watch]

Options:
  --watch              Real-time monitoring mode (like top)
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def add_status_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "status",
        help="Show global orchestrator status",
        description="Display running/completed/failed issue counts "
                    "and current orchestrator state.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Real-time monitoring mode",
    )


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator status command."""
    from extensions.orchestrator.issue_registry import IssueRegistry
    from extensions.orchestrator.workspace_locator import get_registry_path

    # Try to read the registry from the resolved workspace location
    registry_path = get_registry_path(
        workspace_arg=args.workspace,
        workflow_path=args.workflow,
    )
    if registry_path and registry_path.exists():
        registry = IssueRegistry(registry_path)
        _print_summary(registry)
        if args.watch:
            print("Use --watch with a running orchestrator for live updates")
    else:
        print("No orchestrator registry found. Is the orchestrator running?")
        print("Hint: Run 'clawcodex orchestrator run --workflow WORKFLOW.md' first.")
        # List available projects
        _print_available_projects()
    return 0


def _print_summary(registry: "IssueRegistry") -> None:
    from extensions.orchestrator.issue_registry import IssueStatus

    counts = {"PENDING": 0, "SYNCED": 0, "COMPLETED": 0, "FAILED": 0, "ABANDONED": 0}
    for record in registry._records.values():
        key = record.status.name
        counts[key] = counts.get(key, 0) + 1

    print(f"Issue Registry Summary ({len(registry._records)} total)")
    print(f"  PENDING   : {counts.get('PENDING', 0)}")
    print(f"  SYNCED    : {counts.get('SYNCED', 0)}")
    print(f"  COMPLETED : {counts.get('COMPLETED', 0)}")
    print(f"  FAILED    : {counts.get('FAILED', 0)}")
    print(f"  ABANDONED : {counts.get('ABANDONED', 0)}")


def _print_available_projects() -> None:
    """Print available orchestrator projects from metadata."""
    from extensions.orchestrator.workspace_locator import list_orchestrator_projects

    projects = list_orchestrator_projects()
    if not projects:
        print("\n  (no orchestrator projects found)")
        return

    print("\n  Available projects:")
    for p in projects:
        slug = p.get("project_slug", "unknown")
        ws = p.get("workspace_root", "unknown")
        started = p.get("started_at", 0)
        if started:
            import time
            age = int(time.time() - started)
            age_str = f"{age}s ago"
        else:
            age_str = ""
        print(f"    {slug}: {ws} ({age_str})")