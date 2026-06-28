"""Tests for the ``/stickers`` command (Phase 17 — port of TS ``type:'local'``)."""
from __future__ import annotations

import webbrowser
from pathlib import Path

import pytest

from src.command_system import (
    STICKERS_COMMAND,
    create_command_context,
    get_builtin_commands,
    is_bridge_safe_command,
)
from src.command_system.engine import CommandEngine
from src.command_system.registry import CommandRegistry
from src.command_system.safe_commands import REMOTE_SAFE_COMMANDS
from src.command_system.types import CommandType

_URL = "https://www.stickermule.com/claudecode"


def _ctx(tmp_path: Path):
    return create_command_context(workspace_root=tmp_path, cwd=tmp_path)


async def test_success_message(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    result = await STICKERS_COMMAND.call("", _ctx(tmp_path))
    assert result.value == "Opening sticker page in browser…"  # verbatim, U+2026
    assert opened == [_URL]


async def test_failure_falls_back_to_url(tmp_path, monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    result = await STICKERS_COMMAND.call("", _ctx(tmp_path))
    assert result.value == f"Failed to open browser. Visit: {_URL}"


async def test_exception_falls_back_to_url(tmp_path, monkeypatch):
    def _boom(url):
        raise RuntimeError("no display")

    monkeypatch.setattr(webbrowser, "open", _boom)
    result = await STICKERS_COMMAND.call("", _ctx(tmp_path))
    assert result.value == f"Failed to open browser. Visit: {_URL}"


async def test_engine_headless(tmp_path, monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url: True)
    reg = CommandRegistry()
    reg.register(STICKERS_COMMAND)
    ctx = _ctx(tmp_path)
    eng = CommandEngine(registry=reg, workspace_root=tmp_path, context=ctx)
    result = await eng.execute("/stickers")
    assert result.success is True
    assert result.text.startswith("Opening sticker page")


def test_registered_metadata_safety_dispatch():
    assert "stickers" in {c.name for c in get_builtin_commands()}
    assert STICKERS_COMMAND.description == "Order OpenClaude stickers"
    assert STICKERS_COMMAND.command_type == CommandType.LOCAL
    assert STICKERS_COMMAND.supports_non_interactive is False
    assert "stickers" in REMOTE_SAFE_COMMANDS  # matches TS
    assert is_bridge_safe_command(STICKERS_COMMAND) is False  # LOCAL, not allowlisted
