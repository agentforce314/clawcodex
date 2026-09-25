"""R5 round-5 (ch13) — terminal agent_progress for async + failed-sync
subagents, and the goal-label fix.

Round-4 emitted terminal agent_progress only on the sync SUCCESS path (with a
truncated-prompt goal label). R5 emits it for failed-sync and async
(completed + failed) too, with the task description as the goal label. These
drive the REAL Agent tool end-to-end (build registry + context, patch
run_agent, dispatch a ToolCall), not a re-implementation.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.tool_system.tools.agent import _emit_terminal_agent_progress
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage


def _ctx(tmp):
    emitted: list = []
    ctx = ToolContext(workspace_root=Path(tmp))
    ctx.agent_progress_emit = lambda ev: emitted.append(ev)
    return ctx, emitted


def _terminal(emitted, status):
    return [e for e in emitted if e.get("status") == status]


def _wait_terminal(ctx, task_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = ctx.runtime_tasks.get(task_id)
        if st is not None and str(st.status) not in ("running", "pending"):
            return str(st.status)
        time.sleep(0.05)
    st = ctx.runtime_tasks.get(task_id)
    return str(st.status) if st else ""


class TestTerminalEmitHelper(unittest.TestCase):
    def test_emits_with_goal_description(self):
        emitted = []
        ctx = MagicMock(agent_progress_emit=lambda ev: emitted.append(ev))
        _emit_terminal_agent_progress(
            ctx, agent_id="a1", name="Explore", description="explore the repo",
            subagent_type="Explore", status="completed")
        self.assertEqual(emitted[0]["status"], "completed")
        self.assertEqual(emitted[0]["description"], "explore the repo")

    def test_no_hook_and_bad_hook_never_raise(self):
        _emit_terminal_agent_progress(
            MagicMock(agent_progress_emit=None), agent_id="x", name="n",
            description="d", subagent_type="t", status="failed")

        def _boom(_ev):
            raise RuntimeError("bad")
        _emit_terminal_agent_progress(
            MagicMock(agent_progress_emit=_boom), agent_id="x", name="n",
            description="d", subagent_type="t", status="completed")


class TestSyncTerminal(unittest.TestCase):
    def test_sync_success_emits_completed_with_description(self):
        with TemporaryDirectory() as tmp:
            ctx, emitted = _ctx(tmp)

            async def _fake(_p):
                yield AssistantMessage(content=[TextBlock(text="done")])

            with patch("src.tool_system.tools.agent.run_agent", _fake):
                registry = build_default_registry(provider=object())
                registry.dispatch(ToolCall(name="Agent", input={
                    "description": "explore the repo",
                    "prompt": "a long prompt that must NOT become the goal label",
                }), ctx)

            done = _terminal(emitted, "completed")
            self.assertTrue(done, "sync success should emit terminal completed")
            # Goal label is the task description, not the truncated prompt.
            self.assertEqual(done[-1]["description"], "explore the repo")

    def test_running_emits_name_the_resolved_agent(self):
        # Omitting subagent_type runs general-purpose. The running emits sent
        # the raw input (None) while the terminal one sent the resolved type,
        # so a client could not say which agent was running until it ended.
        with TemporaryDirectory() as tmp:
            ctx, emitted = _ctx(tmp)

            async def _fake(p):
                message = AssistantMessage(content=[TextBlock(text="working")])
                p.on_message(message)
                yield message

            with patch("src.tool_system.tools.agent.run_agent", _fake):
                registry = build_default_registry(provider=object())
                registry.dispatch(ToolCall(name="Agent", input={
                    "description": "map the proof obligations",
                    "prompt": "look around",
                    "name": "nl-sketcher",
                }), ctx)

            running = _terminal(emitted, "running")
            self.assertTrue(running, "the per-message hook should emit running progress")
            self.assertEqual(running[-1]["subagent_type"], "general-purpose")
            self.assertEqual(running[-1]["name"], "nl-sketcher")
            self.assertEqual(running[-1]["description"], "map the proof obligations")
            self.assertEqual(_terminal(emitted, "completed")[-1]["subagent_type"], "general-purpose")

    def test_unnamed_spawn_stays_unnamed_when_it_ends(self):
        # The sync terminal emit used to fill `name` with the agent type, so a
        # client labelling rows name-first renamed the agent at completion.
        with TemporaryDirectory() as tmp:
            ctx, emitted = _ctx(tmp)

            async def _fake(p):
                message = AssistantMessage(content=[TextBlock(text="working")])
                p.on_message(message)
                yield message

            with patch("src.tool_system.tools.agent.run_agent", _fake):
                registry = build_default_registry(provider=object())
                registry.dispatch(ToolCall(name="Agent", input={
                    "description": "survey the repo",
                    "prompt": "look around",
                    "subagent_type": "Explore",
                }), ctx)

            self.assertIsNone(_terminal(emitted, "running")[-1]["name"])
            completed = _terminal(emitted, "completed")[-1]
            self.assertIsNone(completed["name"])
            self.assertEqual(completed["subagent_type"], "Explore")

    def test_sync_failure_emits_failed(self):
        with TemporaryDirectory() as tmp:
            ctx, emitted = _ctx(tmp)

            async def _boom(_p):
                raise ValueError("agent blew up")
                yield  # noqa — make it an async generator

            with patch("src.tool_system.tools.agent.run_agent", _boom):
                registry = build_default_registry(provider=object())
                try:
                    registry.dispatch(ToolCall(name="Agent", input={
                        "description": "run tests", "prompt": "p",
                    }), ctx)
                except Exception:
                    pass  # the error surfaces; we only assert the HUD emit

            failed = _terminal(emitted, "failed")
            self.assertTrue(failed, "failed sync should emit terminal failed")
            self.assertEqual(failed[-1]["description"], "run tests")


class TestAsyncTerminal(unittest.TestCase):
    def test_async_success_emits_completed(self):
        with TemporaryDirectory() as tmp:
            ctx, emitted = _ctx(tmp)

            async def _fake(_p):
                yield AssistantMessage(content=[TextBlock(text="async done")])

            with patch("src.tool_system.tools.agent.run_agent", _fake):
                registry = build_default_registry(provider=object())
                res = registry.dispatch(ToolCall(name="Agent", input={
                    "description": "background job", "prompt": "work",
                    "run_in_background": True,
                }), ctx)
                task_id = str(res.output["agent_id"])
                self.assertEqual(_wait_terminal(ctx, task_id), "completed")

            done = _terminal(emitted, "completed")
            self.assertTrue(done, "async success should emit terminal completed")
            self.assertEqual(done[-1]["description"], "background job")

    def test_async_failure_emits_failed(self):
        with TemporaryDirectory() as tmp:
            ctx, emitted = _ctx(tmp)

            async def _boom(_p):
                raise ValueError("async blew up")
                yield

            with patch("src.tool_system.tools.agent.run_agent", _boom):
                registry = build_default_registry(provider=object())
                res = registry.dispatch(ToolCall(name="Agent", input={
                    "description": "background job", "prompt": "work",
                    "run_in_background": True,
                }), ctx)
                task_id = str(res.output["agent_id"])
                self.assertEqual(_wait_terminal(ctx, task_id), "failed")

            failed = _terminal(emitted, "failed")
            self.assertTrue(failed, "async failure should emit terminal failed")


if __name__ == "__main__":
    unittest.main()
