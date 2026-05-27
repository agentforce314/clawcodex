"""Orchestrator CLI subcommand router.

clawcodex orchestrator [--workspace PATH] [--workflow PATH] <subcommand> [args...]

Wires the orchestrator extension's CLI modules into the main clawcodex CLI.
Each subcommand (run, status, issues, etc.) is handled by its corresponding
module in extensions/orchestrator/cli/.

Global options --workspace and --workflow are parsed at the top level
and attached to args for subcommand handlers to use.

Pattern mirrors src/entrypoints/mcp.py, daemon.py, doctor.py.
"""

from __future__ import annotations

import argparse
import sys


def run_orchestrator_subcommand(rest: list[str]) -> int:
    """Handle ``clawcodex orchestrator [--workspace PATH] [--workflow PATH] <subcommand>``.

    Returns the process exit code.
    """
    # Extract global --workspace and --workflow options from anywhere in rest
    workspace_arg = None
    workflow_arg = None
    filtered_rest = []
    i = 0
    while i < len(rest):
        if rest[i] == "--workspace" and i + 1 < len(rest):
            workspace_arg = rest[i + 1]
            i += 2
        elif rest[i] == "--workflow" and i + 1 < len(rest):
            workflow_arg = rest[i + 1]
            i += 2
        else:
            filtered_rest.append(rest[i])
            i += 1

    # Build the main parser with subparsers
    parser = argparse.ArgumentParser(
        prog="clawcodex orchestrator",
        description="Autonomous issue processing orchestration",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Import CLI modules to register their subparsers
    from extensions.orchestrator.cli import (
        run_clarify,
        run_dashboard,
        run_inject,
        run_issues,
        run_lifecycle,
        run_run,
        run_status,
        run_workspace,
    )
    from extensions.orchestrator.cli.clarify import add_clarify_parser
    from extensions.orchestrator.cli.dashboard import add_dashboard_parser
    from extensions.orchestrator.cli.inject import add_inject_parser
    from extensions.orchestrator.cli.issues import add_issues_parser
    from extensions.orchestrator.cli.lifecycle import add_lifecycle_parser
    from extensions.orchestrator.cli.run import add_run_parser
    from extensions.orchestrator.cli.status import add_status_parser
    from extensions.orchestrator.cli.workspace import add_workspace_parser

    # Register subparsers for each command
    add_run_parser(subparsers)
    add_status_parser(subparsers)
    add_issues_parser(subparsers)
    add_dashboard_parser(subparsers)
    add_clarify_parser(subparsers)
    add_inject_parser(subparsers)
    add_lifecycle_parser(subparsers)
    add_workspace_parser(subparsers)

    # Parse all arguments (subcommand only - workspace/workflow already extracted)
    args = parser.parse_args(filtered_rest)

    # Attach extracted global options to args for subcommand handlers
    args.workspace = workspace_arg
    args.workflow = workflow_arg

    # Dispatch to the appropriate run() function
    if args.subcommand in ("run", None):
        return run_run(args)
    elif args.subcommand == "status":
        return run_status(args)
    elif args.subcommand == "issues":
        return run_issues(args)
    elif args.subcommand == "dashboard":
        return run_dashboard(args)
    elif args.subcommand == "clarify":
        return run_clarify(args)
    elif args.subcommand == "inject":
        return run_inject(args)
    elif args.subcommand in ("pause", "resume", "stop", "takeover"):
        return run_lifecycle(args)
    elif args.subcommand == "workspace":
        return run_workspace(args)
    else:
        parser.print_help()
        return 2