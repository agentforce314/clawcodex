"""
Legacy build_context_prompt — backward compatibility bridge.

This function is kept for callers that haven't migrated to the new
fetch_system_prompt_parts() API.  It runs the async WS-5 context
collection synchronously and returns a single prompt string.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path


def build_context_prompt(
    workspace_root: str | Path,
    *,
    cwd: str | Path | None = None,
) -> str:
    """
    Build a context prompt string (legacy API).

    Uses the new WS-5 context system under the hood.
    For new code, prefer fetch_system_prompt_parts() directly.
    """
    root = Path(workspace_root).expanduser().resolve()
    current = Path(cwd).expanduser().resolve() if cwd is not None else root

    sections: list[str] = []

    # Workspace info section
    sections.append(_build_workspace_section(root, current))

    # Git context (sync wrapper around async collect)
    git_section = _build_git_section(str(root))
    if git_section:
        sections.append(git_section)

    # CLAUDE.md context (sync wrapper around async get_memory_files)
    claude_section = _build_claude_md_section(str(current), root)
    if claude_section:
        sections.append(claude_section)

    return "\n\n".join(section for section in sections if section.strip())


def _run_async(coro):
    """Run an async function synchronously, handling nested event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Already in an async context — create a new thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=10)
    else:
        return asyncio.run(coro)


def _build_workspace_section(root: Path, current: Path) -> str:
    from .workspace_snapshot import build_workspace_snapshot
    try:
        workspace = build_workspace_snapshot(root, cwd=current)
    except Exception:
        return f"## Runtime Context\n- Today's date: {date.today().isoformat()}\n- Workspace root: {root}"

    lines = [
        "## Runtime Context",
        f"- Today's date: {date.today().isoformat()}",
        f"- Workspace root: {workspace.workspace_root}",
        f"- Current directory: {workspace.current_directory}",
        f"- Python files: {workspace.python_file_count}",
        f"- Test files: {workspace.test_file_count}",
    ]
    if workspace.key_files:
        lines.append(f"- Key files: {', '.join(workspace.key_files)}")
    if workspace.top_level_entries:
        lines.append(f"- Top-level entries: {', '.join(workspace.top_level_entries)}")
    return "\n".join(lines)


def _build_git_section(cwd: str) -> str:
    from .git_context import collect_git_context, format_git_status
    try:
        ctx = _run_async(collect_git_context(cwd))
        if not ctx.available:
            return ""
        formatted = format_git_status(ctx)
        if formatted:
            return f"## Git Context\n{formatted}"
    except Exception:
        pass
    return ""


def _build_claude_md_section(cwd: str, root: Path) -> str:
    from .claude_md import get_claude_mds, get_memory_files
    try:
        memory_files = _run_async(get_memory_files(cwd=cwd))
        content = get_claude_mds(memory_files)
        if content:
            return f"## Project Instructions\n{content}"
    except Exception:
        pass
    return ""
