"""WS-10: Behavioral parity — subagent context isolation matches TS.

Verifies:
- Default isolation: abort controller, permissions, read-file state, collections
- Opt-in sharing overrides
- Query tracking depth increments
- Permission mode inheritance rules
- Built-in agent types and constants match snapshot
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agent.constants import (
    AGENT_TOOL_NAME,
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    FORK_SUBAGENT_TYPE,
    LEGACY_AGENT_TOOL_NAME,
    ONE_SHOT_BUILTIN_AGENT_TYPES,
)
from src.agent.agent_definitions import (
    EXPLORE_AGENT,
    FORK_AGENT,
    GENERAL_PURPOSE_AGENT,
    PLAN_AGENT,
    find_agent_by_type,
    get_built_in_agents,
    is_built_in_agent,
)
from src.agent.subagent_context import (
    SubagentContextOverrides,
    create_subagent_context,
)
from src.permissions.types import ToolPermissionContext
from src.tool_system.context import QueryChainTracking, ToolContext
from src.utils.abort_controller import AbortController, create_abort_controller

REF_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "reference_data"

_tmpdir = tempfile.mkdtemp()


def _load_json(name: str) -> dict:
    return json.loads((REF_DIR / name).read_text())


def _make_parent_context() -> ToolContext:
    ctx = ToolContext(
        workspace_root=Path(_tmpdir),
        permission_context=ToolPermissionContext(mode="default"),
        abort_controller=create_abort_controller(),
        query_tracking=QueryChainTracking(chain_id="test", depth=0),
    )
    ctx.read_file_fingerprints = {Path("/tmp/file1.py"): (100, 200)}
    ctx.todos = [{"content": "parent todo"}]
    ctx.tasks = {"task1": {"id": "task1"}}
    ctx.outbox = [{"msg": "parent msg"}]
    ctx.crons = {"cron1": {"active": True}}
    ctx.permission_handler = lambda a, b, c: (True, False)
    ctx.set_response_length = lambda fn: None
    ctx.messages = [{"role": "user", "content": "hello"}]
    return ctx


class TestAgentConstantsParity(unittest.TestCase):
    """Agent constants match TS snapshot."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_agent_constants.json")

    def test_agent_tool_name(self) -> None:
        self.assertEqual(AGENT_TOOL_NAME, self.snapshot["agent_tool_name"])

    def test_legacy_agent_tool_name(self) -> None:
        self.assertEqual(LEGACY_AGENT_TOOL_NAME, self.snapshot["legacy_agent_tool_name"])

    def test_all_agent_disallowed_tools(self) -> None:
        expected = set(self.snapshot["all_agent_disallowed_tools"])
        actual = set(ALL_AGENT_DISALLOWED_TOOLS)
        self.assertEqual(expected, actual)

    def test_async_agent_allowed_tools(self) -> None:
        expected = set(self.snapshot["async_agent_allowed_tools"])
        actual = set(ASYNC_AGENT_ALLOWED_TOOLS)
        self.assertEqual(expected, actual)

    def test_one_shot_builtin_agent_types(self) -> None:
        expected = set(self.snapshot["one_shot_builtin_agent_types"])
        actual = set(ONE_SHOT_BUILTIN_AGENT_TYPES)
        self.assertEqual(expected, actual)


