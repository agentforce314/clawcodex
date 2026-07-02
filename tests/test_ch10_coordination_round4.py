"""ch10 round-4 acceptance tests: (WI-1) the subagent context shares the
parent's task/name registries so SendMessage-to-a-running-child is actually
delivered, and (WI-2) terminal background bash tasks become eviction-eligible.

Covers my-docs/port-improvement-round-4/ch10-coordination-round4-plan.md.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.agent.subagent_context import (
    SubagentContextOverrides,
    create_subagent_context,
)
from src.tool_system.context import ToolContext


class TestSubagentSharesRegistries(unittest.TestCase):
    """WI-1 — the fix for the silent SendMessage-to-running-child drop."""

    def test_child_shares_parent_runtime_tasks(self):
        parent = ToolContext(workspace_root=Path("/tmp"))
        child = create_subagent_context(
            parent, SubagentContextOverrides(agent_id="worker-1"),
        )
        # SAME instance — not a fresh empty registry.
        self.assertIs(child.runtime_tasks, parent.runtime_tasks)
        self.assertIs(child.agent_name_registry, parent.agent_name_registry)

    def test_message_queued_on_parent_drains_via_query_hook(self):
        # End-to-end through the REAL query drain hook (critic M2): a message
        # SendMessage queues into the parent's registry is drained by the
        # child via _drain_pending_user_messages(child_context) — the exact
        # call query.py makes each tool round. Before WI-1 the child's
        # registry was a fresh empty instance → the hook returned [].
        from src.query.query import _drain_pending_user_messages
        from src.tasks.local_agent import (
            queue_pending_message,
            register_async_agent,
        )

        parent = ToolContext(workspace_root=Path("/tmp"))
        register_async_agent(
            agent_id="worker-1", description="d", prompt="p",
            agent_type="general-purpose", registry=parent.runtime_tasks,
        )
        child = create_subagent_context(
            parent, SubagentContextOverrides(agent_id="worker-1"),
        )
        ok = queue_pending_message("worker-1", "follow-up task",
                                   parent.runtime_tasks)
        self.assertTrue(ok)

        # The real query hook, driven with the CHILD context.
        drained = _drain_pending_user_messages(child)
        # It yields user messages carrying the queued text.
        joined = " ".join(
            str(getattr(m, "content", m)) for m in (drained or [])
        )
        self.assertIn("follow-up task", joined)


class TestBashTerminalEvictionEligible(unittest.TestCase):
    """WI-2 — a terminal background bash task is eviction-eligible (was
    doubly ineligible: no evict_after, notified=False)."""

    def test_terminal_bash_is_eligible_after_grace(self):
        import time

        from dataclasses import replace

        from src.tasks.eviction import is_eligible_for_eviction, schedule_eviction
        from src.tasks.local_shell import LocalShellTaskState

        # Simulate the reaper's terminal patch (WI-2).
        state = LocalShellTaskState(
            id="bg-1", type="local_bash", status="running", description="d",
            start_time=0.0, output_file="/tmp/bg-1.log",
        )
        terminal = replace(state, status="completed", notified=True)
        terminal = schedule_eviction(terminal)

        self.assertIsNotNone(terminal.evict_after)
        self.assertTrue(terminal.notified)
        # Eligible once the grace deadline has passed.
        future = terminal.evict_after + 1
        self.assertTrue(is_eligible_for_eviction(terminal, now=future))
        # NOT eligible before the deadline.
        self.assertFalse(
            is_eligible_for_eviction(terminal, now=terminal.evict_after - 1),
        )


class TestBashReaperMakesEligible(unittest.TestCase):
    """critic M2 — drive the REAL bash reaper to terminal and assert the
    resulting registry state is eviction-eligible (not the hand-built one)."""

    def test_reaper_terminal_state_is_eligible(self):
        import subprocess
        import time

        from src.tasks.eviction import is_eligible_for_eviction
        from src.tool_system.context import ToolContext
        from src.tool_system.tools.bash.background import spawn_background_bash

        ctx = ToolContext(workspace_root=Path("/tmp"))
        # A command that exits immediately.
        result = spawn_background_bash(
            command="true", cwd=Path("/tmp"), description="t", context=ctx,
        )
        task_id = result["backgroundTaskId"]
        # Wait for the reaper daemon to flip the state terminal.
        deadline = time.time() + 5.0
        state = None
        while time.time() < deadline:
            state = ctx.runtime_tasks.get(task_id)
            if state is not None and state.status in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertIsNotNone(state)
        self.assertIn(state.status, ("completed", "failed"))
        # The reaper set notified + evict_after (WI-2), so it is eligible
        # once the grace deadline passes.
        self.assertTrue(state.notified)
        self.assertIsNotNone(state.evict_after)
        self.assertTrue(
            is_eligible_for_eviction(state, now=state.evict_after + 1),
        )


class TestAgentServerStartsSweeperOrdering(unittest.TestCase):
    """critic B1/M2 — the sweeper start must come AFTER tool_context is
    stored on the session; the first attempt placed it earlier and read a
    still-None sess.tool_context, so the sweeper never started (the exact
    built-but-dead defect WI-2 exists to fix). Guard the ordering."""

    def test_sweeper_starts_after_tool_context_assignment(self):
        import inspect

        from src.server import agent_server as asrv

        src = inspect.getsource(asrv._build_runtime)
        assign_idx = src.find("sess.tool_context = tool_context")
        start_idx = src.find("start_eviction_sweeper(")
        self.assertNotEqual(assign_idx, -1, "tool_context assignment present")
        self.assertNotEqual(start_idx, -1, "sweeper start present")
        # The sweeper start must appear AFTER the assignment (B1 regression).
        self.assertGreater(start_idx, assign_idx)
        # And it must NOT read the stale sess.tool_context — it uses the
        # in-scope local tool_context.
        snippet = src[start_idx - 60:start_idx + 60]
        self.assertIn("tool_context.runtime_tasks", snippet)
        self.assertNotIn("sess.tool_context.runtime_tasks", snippet)


class TestSweeperReclaimsTerminalTasks(unittest.TestCase):
    """WI-2 — sweep_once actually removes an eligible terminal task."""

    def test_sweep_removes_eligible(self):
        from dataclasses import replace

        from src.task_registry import RuntimeTaskRegistry
        from src.tasks.eviction import schedule_eviction, sweep_once
        from src.tasks.local_shell import LocalShellTaskState

        reg = RuntimeTaskRegistry()
        state = LocalShellTaskState(
            id="bg-1", type="local_bash", status="completed", description="d",
            start_time=0.0, output_file="/tmp/bg-1.log", notified=True,
        )
        reg.upsert(schedule_eviction(state))
        # A still-running task must survive.
        reg.upsert(LocalShellTaskState(
            id="bg-2", type="local_bash", status="running", description="d2",
            start_time=0.0, output_file="/tmp/bg-2.log",
        ))

        dropped = sweep_once(reg, now=reg.get("bg-1").evict_after + 1)
        self.assertIn("bg-1", dropped)
        self.assertIsNone(reg.get("bg-1"))
        self.assertIsNotNone(reg.get("bg-2"))  # running task retained


if __name__ == "__main__":
    unittest.main()
