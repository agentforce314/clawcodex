"""diff — ``/diff`` uncommitted-changes view (port of TS local-jsx, degraded).

TS ``/diff`` (``commands/diff/``) renders an interactive ``DiffDialog`` showing the
uncommitted changes AND per-turn file edits (from tool results). Python's TUI ``/diff``
reads ``app_state.pending_diffs`` (app-bound) and pushes ``DiffDialogScreen``. This
registry port shows the **uncommitted changes (staged + unstaged) as ``git diff`` text**
— the content TS shows via ``git diff HEAD``, reachable from ``CommandContext.cwd`` via
the existing ``src/utils/git.py`` helpers — dropping the per-turn diffs (app-bound), the
interactive viewer, and untracked files (TS's ``git diff HEAD`` omits those too). The TUI
keeps its rich dialog (inversion).

Follows the output-style/``/mcp``/``/tasks`` precedent: ``run()`` returns text WITHOUT
touching ``ctx.ui``, so it works on every surface.
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)

# Cap so a huge diff doesn't flood a single REPL/SDK message; the TUI has the full view.
_MAX_LINES = 500


def _cap(text: str) -> str:
    lines = text.split("\n")
    if len(lines) <= _MAX_LINES:
        return text
    more = len(lines) - _MAX_LINES
    head = "\n".join(lines[:_MAX_LINES])
    return (
        f"{head}\n… (diff truncated — {more} more line(s); use the TUI /diff for the "
        "full interactive view)"
    )


def _section(label: str, result) -> str:
    return (
        f"{label} ({result.files_changed} file(s), +{result.insertions} "
        f"-{result.deletions}):\n" + (result.diff_text or "").rstrip("\n")
    )


@dataclass(frozen=True)
class DiffCommand(InteractiveCommand):
    """Show the uncommitted changes (staged + unstaged) as ``git diff`` text. Frozen + no
    new fields (the ``McpCommand`` pattern); ``run()`` returns text without touching
    ``ctx.ui``."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        # Lazy import so `import src.command_system` doesn't pull the git/utils stack.
        from src.utils.git import get_repo_root, get_session_diff

        cwd = str(context.cwd)
        if get_repo_root(cwd) is None:
            # get_repo_root distinguishes not-a-repo from no-changes (both give "" from
            # the diff helper otherwise).
            return InteractiveOutcome("Not a git repository.", display="system")

        # Staged + unstaged = the "uncommitted changes" TS shows via `git diff HEAD`,
        # rendered as two labeled sections (avoids double-counting and handles a repo
        # with no commits, which a literal `git diff HEAD` can't). Untracked files are
        # not shown — matching TS's `git diff HEAD`.
        staged = get_session_diff(cwd, staged=True)
        unstaged = get_session_diff(cwd)
        sections: list[str] = []
        if (staged.diff_text or "").strip():
            sections.append(_section("Staged changes", staged))
        if (unstaged.diff_text or "").strip():
            sections.append(_section("Unstaged changes", unstaged))
        if not sections:
            return InteractiveOutcome("No uncommitted changes.", display="system")
        return InteractiveOutcome(_cap("\n\n".join(sections)), display="system")


DIFF_COMMAND = DiffCommand(
    name="diff",
    # Verbatim TS index.ts (the port shows the git working-tree diff only — see docstring).
    description="View uncommitted changes and per-turn diffs",
)


__all__ = ["DIFF_COMMAND", "DiffCommand"]
