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


# ---------------------------------------------------------------------------
# Round-2: cache-determinism — parent system prompt threading
# ---------------------------------------------------------------------------


def test_fork_uses_rendered_system_prompt_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``context.rendered_system_prompt`` is the preferred fallback chain entry.

    Mirrors ``AgentTool.tsx:496-497`` — the captured parent bytes win over
    any recomputation path.
    """
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    context.rendered_system_prompt = "PARENT_BYTES_FROM_LAST_TURN"
    # Set both lower-priority fallbacks too — the rendered field must win.
    context.options.custom_system_prompt = "SHOULD_NOT_WIN"

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["parent_system_prompt"] = params.parent_system_prompt
        captured["agent_type"] = params.agent_definition.agent_type
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    assert captured["agent_type"] == FORK_AGENT.agent_type
    assert captured["parent_system_prompt"] == "PARENT_BYTES_FROM_LAST_TURN"


def test_fork_falls_back_to_custom_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no rendered prompt is captured, ``custom_system_prompt`` wins."""
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    context.rendered_system_prompt = None
    context.options.custom_system_prompt = "CUSTOM_PROMPT_BYTES"

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["parent_system_prompt"] = params.parent_system_prompt
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    assert captured["parent_system_prompt"] == "CUSTOM_PROMPT_BYTES"


def test_fork_falls_back_to_active_agent_def_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Third fallback: recompute via the active agent definition's prompt."""
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    context.rendered_system_prompt = None
    context.options.custom_system_prompt = None
    context.agent_type = "general-purpose"

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["parent_system_prompt"] = params.parent_system_prompt
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    prompt = captured["parent_system_prompt"]
    assert isinstance(prompt, str) and prompt
    # The general-purpose agent prompt mentions "agent for Claw Codex".
    assert "agent for Claw Codex" in prompt


def test_fork_parent_system_prompt_none_when_no_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rendered prompt + no custom + no active agent_type → None.

    Lets ``get_agent_system_prompt`` fall through to ``DEFAULT_AGENT_PROMPT``
    rather than asserting.
    """
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    context.rendered_system_prompt = None
    context.options.custom_system_prompt = None
    context.agent_type = None

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["parent_system_prompt"] = params.parent_system_prompt
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    assert captured["parent_system_prompt"] is None


# ---------------------------------------------------------------------------
# Round-2: fork + worktree notice
# ---------------------------------------------------------------------------


def test_fork_appends_worktree_notice_when_worktree_root_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fork running inside a worktree appends the translation notice.

    Mirrors ``AgentTool.tsx:610-614``. The notice is the LAST user message
    so it appears as the most recent guidance the child sees.
    """
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    repo_dir = tmp_path / "repo"
    worktree_dir = tmp_path / "wt-1234"
    repo_dir.mkdir()
    worktree_dir.mkdir()
    context.cwd = repo_dir
    context.worktree_root = worktree_dir

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["context_messages"] = list(params.context_messages or [])
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    msgs = captured["context_messages"]
    assert isinstance(msgs, list) and len(msgs) >= 2
    last = msgs[-1]
    assert isinstance(last, UserMessage)
    text = last.content if isinstance(last.content, str) else "".join(
        b.text for b in last.content if isinstance(b, TextBlock)
    )
    assert str(repo_dir) in text
    assert str(worktree_dir) in text
    assert "isolated git worktree" in text
    # And the notice must NOT carry the fork-boilerplate tag so the
    # recursion-guard message scan stays unaffected.
    assert "<fork-boilerplate>" not in text


def test_fork_skips_worktree_notice_when_worktree_root_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default contexts (no worktree_root) get no notice."""
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    assert context.worktree_root is None

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["context_messages"] = list(params.context_messages or [])
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    msgs = captured["context_messages"]
    # No worktree notice means the trailing user message is the directive,
    # which DOES carry the boilerplate tag.
    last = msgs[-1]
    assert isinstance(last, UserMessage)
    blocks = list(last.content) if isinstance(last.content, list) else []
    assert any(
        isinstance(b, TextBlock) and "<fork-boilerplate>" in b.text
        for b in blocks
    )


def test_fork_skips_worktree_notice_when_root_matches_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worktree_root == cwd is a degenerate no-op; skip the notice."""
    _set_fork_env(monkeypatch, enabled=True)
    registry = build_default_registry(provider=object())
    context = _make_interactive_context(tmp_path)
    context.cwd = tmp_path
    context.worktree_root = tmp_path

    captured: dict[str, object] = {}

    async def _fake_run_agent(params):
        captured["context_messages"] = list(params.context_messages or [])
        yield AssistantMessage(content=[TextBlock(text="ok")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "test", "prompt": "go"},
            ),
            context,
        )

    msgs = captured["context_messages"]
    last = msgs[-1]
    assert isinstance(last, UserMessage)
    # The last message must still be the directive (no extra plain-text notice).
    if isinstance(last.content, list):
        assert any(
            isinstance(b, TextBlock) and "<fork-boilerplate>" in b.text
            for b in last.content
        )
    else:
        assert "<fork-boilerplate>" in last.content


def test_fork_worktree_notice_does_not_trip_recursion_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worktree notice must NOT contain the fork boilerplate tag.

    Otherwise a fork child inheriting this message would trip the
    message-scan recursion guard on its own first turn.
    """
    from src.agent.fork_subagent import build_worktree_notice, is_in_fork_child

    notice = build_worktree_notice("/repo", "/tmp/wt-1234")
    assert "<fork-boilerplate>" not in notice

    fake_msg = create_user_message(content=[TextBlock(text=notice)])
    assert is_in_fork_child([fake_msg]) is False
