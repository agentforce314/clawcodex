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
    from src.orchestrator.issue_registry import IssueRegistry

    # Try to read the registry from the default workspace location
    # Note: In a live orchestrator scenario, this would connect to the
    # running orchestrator's status dashboard. Here we show registry state.
    registry_path = _resolve_registry_path()
    if registry_path and registry_path.exists():
        registry = IssueRegistry(registry_path)
        _print_summary(registry)
        if args.watch:
            print("Use --watch with a running orchestrator for live updates")
    else:
        print("No orchestrator registry found. Is the orchestrator running?")
        print("Hint: Run 'clawcodex orchestrator run --workflow WORKFLOW.md' first.")
    return 0


def _print_summary(registry: "IssueRegistry") -> None:
    from src.orchestrator.issue_registry import IssueStatus

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


def _resolve_registry_path():
    """Resolve the issue registry path from environment or default."""
    import os
    from pathlib import Path

    workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_root:
        return Path(workspace_root) / ".clawcodex_issue_registry.json"

    # Try common locations
    for candidate in [
        Path.cwd() / ".clawcodex_issue_registry.json",
        Path.home() / ".clawcodex" / "workspace" / ".clawcodex_issue_registry.json",
    ]:
        if candidate.exists():
            return candidate
    return None