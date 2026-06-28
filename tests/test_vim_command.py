"""Tests for the ``/vim`` command (Phase 14 — port of TS ``type:'local'``).

Toggles the persisted ``editorMode`` (vim ↔ normal, legacy emacs reads as normal) and
the TUI seeds ``PromptInput(vim_mode=...)`` from it at construction — the functional
half that keeps this from being effort-style inert.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.config as cfg
from src.command_system import (
    VIM_COMMAND,
    create_command_context,
    get_builtin_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.safe_commands import (
    BRIDGE_SAFE_COMMANDS,
    REMOTE_SAFE_COMMANDS,
)
from src.command_system.types import CommandType
from src.command_system.vim_command import initial_vim_mode

_VIM_MSG = (
    "Editor mode set to vim. Use Escape key to toggle between INSERT and NORMAL "
    "modes. Takes effect on next TUI launch."
)
_NORMAL_MSG = (
    "Editor mode set to normal. Using standard (readline) keyboard bindings. "
    "Takes effect on next TUI launch."
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))
    return tmp_path


def _ctx(tmp_path: Path):
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path)


def _persisted(tmp_path: Path):
    return cfg.ConfigManager(cwd=tmp_path).get("editorMode")


# --------------------------------------------------------------------------- #
# A. Toggle matrix (messages verbatim TS vim.ts)
# --------------------------------------------------------------------------- #
async def test_unset_toggles_to_vim(isolated_config):
    result = await VIM_COMMAND.call("", _ctx(isolated_config))
    assert result.type == "text"
    assert result.value == _VIM_MSG
    assert _persisted(isolated_config) == "vim"


async def test_vim_toggles_to_normal(isolated_config):
    cfg._get_default_manager().set_global("editorMode", "vim")
    result = await VIM_COMMAND.call("", _ctx(isolated_config))
    assert result.value == _NORMAL_MSG
    assert _persisted(isolated_config) == "normal"


async def test_normal_toggles_to_vim(isolated_config):
    cfg._get_default_manager().set_global("editorMode", "normal")
    result = await VIM_COMMAND.call("", _ctx(isolated_config))
    assert result.value == _VIM_MSG
    assert _persisted(isolated_config) == "vim"


async def test_legacy_emacs_reads_as_normal_toggles_to_vim(isolated_config):
    cfg._get_default_manager().set_global("editorMode", "emacs")
    result = await VIM_COMMAND.call("", _ctx(isolated_config))
    assert result.value == _VIM_MSG
    assert _persisted(isolated_config) == "vim"


# --------------------------------------------------------------------------- #
# B. The consumer seed (the functional half)
# --------------------------------------------------------------------------- #
def test_initial_vim_mode(isolated_config):
    assert initial_vim_mode() is False  # unset
    cfg._get_default_manager().set_global("editorMode", "vim")
    assert initial_vim_mode() is True
    cfg._get_default_manager().set_global("editorMode", "normal")
    assert initial_vim_mode() is False
    cfg._get_default_manager().set_global("editorMode", "emacs")
    assert initial_vim_mode() is False


def test_initial_vim_mode_safe_on_error(monkeypatch):
    monkeypatch.setattr(
        "src.config.load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert initial_vim_mode() is False


# --------------------------------------------------------------------------- #
# C. Registration + metadata + safety + dispatch
# --------------------------------------------------------------------------- #
def test_registered_and_metadata():
    assert "vim" in {c.name for c in get_builtin_commands()}
    assert VIM_COMMAND.name == "vim"
    assert VIM_COMMAND.description == "Toggle between Vim and Normal editing modes"
    assert VIM_COMMAND.command_type == CommandType.LOCAL
    assert VIM_COMMAND.supports_non_interactive is False


def test_safety_and_dispatch():
    assert "vim" in REMOTE_SAFE_COMMANDS  # name-based remote filter (matches TS)
    assert "vim" not in BRIDGE_SAFE_COMMANDS
    assert is_bridge_safe_command(VIM_COMMAND) is False  # LOCAL, not allowlisted


# --------------------------------------------------------------------------- #
# D. Engine end-to-end (headless)
# --------------------------------------------------------------------------- #
async def test_engine_toggles_headless(isolated_config):
    reg = CommandRegistry()
    reg.register(VIM_COMMAND)
    ctx = _ctx(isolated_config)
    eng = CommandEngine(registry=reg, workspace_root=isolated_config, context=ctx)

    result = await eng.execute("/vim")

    assert result.success is True
    assert result.text == _VIM_MSG
    assert _persisted(isolated_config) == "vim"
