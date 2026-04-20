"""WS-10: E2E integration — agent spawn flow matches TS behavior.

Simulates: AgentTool dispatched → child agent context created → result returned.
Tests the agent tool dispatch, subagent context creation, and tool filtering.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.agent.agent_definitions import (
    EXPLORE_AGENT,
    GENERAL_PURPOSE_AGENT,
    PLAN_AGENT,
    find_agent_by_type,
    get_built_in_agents,
)
from src.agent.agent_tool_utils import (
    filter_tools_for_agent,
    resolve_agent_tools,
)
from src.agent.constants import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
)
from src.agent.subagent_context import (
    SubagentContextOverrides,
    create_subagent_context,
)
from src.permissions.types import ToolPermissionContext
from src.tool_system.build_tool import build_tool, Tool
from src.tool_system.context import QueryChainTracking, ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.utils.abort_controller import create_abort_controller


def _make_tool(name: str, *, concurrent: bool = False) -> Tool:
    return build_tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        call=lambda inp, ctx: None,
        is_concurrency_safe=lambda _: concurrent,
        is_read_only=lambda _: concurrent,
    )


class TestE2EAgentToolDispatch(unittest.TestCase):
    """Agent tool dispatch returns error when no provider available."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_agent_tool_registered(self) -> None:
        """Agent tool is registered in the default registry."""
        tool = self.registry.get("Agent")
        self.assertIsNotNone(tool)

    def test_agent_tool_requires_prompt(self) -> None:
        """Agent tool requires a prompt input."""
        tool = self.registry.get("Agent")
        schema = tool.input_schema
        self.assertIn("prompt", schema.get("properties", {}))
        self.assertIn("prompt", schema.get("required", []))

    def test_agent_dispatch_no_provider_returns_error(self) -> None:
        """Agent tool without provider returns error."""
        result = self.registry.dispatch(
            ToolCall(name="Agent", input={
                "prompt": "Search for hello",
            }),
            self.ctx,
        )
        self.assertTrue(result.is_error)

    def test_agent_tool_alias_task(self) -> None:
        """Agent tool is accessible via 'Task' alias."""
        tool = self.registry.get("Task")
        self.assertIsNotNone(tool)


