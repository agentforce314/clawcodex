"""Tests for src/agent/subagent_context.py — context isolation."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from src.agent.subagent_context import (
    SubagentContextOverrides,
    create_subagent_context,
)
from src.permissions.types import ToolPermissionContext
from src.tool_system.context import QueryChainTracking, ToolContext
from src.utils.abort_controller import AbortController, create_child_abort_controller


def _make_parent_context(**kwargs) -> ToolContext:
    """Create a minimal parent ToolContext for testing."""
    defaults = dict(
        workspace_root=Path("/tmp/test-ws"),
        permission_context=ToolPermissionContext(mode="default"),
        abort_controller=AbortController(),
    )
    defaults.update(kwargs)
    return ToolContext(**defaults)


# --- Default isolation ---

class TestDefaultIsolation:
    def test_read_file_fingerprints_starts_empty(self):
        parent = _make_parent_context()
        parent.read_file_fingerprints[Path("/tmp/a.py")] = (100, 200)

        child = create_subagent_context(parent)

        assert dict(child.read_file_fingerprints) == {}
        child.read_file_fingerprints[Path("/tmp/b.py")] = (300, 400)
        assert Path("/tmp/b.py") not in parent.read_file_fingerprints

    def test_todos_fresh(self):
        parent = _make_parent_context()
        parent.todos = [{"text": "parent todo"}]

        child = create_subagent_context(parent)

        assert child.todos == []

    def test_tasks_fresh(self):
        parent = _make_parent_context()
        parent.tasks = {"t1": {"name": "task1"}}

        child = create_subagent_context(parent)

        assert child.tasks == {}

    def test_outbox_fresh(self):
        parent = _make_parent_context()
        parent.outbox = [{"msg": "hello"}]

        child = create_subagent_context(parent)

        assert child.outbox == []

    def test_ask_user_is_none(self):
        parent = _make_parent_context()
        parent.ask_user = lambda questions: {"answer": "yes"}

        child = create_subagent_context(parent)

        assert child.ask_user is None

    def test_set_response_length_is_none(self):
        parent = _make_parent_context()
        parent.set_response_length = lambda fn: None

        child = create_subagent_context(parent)

        assert child.set_response_length is None

    def test_set_in_progress_tool_use_ids_is_none(self):
        parent = _make_parent_context()
        parent.set_in_progress_tool_use_ids = lambda fn: None

        child = create_subagent_context(parent)

        assert child.set_in_progress_tool_use_ids is None


# --- Abort controller ---

class TestAbortController:
    def test_default_creates_child_linked_to_parent(self):
        parent = _make_parent_context()

        child = create_subagent_context(parent)

        # Child has its own abort controller
        assert child.abort_controller is not parent.abort_controller
        # But parent abort propagates to child
        parent.abort_controller.abort("test")
        assert child.abort_controller.signal.aborted is True
        assert child.abort_controller.signal.reason == "test"

    def test_share_abort_controller(self):
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(share_abort_controller=True)

        child = create_subagent_context(parent, overrides)

        assert child.abort_controller is parent.abort_controller

    def test_explicit_abort_controller_override(self):
        parent = _make_parent_context()
        custom_controller = AbortController()
        overrides = SubagentContextOverrides(abort_controller=custom_controller)

        child = create_subagent_context(parent, overrides)

        assert child.abort_controller is custom_controller
        # Parent abort doesn't affect child (they're independent)
        parent.abort_controller.abort("parent")
        assert child.abort_controller.signal.aborted is False


# --- Permission context ---

class TestPermissionContext:
    def test_default_avoids_permission_prompts(self):
        parent = _make_parent_context(
            permission_context=ToolPermissionContext(
                mode="default",
                should_avoid_permission_prompts=False,
            ),
        )

        child = create_subagent_context(parent)

        assert child.permission_context.should_avoid_permission_prompts is True
        assert child.permission_context.mode == "default"

    def test_already_avoiding_prompts_preserved(self):
        parent = _make_parent_context(
            permission_context=ToolPermissionContext(
                mode="bypassPermissions",
                should_avoid_permission_prompts=True,
            ),
        )

        child = create_subagent_context(parent)

        assert child.permission_context.should_avoid_permission_prompts is True
        assert child.permission_context.mode == "bypassPermissions"

    def test_share_abort_shares_permission_context(self):
        parent = _make_parent_context(
            permission_context=ToolPermissionContext(
                mode="default",
                should_avoid_permission_prompts=False,
            ),
        )
        overrides = SubagentContextOverrides(share_abort_controller=True)

        child = create_subagent_context(parent, overrides)

        # Interactive agents share the parent's permission context
        assert child.permission_context is parent.permission_context

    def test_explicit_permission_context_override(self):
        parent = _make_parent_context()
        custom_perm = ToolPermissionContext(mode="plan")
        overrides = SubagentContextOverrides(permission_context=custom_perm)

        child = create_subagent_context(parent, overrides)

        assert child.permission_context is custom_perm


# --- Query tracking ---

class TestQueryTracking:
    def test_depth_incremented(self):
        parent = _make_parent_context()
        parent.query_tracking = QueryChainTracking(chain_id="parent", depth=0)

        child = create_subagent_context(parent)

        assert child.query_tracking is not None
        assert child.query_tracking.depth == 1
        assert child.query_tracking.chain_id != "parent"

    def test_depth_from_none_parent(self):
        parent = _make_parent_context()
        parent.query_tracking = None

        child = create_subagent_context(parent)

        assert child.query_tracking is not None
        assert child.query_tracking.depth == 0

    def test_nested_depth(self):
        parent = _make_parent_context()
        parent.query_tracking = QueryChainTracking(chain_id="p", depth=5)

        child = create_subagent_context(parent)

        assert child.query_tracking.depth == 6


# --- Overrides ---

class TestOverrides:
    def test_agent_id_override(self):
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(agent_id="custom-id")

        child = create_subagent_context(parent, overrides)

        assert child.agent_id == "custom-id"

    def test_agent_type_override(self):
        parent = _make_parent_context()
        overrides = SubagentContextOverrides(agent_type="explore")

        child = create_subagent_context(parent, overrides)

        assert child.agent_type == "explore"

    def test_messages_override(self):
        parent = _make_parent_context()
        parent.messages = [{"role": "user", "content": "parent msg"}]
        custom_msgs = [{"role": "user", "content": "custom msg"}]
        overrides = SubagentContextOverrides(messages=custom_msgs)

        child = create_subagent_context(parent, overrides)

        assert child.messages == custom_msgs

    def test_read_file_state_override(self):
        parent = _make_parent_context()
        parent.read_file_fingerprints[Path("/tmp/a.py")] = (100, 200)
        custom_state = {Path("/tmp/b.py"): (300, 400)}
        overrides = SubagentContextOverrides(read_file_state=custom_state)

        child = create_subagent_context(parent, overrides)

        assert child.read_file_fingerprints == {Path("/tmp/b.py"): (300, 400)}

    def test_share_permission_handler(self):
        handler = lambda name, msg, sug: (True, False)
        parent = _make_parent_context()
        parent.permission_handler = handler
        overrides = SubagentContextOverrides(share_permission_handler=True)

        child = create_subagent_context(parent, overrides)

        assert child.permission_handler is handler

    def test_default_no_permission_handler(self):
        handler = lambda name, msg, sug: (True, False)
        parent = _make_parent_context()
        parent.permission_handler = handler

        child = create_subagent_context(parent)

        assert child.permission_handler is None

    def test_share_set_response_length(self):
        fn = lambda fn: None
        parent = _make_parent_context()
        parent.set_response_length = fn
        overrides = SubagentContextOverrides(share_set_response_length=True)

        child = create_subagent_context(parent, overrides)

        assert child.set_response_length is fn

    def test_content_replacement_state_cloned(self):
        parent = _make_parent_context()
        parent.content_replacement_state = {"a": 1, "b": [2, 3]}

        child = create_subagent_context(parent)

        assert child.content_replacement_state == {"a": 1, "b": [2, 3]}
        # Should be a clone, not same reference
        assert child.content_replacement_state is not parent.content_replacement_state

    def test_content_replacement_state_override(self):
        parent = _make_parent_context()
        parent.content_replacement_state = {"old": True}
        overrides = SubagentContextOverrides(content_replacement_state={"new": True})

        child = create_subagent_context(parent, overrides)

        assert child.content_replacement_state == {"new": True}

    def test_content_replacement_state_none_parent(self):
        parent = _make_parent_context()
        parent.content_replacement_state = None

        child = create_subagent_context(parent)

        assert child.content_replacement_state is None


# --- Workspace inheritance ---

class TestWorkspaceInheritance:
    def test_workspace_root_inherited(self):
        parent = _make_parent_context()

        child = create_subagent_context(parent)

        assert child.workspace_root == parent.workspace_root

    def test_cwd_inherited(self):
        parent = _make_parent_context()

        child = create_subagent_context(parent)

        assert child.cwd == parent.cwd

    def test_file_reading_limits_inherited(self):
        from src.tool_system.context import FileReadingLimits
        parent = _make_parent_context()
        parent.file_reading_limits = FileReadingLimits(max_tokens=1000)

        child = create_subagent_context(parent)

        assert child.file_reading_limits is parent.file_reading_limits
