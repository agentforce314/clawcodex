"""Tests for the moved-to-plugin command factory (Phase 1.5).

Covers ``create_moved_to_plugin_command`` / ``MovedToPluginCommand`` — the USER_TYPE gate
(ant → static plugin-install text; everyone else → private builder), sync + async builder
dispatch, and the builtin metadata. Port of ``createMovedToPluginCommand.ts``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.command_system import (
    CommandType,
    create_command_context,
    create_moved_to_plugin_command,
)


def _make_command(builder):
    return create_moved_to_plugin_command(
        name="foo-cmd",
        description="a foo command",
        progress_message="fooing",
        plugin_name="foo",
        plugin_command="bar",
        get_prompt_while_marketplace_is_private=builder,
    )


def _ctx(tmp_path: Path):
    return create_command_context(workspace_root=tmp_path)


def _raising_builder(args: str, context: Any) -> list[dict[str, Any]]:
    raise AssertionError("private builder must NOT run on the ant branch")


async def test_ant_branch_returns_static_text(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_TYPE", "ant")
    cmd = _make_command(_raising_builder)

    result = await cmd.get_prompt_for_command("", _ctx(tmp_path))

    assert len(result) == 1
    text = result[0]["text"]
    # Verbatim moved-to-plugin message wiring (createMovedToPluginCommand.ts:48-57).
    assert "openclaude plugin install foo@claude-code-marketplace" in text
    assert "/foo:bar" in text
    assert (
        "https://github.com/anthropics/claude-code-marketplace/blob/main/foo/README.md"
        in text
    )


async def test_private_branch_dispatches_to_builder(monkeypatch, tmp_path):
    monkeypatch.delenv("USER_TYPE", raising=False)
    sentinel = [{"type": "text", "text": "PRIVATE"}]
    cmd = _make_command(lambda args, context: sentinel)

    result = await cmd.get_prompt_for_command("", _ctx(tmp_path))

    assert result == sentinel


async def test_private_branch_awaits_async_builder(monkeypatch, tmp_path):
    monkeypatch.delenv("USER_TYPE", raising=False)
    sentinel = [{"type": "text", "text": "ASYNC-PRIVATE"}]

    async def async_builder(args: str, context: Any) -> list[dict[str, Any]]:
        return sentinel

    cmd = _make_command(async_builder)

    result = await cmd.get_prompt_for_command("", _ctx(tmp_path))

    assert result == sentinel


async def test_non_ant_user_type_takes_private_branch(monkeypatch, tmp_path):
    # Any USER_TYPE other than exactly "ant" must take the private path.
    monkeypatch.setenv("USER_TYPE", "external")
    sentinel = [{"type": "text", "text": "PRIVATE"}]
    cmd = _make_command(lambda args, context: sentinel)

    result = await cmd.get_prompt_for_command("", _ctx(tmp_path))

    assert result == sentinel


async def test_missing_builder_on_private_branch_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("USER_TYPE", raising=False)
    # Construct directly without a builder to exercise the guard.
    cmd = create_moved_to_plugin_command(
        name="no-builder",
        description="d",
        progress_message="p",
        plugin_name="x",
        plugin_command="y",
        get_prompt_while_marketplace_is_private=None,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="no private prompt builder"):
        await cmd.get_prompt_for_command("", _ctx(tmp_path))


def test_metadata():
    cmd = _make_command(lambda args, context: [])
    assert cmd.command_type == CommandType.PROMPT
    assert cmd.source == "builtin"
    assert cmd.content_length == 0
    assert cmd.user_facing_name() == "foo-cmd"
    assert cmd.name == "foo-cmd"
    assert cmd.description == "a foo command"
    assert cmd.progress_message == "fooing"
    assert cmd.plugin_name == "foo"
    assert cmd.plugin_command == "bar"
