"""Tests for agent toolkit filtering and resolution.

Validates filter_tools_for_agent() and resolve_agent_tools() from src/agent/agent_tool_utils.py.
"""
from __future__ import annotations

import pytest

from src.agent.agent_definitions import (
    AgentDefinition,
    EXPLORE_AGENT,
    GENERAL_PURPOSE_AGENT,
)
from src.agent.agent_tool_utils import (
    ResolvedAgentTools,
    filter_tools_for_agent,
    resolve_agent_tools,
    count_tool_uses,
    extract_partial_result,
    finalize_agent_tool,
    _extract_tool_name,
    _extract_rule_content,
)
from src.agent.constants import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
)
from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.protocol import ToolResult
from src.types.content_blocks import TextBlock, ToolUseBlock
from src.types.messages import AssistantMessage, UserMessage


def _make_tool(name: str, is_mcp: bool = False) -> Tool:
    """Create a minimal Tool for testing."""
    return build_tool(
        name=name,
        input_schema={"type": "object"},
        call=lambda _input, _ctx: ToolResult(name=name, output="ok"),
        is_mcp=is_mcp,
    )


def _make_tools(*names: str, mcp_names: list[str] | None = None) -> list[Tool]:
    """Create a list of tools by name, with optional MCP tools."""
    tools = [_make_tool(n) for n in names]
    if mcp_names:
        tools.extend(_make_tool(n, is_mcp=True) for n in mcp_names)
    return tools


# --- filter_tools_for_agent ---

class TestFilterToolsForAgent:
    def test_mcp_tools_always_allowed(self):
        """MCP tools pass through regardless of agent type."""
        tools = _make_tools("Read", "Write", mcp_names=["mcp__github"])
        
        result = filter_tools_for_agent(
            tools=tools, is_built_in=True, is_async=True,
        )
        
        names = {t.name for t in result}
        assert "mcp__github" in names

    def test_all_agent_disallowed_blocked(self):
        """ALL_AGENT_DISALLOWED_TOOLS are always blocked."""
        tool_names = ["Read", "Write", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "TaskOutput", "Agent"]
        tools = _make_tools(*tool_names)
        
        result = filter_tools_for_agent(tools=tools, is_built_in=True)
        
        names = {t.name for t in result}
        for blocked in ALL_AGENT_DISALLOWED_TOOLS:
            assert blocked not in names, f"{blocked} should be blocked"

    def test_read_write_allowed_for_sync(self):
        """Basic tools like Read and Write are allowed for sync agents."""
        tools = _make_tools("Read", "Write", "Grep")
        
        result = filter_tools_for_agent(tools=tools, is_built_in=True)
        
        names = {t.name for t in result}
        assert "Read" in names
        assert "Write" in names
        assert "Grep" in names

    def test_custom_agent_disallowed_blocked(self):
        """CUSTOM_AGENT_DISALLOWED_TOOLS blocked for non-built-in agents."""
        tools = _make_tools("Read", "AskUserQuestion", "TaskStop")
        
        result = filter_tools_for_agent(tools=tools, is_built_in=False)
        
        names = {t.name for t in result}
        for blocked in CUSTOM_AGENT_DISALLOWED_TOOLS:
            assert blocked not in names

    def test_built_in_not_custom_blocked(self):
        """Built-in agents don't get CUSTOM_AGENT_DISALLOWED_TOOLS filtering
        (though they overlap with ALL_AGENT_DISALLOWED_TOOLS)."""
        # All agent disallowed should still be blocked
        tools = _make_tools("Read", "AskUserQuestion")

        result_builtin = filter_tools_for_agent(tools=tools, is_built_in=True)
        result_custom = filter_tools_for_agent(tools=tools, is_built_in=False)

        # Both should block AskUserQuestion (it's in ALL_AGENT_DISALLOWED)
        assert "AskUserQuestion" not in {t.name for t in result_builtin}
        assert "AskUserQuestion" not in {t.name for t in result_custom}

    def test_async_whitelist_only(self):
        """Async agents only get ASYNC_AGENT_ALLOWED_TOOLS."""
        tools = _make_tools("Read", "Write", "Grep", "Bash", "Config", "Sleep")

        result = filter_tools_for_agent(
            tools=tools, is_built_in=True, is_async=True,
        )

        names = {t.name for t in result}
        for name in names:
            assert name in ASYNC_AGENT_ALLOWED_TOOLS, f"{name} should not be in async whitelist"

    def test_async_mcp_still_allowed(self):
        """MCP tools pass even for async agents."""
        tools = _make_tools("Config", mcp_names=["mcp__github"])

        result = filter_tools_for_agent(
            tools=tools, is_built_in=True, is_async=True,
        )

        names = {t.name for t in result}
        assert "mcp__github" in names

    def test_exit_plan_mode_allowed_in_plan(self):
        """ExitPlanMode is allowed for agents in plan mode."""
        tools = _make_tools("Read", "ExitPlanMode")

        result = filter_tools_for_agent(
            tools=tools, is_built_in=True, permission_mode="plan",
        )

        names = {t.name for t in result}
        assert "ExitPlanMode" in names

    def test_exit_plan_mode_blocked_in_default(self):
        """ExitPlanMode is blocked in default mode (it's in ALL_AGENT_DISALLOWED)."""
        tools = _make_tools("Read", "ExitPlanMode")

        result = filter_tools_for_agent(
            tools=tools, is_built_in=True, permission_mode="default",
        )

        names = {t.name for t in result}
        assert "ExitPlanMode" not in names


