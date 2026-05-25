"""orchestrator inject — inject operator hints into a running agent.

Usage:
  clawcodex orchestrator inject <issue_id> "hint text"
  clawcodex orchestrator inject <issue_id> --list
  clawcodex orchestrator inject <issue_id> --remove <hint_num>

The hint is written to .operator_hints.md in the issue's workspace directory.
AgentRunner reads this file at each tool call boundary and injects hints
into the tool context.

File format (workspace/.operator_hints.md):
  --- Operator Hint (injected at 2026-05-19 10:35:00) ---
  别动 auth.py，已经有人在改了
  -----------------------------------
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def add_inject_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "inject",
        help="Inject operator hints into a running agent",
        description="Write a hint to .operator_hints.md in the issue workspace. "
                    "The agent reads this file at each tool call boundary.",
    )
    parser.add_argument("issue_id", help="Issue ID to inject hint for")
    parser.add_argument(
        "hint",
        nargs="?",
        default=None,
        help="Hint text to inject (omit to just list hints)",
    )
    parser.add_argument(
        "--list",
        dest="list_hints",
        action="store_true",
        help="List existing hints for this issue",
    )
    parser.add_argument(
        "--remove",
        dest="remove_hint",
        type=int,
        metavar="N",
        help="Remove hint number N",
    )


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator inject command."""
    issue_id = args.issue_id

    hints_file = _resolve_hints_file(issue_id)
    if hints_file is None:
        print(
            f"Could not find workspace for issue {issue_id}.\n"
            f"Hints are stored in the issue's workspace directory.\n"
            f"Set CLAWCODEX_WORKSPACE_ROOT or run the orchestrator with --workflow.",
            file=sys.stderr,
        )
        return 1

    if args.list_hints:
        return _list_hints(issue_id, hints_file)
    elif args.remove_hint is not None:
        return _remove_hint(issue_id, hints_file, args.remove_hint)
    elif args.hint:
        return _inject_hint(issue_id, hints_file, args.hint)
    else:
        # No action — list hints
        return _list_hints(issue_id, hints_file)


def _resolve_hints_file(issue_id: str) -> Path | None:
    """Resolve the .operator_hints.md path for an issue."""
    workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_root:
        base = Path(workspace_root)
    else:
        base = Path.home() / ".clawcodex" / "workspace"

    # Try to find the issue workspace
    for workspace_dir in base.iterdir():
        if not workspace_dir.is_dir():
            continue
        # Each workspace is named by issue identifier
        hints_file = workspace_dir / ".operator_hints.md"
        if hints_file.exists():
            # Check if this is the right issue
            metadata_file = workspace_dir / ".metadata"
            if metadata_file.exists():
                try:
                    import json
                    metadata = json.loads(metadata_file.read_text())
                    if metadata.get("issue_id") == issue_id:
                        return hints_file
                except Exception:
                    pass
            # Fallback: check directory name
            if workspace_dir.name == issue_id or issue_id in workspace_dir.name:
                return hints_file

    # Default path (orchestrator will create workspace if running)
    default = base / f"workspace_{issue_id}" / ".operator_hints.md"
    default.parent.mkdir(parents=True, exist_ok=True)
    return default


def _format_hint_entry(timestamp: float, hint: str, number: int) -> str:
    """Format a hint entry with marker."""
    from datetime import datetime
    dt = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"--- Operator Hint #{number} (injected at {dt}) ---\n"
        f"{hint}\n"
        f"{'─' * 45}"
    )


def _inject_hint(issue_id: str, hints_file: Path, hint: str) -> int:
    """Append a hint to the hints file."""
    timestamp = time.time()
    hints_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing hints to determine number
    existing = _parse_hints_file(hints_file)
    next_num = len(existing) + 1

    entry = f"\n{_format_hint_entry(timestamp, hint, next_num)}\n"

    with open(hints_file, "a", encoding="utf-8") as f:
        f.write(entry)

    print(f"Injected hint #{next_num} for issue {issue_id}: {hint[:50]}{'...' if len(hint) > 50 else ''}")
    print(f"  The agent will read this on its next tool call.")
    return 0


def _list_hints(issue_id: str, hints_file: Path) -> int:
    """List all hints for an issue."""
    if not hints_file.exists():
        print(f"No hints found for issue {issue_id}.")
        return 0

    existing = _parse_hints_file(hints_file)
    if not existing:
        print(f"No hints found for issue {issue_id}.")
        return 0

    print(f"Hints for issue {issue_id} ({len(existing)} total):")
    print("-" * 50)
    for i, (ts, hint) in enumerate(existing, 1):
        from datetime import datetime
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{i}] {dt}")
        print(f"    {hint[:80]}{'...' if len(hint) > 80 else ''}")
        print()
    return 0


def _remove_hint(issue_id: str, hints_file: Path, hint_num: int) -> int:
    """Remove a specific hint by number."""
    if not hints_file.exists():
        print(f"No hints found for issue {issue_id}.", file=sys.stderr)
        return 1

    existing = _parse_hints_file(hints_file)
    if hint_num < 1 or hint_num > len(existing):
        print(f"Invalid hint number {hint_num}. Valid range: 1-{len(existing)}.", file=sys.stderr)
        return 1

    # Remove the hint
    removed_ts, removed_hint = existing.pop(hint_num - 1)

    # Rewrite the file
    hints_file.write_text("", encoding="utf-8")
    for i, (ts, hint) in enumerate(existing, 1):
        entry = f"\n{_format_hint_entry(ts, hint, i)}\n"
        hints_file.write_text(entry, encoding="utf-8", append=True)

    print(f"Removed hint #{hint_num} for issue {issue_id}: {removed_hint[:50]}{'...' if len(removed_hint) > 50 else ''}")
    return 0


def _parse_hints_file(hints_file: Path) -> list[tuple[float, str]]:
    """Parse the hints file into list of (timestamp, hint) tuples."""
    if not hints_file.exists():
        return []

    hints = []
    content = hints_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("--- Operator Hint #"):
            # Parse timestamp from header
            ts_str = ""
            try:
                # Format: --- Operator Hint #N (injected at YYYY-MM-DD HH:MM:SS) ---
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

            # Read hint lines until separator
            hint_lines = []
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