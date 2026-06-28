"""Tests for the ``/mcp`` command (Phase 9 — port of TS local-jsx, display-only).

``/mcp`` lists the configured MCP servers (Python's dialog is display-only). It follows
the output-style precedent: an ``InteractiveCommand`` whose ``run()`` returns text WITHOUT
touching ``ctx.ui`` (so it works on every surface incl. ``NullUIHost``). Coexistence is
**inversion** (the TUI keeps its ``McpListScreen`` via ``open_dialog="mcp"``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.config as cfg
from src.command_system import (
    MCP_COMMAND,
    McpCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.types import CommandType, InteractiveOutcome, NullUIHost


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "_default_manager", cfg.ConfigManager(cwd=tmp_path))
    return tmp_path


def _set_servers(servers: dict) -> None:
    cfg._get_default_manager().set_global("mcp_servers", servers)


def _ctx(tmp_path: Path, *, ui=None):
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path, ui=ui)


# --------------------------------------------------------------------------- #
# A. Metadata + registration
# --------------------------------------------------------------------------- #
def test_mcp_registered_in_builtins_and_aggregator():
    assert "mcp" in {c.name for c in get_builtin_commands()}
    assert "mcp" in {c.name for c in get_commands(cwd=str(Path.cwd()))}


def test_mcp_metadata_mirrors_ts():
    assert isinstance(MCP_COMMAND, McpCommand)
    assert MCP_COMMAND.name == "mcp"
    assert MCP_COMMAND.description == "Manage MCP servers"
    assert MCP_COMMAND.argument_hint == "[enable|disable [server-name]]"
    assert MCP_COMMAND.command_type == CommandType.INTERACTIVE
    assert MCP_COMMAND.is_hidden is False


# --------------------------------------------------------------------------- #
# B. Bridge-safety
# --------------------------------------------------------------------------- #
def test_mcp_blocked_from_bridge_by_type():
    assert is_bridge_safe_command(MCP_COMMAND) is False


# --------------------------------------------------------------------------- #
# C. List output — synthetic config exercising the ported _status_summary.
#    (status/tools/error aren't in the real config schema; this locks the
#    verbatim quirks: always-plural "1 tools", suffix only on connected.)
# --------------------------------------------------------------------------- #
async def test_list_output_synthetic(isolated_config):
    tmp = isolated_config
    _set_servers(
        {
            "a": {"name": "alpha", "status": "connected", "tools": ["t1"]},
            "b": {"name": "beta", "status": "error", "error": "boom"},
        }
    )
    out = await MCP_COMMAND.run("", _ctx(tmp, ui=NullUIHost()))  # no raise: never touches ui
    assert isinstance(out, InteractiveOutcome)
    assert out.message == (
        "MCP servers:\n"
        "• alpha — connected (1 tools)\n"  # always-plural quirk
        "• beta — error: boom"  # no tool suffix on error
    )
    assert out.display == "system"


async def test_list_output_realistic_renders_disconnected(isolated_config):
    tmp = isolated_config
    # Real config schema (command/args, no runtime status) → "disconnected".
    _set_servers({"x": {"command": "foo", "args": ["bar"]}})
    out = await MCP_COMMAND.run("", _ctx(tmp, ui=NullUIHost()))
    assert out.message == "MCP servers:\n• x — disconnected"  # name defaults to id


# --------------------------------------------------------------------------- #
# D. Empty
# --------------------------------------------------------------------------- #
async def test_empty(isolated_config):
    out = await MCP_COMMAND.run("", _ctx(isolated_config, ui=NullUIHost()))
    assert out.message == "No MCP servers configured."
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# E. Management args → not supported
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "arg", ["enable foo", "disable", "reconnect x", "no-redirect", "ENABLE all"]
)
async def test_management_args_not_supported(isolated_config, arg):
    _set_servers({"a": {"name": "alpha"}})
    out = await MCP_COMMAND.run(arg, _ctx(isolated_config, ui=NullUIHost()))
    assert out.message.startswith("MCP server management")
    assert "not supported" in out.message
    assert out.display == "system"


# --------------------------------------------------------------------------- #
# F. Engine end-to-end on a NullUIHost surface — no headless error (unlike pickers)
# --------------------------------------------------------------------------- #
async def test_engine_succeeds_headless(isolated_config):
    tmp = isolated_config
    _set_servers({"a": {"name": "alpha"}})
    reg = CommandRegistry()
    reg.register(MCP_COMMAND)
    ctx = create_command_context(workspace_root=tmp, cwd=tmp)  # ui=None → engine subs NullUIHost
    eng = CommandEngine(registry=reg, workspace_root=tmp, context=ctx)

    result = await eng.execute("/mcp")

    assert result.success is True
    assert result.result_type == "text"
    assert result.text.startswith("MCP servers:")
