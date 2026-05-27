"""orchestrator clarify — operator answer to a clarification request.

Usage:
  clawcodex orchestrator clarify --issue <id> --answer <text>
  clawcodex orchestrator clarify --issue <id> --forward-to-author

Options:
  --issue <id>          Issue ID being clarified
  --answer <text>       Operator's answer to the clarification question
  --forward-to-author   Skip local answer, forward directly to author (@mention)

The operator's answer is written to the ClarificationQueue file
(~/.clawcodex/clarification_queue.json) for the orchestrator to pick up
on its next poll cycle.
"""

from __future__ import annotations

import argparse
import sys


def add_clarify_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "clarify",
        help="Answer a clarification request from the orchestrator",
        description="Write an operator answer to the clarification queue. "
                    "Used when the orchestrator asks for local input on an issue.",
    )
    parser.add_argument(
        "--issue",
        type=str,
        required=True,
        help="Issue ID being clarified",
    )
    parser.add_argument(
        "--answer",
        type=str,
        default=None,
        help="Operator's answer text",
    )
    parser.add_argument(
        "--forward-to-author",
        action="store_true",
        help="Skip local answer and forward directly to author via @mention",
    )


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator clarify command."""
    from extensions.orchestrator.clarification_queue import (
        ClarificationQueue,
        ClarificationStatus,
        DEFAULT_QUEUE_PATH,
    )

    issue_id = args.issue
    queue = ClarificationQueue()

    # Check current state
    item = queue.get(issue_id)
    if item is None:
        print(
            f"No pending clarification found for issue {issue_id}.",
            file=sys.stderr,
        )
        print(
            f"Run 'clawcodex orchestrator status' to see active issues.",
            file=sys.stderr,
        )
        return 1

    if args.forward_to_author:
        # Mark as awaiting_author to trigger @mention escalation
        queue.mark_awaiting_author(issue_id)
        print(f"Issue {issue_id} forwarded to author (@mention will be sent on next poll).")
        return 0

    answer = args.answer
    if not answer:
        print("error: --answer is required unless --forward-to-author is used", file=sys.stderr)
        return 2

    # Write the answer to the queue
    resolved = queue.resolve(issue_id, answer, source="clarification_queue")
    if resolved is None:
        print(f"Failed to write answer for issue {issue_id}.", file=sys.stderr)
        return 1

    print(f"Answer recorded for issue {issue_id}: {answer}")
    print(f"Status: {resolved.status.value}")
    print(f"The orchestrator will pick this up on its next poll cycle.")
    return 0