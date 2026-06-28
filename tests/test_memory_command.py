"""Tests for the ``/memory`` command (Phase 15 — port of TS local-jsx, no-spawn)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.command_system import (
    MEMORY_COMMAND,
    MemoryCommand,
    create_command_context,
    get_builtin_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.memory_command import _display_path
from src.command_system.registry import CommandRegistry
from src.command_system.types import CommandType, InteractiveOutcome, NullUIHost


class FakeUIHost:
    def __init__(self, *, pick=None):
        self._pick = pick
        self.select_calls: list[dict] = []

    async def select(self, title, options, *, current=None):
        self.select_calls.append(
            {
                "title": title,
                "values": [o.value for o in options],
                "labels": [o.label for o in options],
                "descriptions": [o.description for o in options],
            }
        )
        return self._pick

    async def prompt_text(self, title, *, default="", placeholder=None):
        return None

    async def display(self, title, body):
        return None


@pytest.fixture
def mem_env(tmp_path, monkeypatch):
    """Fake HOME (so ~/.claude lives in tmp) + empty get_memory_files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    async def _no_files(cwd=None, **kwargs):
        return []

    monkeypatch.setattr(
        "src.context_system.claude_md.get_memory_files", _no_files
    )
    cwd = tmp_path / "proj"
    cwd.mkdir()
    return home, cwd


def _ctx(cwd: Path, *, ui=None):
    return create_command_context(workspace_root=cwd, cwd=cwd, ui=ui)


class _Info:
    def __init__(self, path, type="Project", parent=None):
        self.path = path
        self.type = type
        self.parent = parent


# --------------------------------------------------------------------------- #
# A. Options (plain labels; TS differentiates via DESCRIPTIONS — " (new)" is
#    dead code in TS and is not ported)
# --------------------------------------------------------------------------- #
async def test_options_synthetic_candidates_with_descriptions(mem_env):
    home, cwd = mem_env  # no git repo, no enumerated files
    ui = FakeUIHost(pick=None)
    await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    call = ui.select_calls[0]
    assert call["labels"][0] == "User memory"
    assert call["labels"][1] == "Project memory"
    assert call["descriptions"][0] == "Saved in ~/.claude/CLAUDE.md"  # verbatim TS
    assert call["descriptions"][1] == "Saved in ./CLAUDE.md"  # non-git branch
    assert call["values"][0] == str(home / ".claude" / "CLAUDE.md")
    assert call["values"][1] == str(cwd / "CLAUDE.md")  # fallback (no loaded ancestor)


async def test_project_memory_resolved_from_ancestor_walk(mem_env, monkeypatch):
    # From a repo SUBDIRECTORY the Project entry must point at the loaded
    # root-level CLAUDE.md, not {cwd}/CLAUDE.md (critic M2).
    home, cwd = mem_env
    root_claude = cwd / "CLAUDE.md"
    root_claude.write_text("root project mem")
    subdir = cwd / "sub"
    subdir.mkdir()

    async def _files(cwd=None, **kwargs):
        return [_Info(str(root_claude), type="Project", parent=None)]

    monkeypatch.setattr("src.context_system.claude_md.get_memory_files", _files)
    ui = FakeUIHost(pick=None)
    await MEMORY_COMMAND.run("", _ctx(subdir, ui=ui))
    call = ui.select_calls[0]
    assert call["values"][1] == str(root_claude)  # ancestor-resolved, not sub/CLAUDE.md
    assert call["values"].count(str(root_claude)) == 1  # deduped from the enumeration
    assert "Project memory" in call["labels"]


async def test_project_memory_prefers_nearest_ancestor(mem_env, monkeypatch):
    # Monorepo: /repo/CLAUDE.md + /repo/sub/CLAUDE.md, cwd=/repo/sub/deep ->
    # the NEAREST loaded ancestor wins (TS walks cwd upward).
    home, cwd = mem_env
    (cwd / "CLAUDE.md").write_text("root")
    sub = cwd / "sub"
    deep = sub / "deep"
    deep.mkdir(parents=True)
    (sub / "CLAUDE.md").write_text("nearer")

    async def _files(cwd=None, **kwargs):
        return [  # enumeration order is root -> cwd
            _Info(str(mem_env[1] / "CLAUDE.md"), type="Project", parent=None),
            _Info(str(sub / "CLAUDE.md"), type="Project", parent=None),
        ]

    monkeypatch.setattr("src.context_system.claude_md.get_memory_files", _files)
    ui = FakeUIHost(pick=None)
    await MEMORY_COMMAND.run("", _ctx(deep, ui=ui))
    assert ui.select_calls[0]["values"][1] == str(sub / "CLAUDE.md")