# --- resolve_agent_tools ---

class TestResolveAgentTools:
    def test_wildcard_gets_all_filtered(self):
        """['*'] means all tools after filtering."""
        tools = _make_tools("Read", "Write", "Grep", "AskUserQuestion")

        result = resolve_agent_tools(GENERAL_PURPOSE_AGENT, tools)

        assert result.has_wildcard is True
        names = {t.name for t in result.resolved_tools}
        assert "Read" in names
        assert "Write" in names
        assert "Grep" in names
        assert "AskUserQuestion" not in names  # Blocked

    def test_specific_tools_validated(self):
        """Specific tool lists are validated against available tools."""
        agent = AgentDefinition(
            agent_type="test",
            when_to_use="test",
            tools=["Read", "Grep"],
            source="built-in",
        )
        tools = _make_tools("Read", "Write", "Grep")

        result = resolve_agent_tools(agent, tools)

        assert result.has_wildcard is False
        assert result.valid_tools == ["Read", "Grep"]
        assert result.invalid_tools == []
        names = {t.name for t in result.resolved_tools}
        assert names == {"Read", "Grep"}

    def test_invalid_tools_reported(self):
        """Unknown tools in agent definition are reported as invalid."""
        agent = AgentDefinition(
            agent_type="test",
            when_to_use="test",
            tools=["Read", "NonExistentTool"],
            source="built-in",
        )
        tools = _make_tools("Read", "Write")

        result = resolve_agent_tools(agent, tools)

        assert "NonExistentTool" in result.invalid_tools
        assert "Read" in result.valid_tools

    def test_disallowed_tools_filtered(self):
        """Agent-level disallowedTools blocks specific tools."""
        agent = AgentDefinition(
            agent_type="test",
            when_to_use="test",
            tools=["*"],
            disallowed_tools=["Write"],
            source="built-in",
        )
        tools = _make_tools("Read", "Write", "Grep")

        result = resolve_agent_tools(agent, tools)

        names = {t.name for t in result.resolved_tools}
        assert "Write" not in names
        assert "Read" in names
        assert "Grep" in names

    def test_async_filtering_applied(self):
        """Async flag applies async whitelist filtering."""
        agent = AgentDefinition(
            agent_type="test",
            when_to_use="test",
            tools=["*"],
            source="built-in",
        )
        tools = _make_tools("Read", "Write", "Config", "Sleep")

        result = resolve_agent_tools(agent, tools, is_async=True)

        names = {t.name for t in result.resolved_tools}
        assert "Read" in names
        assert "Write" in names
        # Config and Sleep are not in ASYNC_AGENT_ALLOWED_TOOLS
        assert "Config" not in names
        assert "Sleep" not in names

    def test_explore_agent_tools(self):
        """EXPLORE_AGENT uses disallowed_tools (denylist) — tools=None means wildcard."""
        tools = _make_tools("Read", "Glob", "Grep", "Bash", "Write", "Edit", "Agent")

        result = resolve_agent_tools(EXPLORE_AGENT, tools)

        # EXPLORE_AGENT has tools=None (wildcard) with disallowed_tools
        assert result.has_wildcard is True
        names = {t.name for t in result.resolved_tools}
        assert "Read" in names
        assert "Glob" in names
        assert "Grep" in names
        assert "Bash" in names
        # disallowed_tools should be filtered out
        assert "Agent" not in names
        assert "Edit" not in names
        assert "Write" not in names

    def test_none_tools_means_wildcard(self):
        """Agent with tools=None gets all tools."""
        agent = AgentDefinition(
            agent_type="test",
            when_to_use="test",
            tools=None,
            source="built-in",
        )
        tools = _make_tools("Read", "Write")

        result = resolve_agent_tools(agent, tools)

        assert result.has_wildcard is True


