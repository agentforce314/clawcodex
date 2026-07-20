"""memory — ``/memory`` memory-file picker (port of TS local-jsx).

Port of ``typescript/src/commands/memory/`` + the core of ``MemoryFileSelector``.
Presents the CLAUDE.md memory hierarchy — the synthetic **User memory**
(``~/.clawcodex/CLAUDE.md``) and **Project memory** (nearest loaded ancestor
``CLAUDE.md``, falling back to ``{cwd}/CLAUDE.md``) candidates, differentiated via
option *descriptions* (the TS selector's real channel), plus the existing files
enumerated by the ``claude_md`` port — ensure-creates the selected file
(exclusive-create; existing content preserved), and reports its path with an editor
hint.

The FULL port — picker overlay + TS ``editFileInEditor``'s suspend-aware
``$EDITOR`` spawn + post-edit cache bust — lives in the Ink TUI
(``ui-tui/src/components/memoryPicker.tsx`` + ``ui-tui/src/lib/memoryEdit.ts``),
fed by the agent-server's ``memory_targets`` / ``memory_edited`` controls over
``build_memory_options`` below. This InteractiveCommand remains the UIHost-driven
fallback for non-TUI hosts (and yields the clean ``NullUIHost`` engine error on
headless surfaces); with no way to suspend a caller's screen from here, it
reports the path instead of spawning (the ``/copy`` clipboard standard — the
cancel/error strings stay TS-verbatim).

Deliberate divergences (documented for parity review):
  * **Folder-open extras dropped** (auto-memory / team / agent folders — need the
    open-folder mechanism and those subsystems).
  * Rules files are listed flat (the ``select`` primitive has flat labels; TS indents).

Picker-only (TS ignores args) → no headless keystone; ``NullUIHost`` gets a clean
engine error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
    UIOption,
)


def _display_path(path: str, cwd: str) -> str:
    """``~``-abbreviated, else cwd-relative, else absolute (the TS
    ``getRelativeMemoryPath`` shape)."""
    home = str(Path.home())
    real = os.path.realpath(path)
    try:
        rel = os.path.relpath(real, os.path.realpath(cwd))
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    if real.startswith(home + os.sep) or real == home:
        return "~" + real[len(home):]
    return real


def _resolve_project_memory_path(files: list, cwd: str) -> str:
    """TS ``getProjectMemoryPathForSelector``: prefer the **nearest** already-loaded
    root-level Project CLAUDE.md (TS walks cwd upward; ``get_memory_files`` enumerates
    root→cwd, so iterate reversed — last match = nearest); fall back to
    ``{cwd}/CLAUDE.md`` only when none exists — so a repo subdirectory still points at
    the real project memory."""
    for info in reversed(files):
        if (
            getattr(info, "parent", None) is None
            and getattr(info, "type", None) == "Project"
            and os.path.basename(info.path) == "CLAUDE.md"
        ):
            return info.path
    return str(Path(cwd) / "CLAUDE.md")


async def build_memory_options(cwd: str) -> list[UIOption]:
    """Public: the memory-target hierarchy, shared by ``/memory`` and
    the C9 ``#`` shortcut so the two pickers can never drift."""
    from src.context_system.claude_md import (
        clear_memory_file_caches,
        get_memory_files,
    )

    try:
        clear_memory_file_caches()  # TS primes fresh per open (memory.tsx:86-87)
        files = list(await get_memory_files(cwd=cwd))
    except Exception:
        files = []

    home = Path.home()
    user_path = str(home / ".clawcodex" / "CLAUDE.md")
    project_path = _resolve_project_memory_path(files, cwd)

    # Git-aware project description (TS: `${isGit ? 'Checked in at' : 'Saved in'} ./…`).
    try:
        from src.utils.git import get_repo_root

        in_git = get_repo_root(cwd) is not None
    except Exception:
        in_git = False
    project_desc = (
        f"{'Checked in at' if in_git else 'Saved in'} ./{os.path.basename(project_path)}"
    )

    # NOTE: TS's `" (new)"` suffix is DEAD CODE (only interpolated in the depth>0
    # branch, where exists is always true) — the real differentiation is the
    # description column, ported here. Labels stay plain.
    options: list[UIOption] = [
        UIOption(
            value=user_path,
            label="User memory",
            description="Saved in ~/.clawcodex/CLAUDE.md",  # hardcoded tilde (TS shape)
        ),
        UIOption(value=project_path, label="Project memory", description=project_desc),
    ]
    seen: set[str] = {os.path.realpath(user_path), os.path.realpath(project_path)}

    # Bounded persistent-memory stores (hermes-agent port, src/memory):
    # §-delimited MEMORY.md / USER.md the Memory tool curates. Editable
    # here too — the store's drift guard protects tool rewrites against
    # free-form external edits.
    try:
        from src.memory import get_memory_dir

        bounded_dir = get_memory_dir()
        for fname, label in (
            ("MEMORY.md", "Agent memory (bounded)"),
            ("USER.md", "User profile (bounded)"),
        ):
            bpath = str(bounded_dir / fname)
            real = os.path.realpath(bpath)
            if real in seen:
                continue
            seen.add(real)
            options.append(
                UIOption(
                    value=bpath,
                    label=label,
                    description="§-delimited entries — curated by the Memory tool",
                )
            )
    except Exception:
        pass  # the bounded store is optional; the picker must still open

    # Remaining enumerated files (managed/user/project + rules), deduped by realpath
    # (stronger than TS's exact-path dedup — deliberate). Parented files were
    # @-imported (TS desc); others get no description.
    for info in files:
        real = os.path.realpath(info.path)
        if real in seen:
            continue
        seen.add(real)
        options.append(
            UIOption(
                value=info.path,
                label=_display_path(info.path, cwd),
                description="@-imported" if getattr(info, "parent", None) else None,
            )
        )
    return options


def _ensure_file(path: str) -> None:
    """TS parity: mkdir the config home when the path is under it, then
    exclusive-create an empty file (existing content preserved)."""
    config_home = str(Path.home() / ".clawcodex")
    if path.startswith(config_home):
        Path(config_home).mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8"):
            pass
    except FileExistsError:
        pass


_EDITOR_HINT = (
    "> To choose an editor, set the $EDITOR or $VISUAL environment variable."
)


@dataclass(frozen=True)
class MemoryCommand(InteractiveCommand):
    """Pick a memory file, ensure it exists, and report its path."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        cwd = str(context.cwd)
        options = await build_memory_options(cwd)
        picked = await context.ui.select("Memory", options)
        if picked is None:
            # Verbatim TS (memory.tsx:65).
            return InteractiveOutcome(
                message="Cancelled memory editing", display="system"
            )
        try:
            _ensure_file(picked)
        except Exception as exc:
            # Verbatim TS catch shape (memory.tsx:61) — onDone without options.
            return InteractiveOutcome(
                message=f"Error opening memory file: {exc}", display="user"
            )
        return InteractiveOutcome(
            message=(
                f"Memory file at {_display_path(picked, cwd)}. "
                f"Open it in your editor.\n\n{_EDITOR_HINT}"
            ),
            display="system",  # TS uses display:'system' for the success line
        )


MEMORY_COMMAND = MemoryCommand(
    name="memory",
    description="Edit Claude memory files",  # verbatim TS index.ts
)


__all__ = ["MEMORY_COMMAND", "MemoryCommand", "build_memory_options"]
