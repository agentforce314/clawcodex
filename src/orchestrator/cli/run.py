"""orchestrator run — launch the orchestrator (replaces --workflow flag).

Usage:
  clawcodex orchestrator run --workflow PATH [options]

Options:
  --workflow PATH        Path to WORKFLOW.md file (required)
  --dashboard            Show embedded status dashboard
  --port PORT            LiveView dashboard port

This is the replacement for the legacy `clawcodex --workflow PATH` command.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "run",
        help="Launch the orchestrator in autonomous mode",
        description="Run orchestrator using a WORKFLOW.md file. "
                    "Replaces the deprecated --workflow flag.",
    )
    parser.add_argument(
        "--workflow",
        type=str,
        required=True,
        metavar="PATH",
        help="Path to WORKFLOW.md file",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Show embedded status dashboard",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="LiveView dashboard port",
    )


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator run command."""
    from src.orchestrator.tracker import TrackerConfigError, validate_tracker_config
    from src.orchestrator.workflow import WorkflowLoader, WorkflowParseError

    workflow_path = args.workflow
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

    from src.api.orchestration import OrchestrationSubsystem

    subsystem = OrchestrationSubsystem(config)

    async def _run() -> None:
        try:
            await subsystem.run()
        except asyncio.CancelledError:
            await subsystem.shutdown()
            raise

    if args.dashboard:
        dashboard_task = asyncio.create_task(
            _dashboard_loop(subsystem.status_dashboard, args.port)
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