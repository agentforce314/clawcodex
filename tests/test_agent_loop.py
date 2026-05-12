"""Tests for helpers in src.tool_system.agent_loop and src.agent.

The loop-direct tests for the legacy ``run_agent_loop`` function were
removed when that function was deleted. Equivalent coverage for the
canonical loop's adapter lives in ``tests/test_query_agent_loop_compat.py``.
The tests in this file cover the surviving helper surface:
``filter_incomplete_tool_calls``, agent-definition lookups, and the
agent prompt builder.
"""

import unittest

class TestFilterIncompleteToolCalls(unittest.TestCase):
    """Test filter_incomplete_tool_calls from run_agent.py."""

    def test_empty_messages(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        assert filter_incomplete_tool_calls([]) == []

    def test_no_trailing_tool_use(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import TextBlock

        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="hi there")]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 2

    def test_trailing_tool_use_removed(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import TextBlock, ToolUseBlock

        msgs = [
            UserMessage(content="hello"),
            AssistantMessage(content=[TextBlock(text="let me search")]),
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a"}),
            ]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        # Trailing assistant with orphaned tool_use removed
        assert len(result) == 2

    def test_string_content_not_removed(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage

        msgs = [
            AssistantMessage(content="just text, no tool_use"),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 1

    def test_matched_tool_use_is_kept(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import ToolUseBlock, ToolResultBlock

        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="t1", content="ok"),
            ]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 2

    def test_unmatched_tool_use_removed_even_if_not_trailing(self):
        from src.agent.run_agent import filter_incomplete_tool_calls
        from src.types.messages import AssistantMessage, UserMessage
        from src.types.content_blocks import ToolUseBlock, TextBlock

        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"path": "/tmp/a"}),
            ]),
            UserMessage(content="no tool results here"),
            AssistantMessage(content=[TextBlock(text="final summary")]),
        ]
        result = filter_incomplete_tool_calls(msgs)
        assert len(result) == 2
        assert isinstance(result[0], UserMessage)
        assert isinstance(result[1], AssistantMessage)


class TestAgentDefinitions(unittest.TestCase):
    """Test agent definition module."""

    def test_get_built_in_agents(self):
        from src.agent.agent_definitions import get_built_in_agents

        agents = get_built_in_agents()
        assert len(agents) >= 3  # general-purpose, explore, plan
        types = {a.agent_type for a in agents}
        assert "general-purpose" in types
        assert "Explore" in types
        assert "Plan" in types

    def test_find_agent_by_type(self):
        from src.agent.agent_definitions import find_agent_by_type, get_built_in_agents

        agents = get_built_in_agents()
        gp = find_agent_by_type(agents, "general-purpose")
        assert gp is not None
        assert gp.agent_type == "general-purpose"
        assert gp.tools == ["*"]

    def test_find_agent_by_type_not_found(self):
        from src.agent.agent_definitions import find_agent_by_type, get_built_in_agents

        agents = get_built_in_agents()
        result = find_agent_by_type(agents, "nonexistent-agent")
        assert result is None

    def test_is_built_in_agent(self):
        from src.agent.agent_definitions import (
            is_built_in_agent,
            GENERAL_PURPOSE_AGENT,
            AgentDefinition,
        )

        assert is_built_in_agent(GENERAL_PURPOSE_AGENT) is True
        custom = AgentDefinition(
            agent_type="custom", when_to_use="custom", source="user"
        )
        assert is_built_in_agent(custom) is False


class TestAgentPrompt(unittest.TestCase):
    """Test agent prompt generation."""

    def test_get_agent_prompt(self):
        from src.agent.prompt import get_agent_prompt
        from src.agent.agent_definitions import get_built_in_agents

        agents = get_built_in_agents()
        prompt = get_agent_prompt(agents)

        assert "Available agent types" in prompt
        assert "general-purpose" in prompt
        assert "When NOT to use" in prompt
        assert "Writing the prompt" in prompt

    def test_format_agent_line(self):
        from src.agent.prompt import format_agent_line
        from src.agent.agent_definitions import EXPLORE_AGENT

        line = format_agent_line(EXPLORE_AGENT)
        assert "Explore" in line
        assert "Tools:" in line

    def test_get_agent_system_prompt_built_in(self):
        from src.agent.prompt import get_agent_system_prompt
        from src.agent.agent_definitions import GENERAL_PURPOSE_AGENT

        prompt = get_agent_system_prompt(GENERAL_PURPOSE_AGENT)
        assert "agent" in prompt.lower()
        assert len(prompt) > 50

    def test_get_agent_system_prompt_fork_inherits_parent(self):
        from src.agent.prompt import get_agent_system_prompt
        from src.agent.agent_definitions import FORK_AGENT

        parent_prompt = "You are the parent system prompt."
        prompt = get_agent_system_prompt(FORK_AGENT, parent_system_prompt=parent_prompt)
        assert prompt == parent_prompt


if __name__ == "__main__":
    unittest.main()
