"""Tests for Agent tool fork routing.

Covers ``src/tool_system/tools/agent.py`` integration with the fork helpers:

- Disabled by default → no fork routing.
- Enabled via env var → routes to ``FORK_AGENT`` when ``subagent_type``
  is omitted.
- Recursive-fork guard rejects nested fork attempts.
- ``filter_incomplete_tool_calls`` runs on parent context messages.
- ``use_exact_tools`` bypasses ``resolve_agent_tools()``.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.fork_subagent import FORK_AGENT
from src.tool_system.context import ToolContext, ToolUseOptions
from src.tool_system.defaults import build_default_registry
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolCall
from src.types.content_blocks import (
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    create_user_message,
)


def _set_fork_env(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    if enabled:
        monkeypatch.setenv("CLAUDE_FORK_SUBAGENT", "1")
    else:
        monkeypatch.delenv("CLAUDE_FORK_SUBAGENT", raising=False)


def _make_interactive_context(tmp_path: Path) -> ToolContext:
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.options = ToolUseOptions(is_non_interactive_session=False)
    return ctx


def test_fork_routing_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_fork_env(monkeypatch, enabled=False)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["agent_type"] = params.agent_definition.agent_type
        captured["use_exact_tools"] = params.use_exact_tools
        captured["query_source"] = params.query_source
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    assert result.is_error is False
    # Without the env flag, fork is disabled — defaults to general-purpose.
    assert captured["agent_type"] == "general-purpose"
    assert captured["use_exact_tools"] is False
    assert captured["query_source"] is None


def test_fork_routing_enabled_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["agent_type"] = params.agent_definition.agent_type
        captured["use_exact_tools"] = params.use_exact_tools
        captured["query_source"] = params.query_source
        captured["context_messages"] = list(params.context_messages or [])
        yield AssistantMessage(content=[TextBlock(text="forked done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "fork test", "prompt": "build feature foo"},
            ),
            context,
        )

    assert result.is_error is False
    # With the env flag and no subagent_type, the fork path is selected.
    assert captured["agent_type"] == FORK_AGENT.agent_type
    assert captured["use_exact_tools"] is True
    assert captured["query_source"] == "agent:builtin:fork"

    # The forked-message pair should have been appended to the context.
    msgs = captured["context_messages"]
    assert isinstance(msgs, list)
    assert len(msgs) >= 1
    # Last message must contain the boilerplate-wrapped directive.
    last = msgs[-1]
    assert isinstance(last, UserMessage)
    text_blocks = [b for b in last.content if isinstance(b, TextBlock)]
    assert any("<fork-boilerplate>" in b.text for b in text_blocks)
    assert any("Your directive: build feature foo" in b.text for b in text_blocks)


def test_fork_recursive_guard_via_query_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    # Simulate that this context is already a fork child.
    context.options.query_source = "agent:builtin:fork"

    with pytest.raises(ToolInputError) as excinfo:
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "x", "prompt": "spawn another"},
            ),
            context,
        )
    assert "fork" in str(excinfo.value).lower()


def test_fork_recursive_guard_via_message_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    # Simulate the parent conversation already containing a fork tag.
    context.messages = [
        create_user_message(content=[TextBlock(text="<fork-boilerplate>...</fork-boilerplate>")]),
    ]

    with pytest.raises(ToolInputError) as excinfo:
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "x", "prompt": "spawn another"},
            ),
            context,
        )
    assert "fork" in str(excinfo.value).lower()


def test_fork_routing_includes_parent_assistant_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)

    parent_assistant = AssistantMessage(
        content=[
            ToolUseBlock(id="tu_1", name="Read", input={"path": "/a"}),
            ToolUseBlock(id="tu_2", name="Read", input={"path": "/b"}),
        ]
    )
    context.messages = [
        create_user_message(content=[TextBlock(text="planning step")]),
        parent_assistant,
    ]

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["context_messages"] = list(params.context_messages or [])
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "rewrite a"},
            ),
            context,
        )

    msgs = captured["context_messages"]
    assert isinstance(msgs, list)
    # context_messages should be: [original parent messages..., cloned_assistant, user_with_directive]
    assert len(msgs) >= 4
    cloned_assistant_candidate = msgs[-2]
    assert isinstance(cloned_assistant_candidate, AssistantMessage)
    # Cloned assistant must preserve all tool_use block IDs.
    block_ids = [
        b.id for b in cloned_assistant_candidate.content if isinstance(b, ToolUseBlock)
    ]
    assert block_ids == ["tu_1", "tu_2"]
    # The cloned assistant should not be the same Python object as the parent's.
    assert cloned_assistant_candidate is not parent_assistant

    user_msg = msgs[-1]
    assert isinstance(user_msg, UserMessage)
    result_block_ids = {
        b.tool_use_id for b in user_msg.content if isinstance(b, ToolResultBlock)
    }
    assert result_block_ids == {"tu_1", "tu_2"}


def test_fork_disabled_in_non_interactive_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    context.options.is_non_interactive_session = True

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["agent_type"] = params.agent_definition.agent_type
        captured["use_exact_tools"] = params.use_exact_tools
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    # Even with the env flag, non-interactive sessions skip the fork path.
    assert captured["agent_type"] == "general-purpose"
    assert captured["use_exact_tools"] is False