class TestE2EAgentContextCreation(unittest.TestCase):
    """Subagent context creation for agent spawn."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_spawn_creates_isolated_context(self) -> None:
        """Spawning an agent creates an isolated context."""
        parent = ToolContext(
            workspace_root=self.root,
            permission_context=ToolPermissionContext(mode="default"),
            abort_controller=create_abort_controller(),
            query_tracking=QueryChainTracking(chain_id="test", depth=0),
            messages=[{"role": "user", "content": "test"}],
        )
        parent.todos = [{"content": "parent todo"}]

        child = create_subagent_context(parent, SubagentContextOverrides(
            agent_type="Explore",
        ))

        # Isolated collections
        self.assertEqual(child.todos, [])
        self.assertEqual(child.tasks, {})
        self.assertEqual(child.outbox, [])

        # Workspace inherited
        self.assertEqual(child.workspace_root, parent.workspace_root)

        # Depth incremented
        self.assertEqual(child.query_tracking.depth, 1)

        # Agent type set
        self.assertEqual(child.agent_type, "Explore")

    def test_spawn_with_agent_id(self) -> None:
        """Spawning an agent can set a specific agent_id."""
        parent = ToolContext(
            workspace_root=self.root,
            abort_controller=create_abort_controller(),
            query_tracking=QueryChainTracking(chain_id="test", depth=0),
            messages=[],
        )
        child = create_subagent_context(parent, SubagentContextOverrides(
            agent_id="agent-123",
            agent_type="general-purpose",
        ))
        self.assertEqual(child.agent_id, "agent-123")
        self.assertEqual(child.agent_type, "general-purpose")

    def test_nested_spawn_depth_increments(self) -> None:
        """Nested agent spawns increment depth correctly."""
        root_ctx = ToolContext(
            workspace_root=self.root,
            abort_controller=create_abort_controller(),
            query_tracking=QueryChainTracking(chain_id="test", depth=0),
            messages=[],
        )
        child1 = create_subagent_context(root_ctx)
        self.assertEqual(child1.query_tracking.depth, 1)

        child2 = create_subagent_context(child1)
        self.assertEqual(child2.query_tracking.depth, 2)

        child3 = create_subagent_context(child2)
        self.assertEqual(child3.query_tracking.depth, 3)


class TestE2EAgentToolFiltering(unittest.TestCase):
    """Agent tool filtering matches TS disallowed/allowed lists."""

    def test_disallowed_tools_filtered(self) -> None:
        """ALL_AGENT_DISALLOWED_TOOLS are removed from agent toolkits."""
        all_tools = [_make_tool(name) for name in [
            "Read", "Edit", "Agent", "AskUserQuestion", "TaskOutput",
            "ExitPlanMode", "EnterPlanMode", "Bash", "Brief", "TaskStop",
        ]]
        filtered = filter_tools_for_agent(tools=all_tools, is_built_in=True, is_async=False)
        filtered_names = {t.name for t in filtered}

        for disallowed in ALL_AGENT_DISALLOWED_TOOLS:
            self.assertNotIn(
                disallowed, filtered_names,
                f"Disallowed tool '{disallowed}' should be filtered out",
            )

    def test_async_agent_whitelist(self) -> None:
        """Async agents only get ASYNC_AGENT_ALLOWED_TOOLS."""
        all_tools = [_make_tool(name) for name in [
            "Read", "Edit", "Bash", "Write", "Glob", "Grep",
            "WebSearch", "WebFetch", "Agent", "AskUserQuestion",
        ]]
        filtered = filter_tools_for_agent(tools=all_tools, is_built_in=True, is_async=True)
        filtered_names = {t.name for t in filtered}

        for name in filtered_names:
            self.assertIn(
                name, ASYNC_AGENT_ALLOWED_TOOLS,
                f"Async agent should only have allowed tools, but has '{name}'",
            )

    def test_mcp_tools_always_allowed(self) -> None:
        """MCP tools are always allowed for agents."""
        mcp_tool = build_tool(
            name="mcp__server__tool",
            input_schema={"type": "object", "properties": {}},
            call=lambda inp, ctx: None,
            is_mcp=True,
        )
        all_tools = [_make_tool("Read"), mcp_tool]
        filtered = filter_tools_for_agent(tools=all_tools, is_built_in=True, is_async=False)
        filtered_names = {t.name for t in filtered}
        self.assertIn("mcp__server__tool", filtered_names)


class TestE2EAgentDefinitionLookup(unittest.TestCase):
    """Agent definition lookup for spawn."""

    def test_find_explore_agent(self) -> None:
        agents = get_built_in_agents()
        found = find_agent_by_type(agents, "Explore")
        self.assertIsNotNone(found)
        self.assertEqual(found.agent_type, "Explore")

    def test_find_plan_agent(self) -> None:
        agents = get_built_in_agents()
        found = find_agent_by_type(agents, "Plan")
        self.assertIsNotNone(found)
        self.assertEqual(found.agent_type, "Plan")

    def test_find_general_purpose_agent(self) -> None:
        agents = get_built_in_agents()
        found = find_agent_by_type(agents, "general-purpose")
        self.assertIsNotNone(found)
        self.assertEqual(found.agent_type, "general-purpose")

    def test_unknown_agent_type_returns_none(self) -> None:
        agents = get_built_in_agents()
        found = find_agent_by_type(agents, "nonexistent-type")
        self.assertIsNone(found)

    def test_explore_agent_has_search_tools(self) -> None:
        """Explore agent should have search tools."""
        self.assertIn("Read", EXPLORE_AGENT.tools)
        self.assertIn("Glob", EXPLORE_AGENT.tools)
        self.assertIn("Grep", EXPLORE_AGENT.tools)

    def test_general_purpose_has_wildcard_tools(self) -> None:
        """General purpose agent should have wildcard tools."""
        self.assertIn("*", GENERAL_PURPOSE_AGENT.tools)


if __name__ == "__main__":
    unittest.main()