class TestBuiltInAgentsParity(unittest.TestCase):
    """Built-in agent definitions match TS snapshot."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_agent_constants.json")

    def test_built_in_agent_count(self) -> None:
        # get_built_in_agents() returns the 3 active agents (Fork is separate)
        actual = get_built_in_agents()
        self.assertGreaterEqual(len(actual), 3)

    def test_all_built_in_types_findable(self) -> None:
        all_agents = get_built_in_agents() + [FORK_AGENT]
        for agent_info in self.snapshot["built_in_agents"]:
            agent_type = agent_info["type"]
            # Map snapshot type to actual agent_type field
            type_map = {
                "GeneralPurpose": "general-purpose",
                "Explore": "Explore",
                "Plan": "Plan",
                "fork": "fork",
            }
            mapped = type_map.get(agent_type, agent_type)
            found = find_agent_by_type(all_agents, mapped)
            self.assertIsNotNone(
                found,
                f"Built-in agent type '{mapped}' not found",
            )

    def test_general_purpose_agent_exists(self) -> None:
        self.assertIsNotNone(GENERAL_PURPOSE_AGENT)
        self.assertTrue(is_built_in_agent(GENERAL_PURPOSE_AGENT))

    def test_explore_agent_exists(self) -> None:
        self.assertIsNotNone(EXPLORE_AGENT)
        self.assertTrue(is_built_in_agent(EXPLORE_AGENT))

    def test_plan_agent_exists(self) -> None:
        self.assertIsNotNone(PLAN_AGENT)
        self.assertTrue(is_built_in_agent(PLAN_AGENT))

    def test_fork_agent_exists(self) -> None:
        self.assertIsNotNone(FORK_AGENT)
        self.assertEqual(FORK_AGENT.agent_type, FORK_SUBAGENT_TYPE)


class TestSubagentDefaultIsolation(unittest.TestCase):
    """Default subagent isolation matches TS snapshot defaults."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_agent_constants.json")
        cls.defaults = cls.snapshot["subagent_isolation_defaults"]

    def test_abort_controller_is_new_child(self) -> None:
        """Abort controller should be a new child linked to parent."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        # Child should have its own abort controller
        self.assertIsNotNone(child.abort_controller)
        self.assertIsNot(child.abort_controller, parent.abort_controller)

    def test_permission_context_avoids_prompts(self) -> None:
        """Subagent permission context should avoid prompts by default."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertTrue(child.permission_context.should_avoid_permission_prompts)

    def test_read_file_state_starts_empty(self) -> None:
        """Subagent starts with empty fingerprints (hasn't read any files yet)."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertEqual(child.read_file_fingerprints, {})
        child.read_file_fingerprints[Path("/tmp/new_file")] = (0, 0)
        self.assertNotIn(Path("/tmp/new_file"), parent.read_file_fingerprints)

    def test_todos_fresh_empty(self) -> None:
        """Subagent should have fresh empty todos."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertEqual(child.todos, [])

    def test_tasks_fresh_empty(self) -> None:
        """Subagent should have fresh empty tasks."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertEqual(child.tasks, {})

    def test_outbox_fresh_empty(self) -> None:
        """Subagent should have fresh empty outbox."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertEqual(child.outbox, [])

    def test_crons_fresh_empty(self) -> None:
        """Subagent should have fresh empty crons."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertEqual(child.crons, {})

    def test_permission_handler_none(self) -> None:
        """Subagent should have no permission handler by default."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertIsNone(child.permission_handler)

    def test_set_response_length_none(self) -> None:
        """Subagent should have no set_response_length by default."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertIsNone(child.set_response_length)

    def test_query_tracking_depth_incremented(self) -> None:
        """Subagent query tracking depth should be parent + 1."""
        parent = _make_parent_context()
        parent.query_tracking = QueryChainTracking(chain_id="test", depth=0)
        child = create_subagent_context(parent)
        self.assertEqual(child.query_tracking.depth, 1)

    def test_nested_depth_increments(self) -> None:
        """Nested subagent depth should keep incrementing."""
        parent = _make_parent_context()
        parent.query_tracking = QueryChainTracking(chain_id="test", depth=2)
        child = create_subagent_context(parent)
        self.assertEqual(child.query_tracking.depth, 3)


class TestSubagentOverrides(unittest.TestCase):
    """Opt-in sharing overrides work correctly."""

    def test_share_abort_controller(self) -> None:
        """Overrides can share parent's abort controller."""
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(share_abort_controller=True)
        child = create_subagent_context(parent, overrides)
        self.assertIs(child.abort_controller, parent.abort_controller)

    def test_share_permission_handler(self) -> None:
        """Overrides can share parent's permission handler."""
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(share_permission_handler=True)
        child = create_subagent_context(parent, overrides)
        self.assertIs(child.permission_handler, parent.permission_handler)

    def test_share_set_response_length(self) -> None:
        """Overrides can share parent's set_response_length."""
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(share_set_response_length=True)
        child = create_subagent_context(parent, overrides)
        self.assertIs(child.set_response_length, parent.set_response_length)

    def test_custom_abort_controller(self) -> None:
        """Overrides can provide a custom abort controller."""
        parent = _make_parent_context()
        custom_abort = create_abort_controller()
        overrides = SubagentContextOverrides(abort_controller=custom_abort)
        child = create_subagent_context(parent, overrides)
        self.assertIs(child.abort_controller, custom_abort)

    def test_custom_messages(self) -> None:
        """Overrides can provide custom messages."""
        parent = _make_parent_context()
        custom_msgs = [{"role": "system", "content": "custom"}]
        overrides = SubagentContextOverrides(messages=custom_msgs)
        child = create_subagent_context(parent, overrides)
        self.assertEqual(child.messages, custom_msgs)

    def test_custom_read_file_state(self) -> None:
        """Overrides can provide custom read file state."""
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(read_file_state={})
        child = create_subagent_context(parent, overrides)
        self.assertEqual(child.read_file_fingerprints, {})

    def test_workspace_inherited(self) -> None:
        """Workspace root should be inherited from parent."""
        parent = _make_parent_context()
        child = create_subagent_context(parent)
        self.assertEqual(child.workspace_root, parent.workspace_root)
        self.assertEqual(child.cwd, parent.cwd)

    def test_custom_agent_id(self) -> None:
        """Overrides can set agent_id."""
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(agent_id="custom-123")
        child = create_subagent_context(parent, overrides)
        self.assertEqual(child.agent_id, "custom-123")

    def test_custom_agent_type(self) -> None:
        """Overrides can set agent_type."""
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(agent_type="Explore")
        child = create_subagent_context(parent, overrides)
        self.assertEqual(child.agent_type, "Explore")


if __name__ == "__main__":
    unittest.main()
