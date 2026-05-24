"""orchestrator workspace — view/modify files in issue workspaces.

Usage:
  clawcodex orchestrator workspace <issue_id> --ls
  clawcodex orchestrator workspace <issue_id> --cat <file>
  clawcodex orchestrator workspace <issue_id> --edit <file> --with <content>

This allows operators to view and modify workspace files during agent runs,
enabling manual intervention without stopping the agent.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def add_workspace_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace",
        help="View and modify workspace files during agent runs",
        description="List, view, or edit files in an issue workspace. "
                    "Use with caution — concurrent edits may conflict with agent changes.",
    )
    parser.add_argument("issue_id", help="Issue ID")
    parser.add_argument("--ls", action="store_true", help="List files in workspace")
    parser.add_argument("--cat", metavar="FILE", help="Show file contents")
    parser.add_argument(
        "--edit",
        metavar="FILE",
        help="Edit a file (use with --with)",
    )
    parser.add_argument(
        "--with",
        dest="content",
        metavar="CONTENT",
        help="New file content (for --edit)",
    )


def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator workspace command."""
    issue_id = args.issue_id

    workspace_path = _resolve_workspace_path(issue_id)
    if workspace_path is None:
        print(
            f"Could not find workspace for issue {issue_id}.\n"
            f"Set CLAWCODEX_WORKSPACE_ROOT or run the orchestrator with --workflow.",
            file=sys.stderr,
        )
        return 1

    if args.ls:
        return _list_files(issue_id, workspace_path)
    elif args.cat:
        return _cat_file(issue_id, workspace_path, args.cat)
    elif args.edit:
        if not args.content:
            print("error: --edit requires --with <content>", file=sys.stderr)
            return 2
        return _edit_file(issue_id, workspace_path, args.edit, args.content)
    else:
        # Default: list files
        return _list_files(issue_id, workspace_path)


def _resolve_workspace_path(issue_id: str) -> Path | None:
    """Resolve the workspace path for an issue."""
    workspace_root = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if workspace_root:
        base = Path(workspace_root)
    else:
        base = Path.home() / ".clawcodex" / "workspace"

    # Search for matching workspace
    if not base.exists():
        return None

    for workspace_dir in base.iterdir():
        if not workspace_dir.is_dir():
            continue
        metadata_file = workspace_dir / ".metadata"
        if metadata_file.exists():
            try:
                import json
                metadata = json.loads(metadata_file.read_text())
                if metadata.get("issue_id") == issue_id:
                    return workspace_dir
            except Exception:
                pass
        if workspace_dir.name == issue_id or issue_id in workspace_dir.name:
            return workspace_dir

    return None


def _list_files(issue_id: str, workspace_path: Path) -> int:
    """List all files in the workspace."""
    if not workspace_path.exists():
        print(f"Workspace for issue {issue_id} not found.", file=sys.stderr)
        return 1

    print(f"Workspace for issue {issue_id}: {workspace_path}")
    print("-" * 60)

    # Exclude metadata and control files
    exclude = {".metadata", ".orchestrator_control", ".operator_hints.md"}
    files = []
    dirs = []

    for item in sorted(workspace_path.iterdir()):
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


def _cat_file(issue_id: str, workspace_path: Path, filename: str) -> int:
    """Show file contents."""
    file_path = workspace_path / filename
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


def _edit_file(issue_id: str, workspace_path: Path, filename: str, content: str) -> int:
    """Write new content to a file."""
    file_path = workspace_path / filename

    # Create parent directories if needed
    file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        file_path.write_text(content, encoding="utf-8")
        print(f"Updated {filename} in issue {issue_id} workspace.")
        print(f"  The agent will see this change on its next tool call.")
        return 0
    except Exception as exc:
        print(f"Failed to write {filename}: {exc}", file=sys.stderr)
        return 1