async def test_project_desc_git_branch(mem_env, monkeypatch):
    home, cwd = mem_env
    monkeypatch.setattr("src.utils.git.get_repo_root", lambda *a, **k: str(cwd))
    ui = FakeUIHost(pick=None)
    await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    assert ui.select_calls[0]["descriptions"][1] == "Checked in at ./CLAUDE.md"


async def test_options_extra_files_and_imported_desc(mem_env, monkeypatch):
    home, cwd = mem_env

    async def _files(cwd=None, **kwargs):
        return [
            _Info(str(home / "extra.md"), type="User", parent=None),
            _Info(str(home / "imported.md"), type="User", parent=str(home / "extra.md")),
        ]

    monkeypatch.setattr("src.context_system.claude_md.get_memory_files", _files)
    ui = FakeUIHost(pick=None)
    await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    call = ui.select_calls[0]
    i_extra = call["values"].index(str(home / "extra.md"))
    i_imp = call["values"].index(str(home / "imported.md"))
    assert call["labels"][i_extra] == "~/extra.md"
    assert call["descriptions"][i_extra] is None
    assert call["descriptions"][i_imp] == "@-imported"  # parented -> TS desc


# --------------------------------------------------------------------------- #
# B. Select: ensure-create + preservation + message
# --------------------------------------------------------------------------- #
async def test_select_user_memory_creates_dir_and_file(mem_env):
    home, cwd = mem_env
    target = str(home / ".claude" / "CLAUDE.md")
    ui = FakeUIHost(pick=target)
    out = await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    assert isinstance(out, InteractiveOutcome)
    assert (home / ".claude" / "CLAUDE.md").exists()
    assert out.message.startswith("Memory file at ~/.claude/CLAUDE.md. Open it in your editor.")
    assert "> To choose an editor, set the $EDITOR or $VISUAL environment variable." in out.message
    assert out.display == "system"


async def test_select_preserves_existing_content(mem_env):
    home, cwd = mem_env
    target = cwd / "CLAUDE.md"
    target.write_text("precious")
    ui = FakeUIHost(pick=str(target))
    await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    assert target.read_text() == "precious"  # exclusive-create swallowed


async def test_select_create_failure(mem_env, monkeypatch):
    home, cwd = mem_env
    monkeypatch.setattr(
        "src.command_system.memory_command._ensure_file",
        lambda path: (_ for _ in ()).throw(OSError("nope")),
    )
    ui = FakeUIHost(pick=str(cwd / "CLAUDE.md"))
    out = await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    assert out.message == "Error opening memory file: nope"
    assert out.display == "user"


async def test_cancel(mem_env):
    home, cwd = mem_env
    ui = FakeUIHost(pick=None)
    out = await MEMORY_COMMAND.run("", _ctx(cwd, ui=ui))
    assert out.message == "Cancelled memory editing"  # verbatim TS
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# C. Display-path helper
# --------------------------------------------------------------------------- #
def test_display_path(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    cwd = tmp_path / "w"
    cwd.mkdir()
    assert _display_path(str(cwd / "CLAUDE.md"), str(cwd)) == "CLAUDE.md"
    assert _display_path(str(home / "x.md"), str(cwd)) == "~/x.md"


# --------------------------------------------------------------------------- #
# D. Null surface + registration + safety + dispatch
# --------------------------------------------------------------------------- #
async def test_engine_errors_on_null_surface(mem_env):
    home, cwd = mem_env
    reg = CommandRegistry()
    reg.register(MEMORY_COMMAND)
    ctx = create_command_context(workspace_root=cwd, cwd=cwd)
    eng = CommandEngine(registry=reg, workspace_root=cwd, context=ctx)
    result = await eng.execute("/memory")
    assert result.success is False
    assert "interactive surface" in result.error


def test_registered_metadata_safety_dispatch():
    assert "memory" in {c.name for c in get_builtin_commands()}
    assert isinstance(MEMORY_COMMAND, MemoryCommand)
    assert MEMORY_COMMAND.description == "Edit Claude memory files"
    assert MEMORY_COMMAND.command_type == CommandType.INTERACTIVE
    assert is_bridge_safe_command(MEMORY_COMMAND) is False