# --- Helper functions ---

class TestHelpers:
    def test_extract_tool_name_simple(self):
        assert _extract_tool_name("Read") == "Read"

    def test_extract_tool_name_with_parens(self):
        assert _extract_tool_name("Agent(general-purpose, explore)") == "Agent"

    def test_extract_rule_content(self):
        assert _extract_rule_content("Agent(general-purpose, explore)") == "general-purpose, explore"

    def test_extract_rule_content_none(self):
        assert _extract_rule_content("Read") is None


# --- count_tool_uses ---

class TestCountToolUses:
    def test_empty_messages(self):
        assert count_tool_uses([]) == 0

    def test_no_tool_uses(self):
        msgs = [AssistantMessage(content="Hello")]
        assert count_tool_uses(msgs) == 0

    def test_with_tool_uses(self):
        msgs = [
            AssistantMessage(content=[
                TextBlock(text="Let me search"),
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a.py"}),
                ToolUseBlock(id="t2", name="Grep", input={"query": "hello"}),
            ]),
            AssistantMessage(content=[
                ToolUseBlock(id="t3", name="Write", input={"path": "/tmp/b.py", "content": "hi"}),
            ]),
        ]
        assert count_tool_uses(msgs) == 3


# --- extract_partial_result ---

class TestExtractPartialResult:
    def test_empty_messages(self):
        assert extract_partial_result([]) is None

    def test_text_content(self):
        msgs = [AssistantMessage(content="Found the bug in line 42")]
        assert extract_partial_result(msgs) == "Found the bug in line 42"

    def test_block_content(self):
        msgs = [
            AssistantMessage(content=[
                TextBlock(text="Result: success"),
            ]),
        ]
        assert extract_partial_result(msgs) == "Result: success"

    def test_last_assistant_preferred(self):
        msgs = [
            AssistantMessage(content="First response"),
            UserMessage(content="Continue"),
            AssistantMessage(content="Second response"),
        ]
        assert extract_partial_result(msgs) == "Second response"

    def test_empty_content_skipped(self):
        msgs = [
            AssistantMessage(content="Good response"),
            AssistantMessage(content=""),
        ]
        assert extract_partial_result(msgs) == "Good response"


# --- finalize_agent_tool ---

class TestFinalizeAgentTool:
    def test_basic_finalize(self):
        import time
        msgs = [
            AssistantMessage(content=[
                TextBlock(text="Task completed successfully"),
            ]),
        ]
        result = finalize_agent_tool(msgs, "agent-1", {
            "start_time": time.time(),
            "agent_type": "general-purpose",
        })

        assert result.agent_id == "agent-1"
        assert result.agent_type == "general-purpose"
        assert len(result.content) == 1
        assert result.content[0]["type"] == "text"
        assert result.content[0]["text"] == "Task completed successfully"

    def test_no_assistant_messages_raises(self):
        with pytest.raises(ValueError, match="No assistant messages"):
            finalize_agent_tool([], "agent-1", {"start_time": 0, "agent_type": "test"})

    def test_tool_use_count(self):
        import time
        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={}),
            ]),
            UserMessage(content="result"),
            AssistantMessage(content=[
                TextBlock(text="Done"),
                ToolUseBlock(id="t2", name="Write", input={}),
            ]),
            UserMessage(content="result2"),
            AssistantMessage(content=[TextBlock(text="Final")]),
        ]
        result = finalize_agent_tool(msgs, "a1", {
            "start_time": time.time(),
            "agent_type": "test",
        })
        assert result.total_tool_use_count == 2
