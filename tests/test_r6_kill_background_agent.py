"""R6-1 — killing a background agent actually STOPS the live run.

Round-4/5 shipped the async subagent lifecycle but its abort_event was never
wired to the run, so kill_async_agent only flipped the registry status to
"killed" while the run kept going (burning tokens) to natural completion.
R6 gives the async agent a dedicated AbortController, stores it on the task
state, wires it as run_params.abort_controller (query() polls it), and has
kill_async_agent .abort() it — from the kill thread while the run polls on the
background loop.
"""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _wait_terminal(ctx, task_id, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = ctx.runtime_tasks.get(task_id)
        if st is not None and str(st.status) not in ("running", "pending"):
            return str(st.status)
        time.sleep(0.02)
    st = ctx.runtime_tasks.get(task_id)
    return str(st.status) if st else ""


class TestKillWireUnit(unittest.TestCase):
    """The registry-level wire: register with a controller, kill → aborted."""

    def test_kill_aborts_the_stored_controller(self):
        from src.task_registry import RuntimeTaskRegistry
        from src.tasks.local_agent import kill_async_agent, register_async_agent
        from src.utils.abort_controller import AbortController

        reg = RuntimeTaskRegistry()
        ctrl = AbortController()
        with TemporaryDirectory() as tmp:
            with patch("src.agent.transcript.get_agent_transcript_path",
                       return_value=str(Path(tmp) / "t.jsonl")):
                register_async_agent(
                    agent_id="a1", description="d", prompt="p",
                    agent_type="general", abort_controller=ctrl, registry=reg,
                )
            self.assertFalse(ctrl.signal.aborted)
            kill_async_agent("a1", reg, enqueue_notification=False)
        self.assertTrue(ctrl.signal.aborted)  # the run's controller is aborted
        self.assertEqual(str(reg.get("a1").status), "killed")

    def test_kill_without_controller_is_safe(self):
        # Legacy state (no controller) must not raise.
        from src.task_registry import RuntimeTaskRegistry
        from src.tasks.local_agent import kill_async_agent, register_async_agent

        reg = RuntimeTaskRegistry()
        with TemporaryDirectory() as tmp:
            with patch("src.agent.transcript.get_agent_transcript_path",
                       return_value=str(Path(tmp) / "t.jsonl")):
                register_async_agent(
                    agent_id="a2", description="d", prompt="p",
                    agent_type="general", registry=reg,
                )
            kill_async_agent("a2", reg, enqueue_notification=False)
        self.assertEqual(str(reg.get("a2").status), "killed")


class TestKillStopsTheRunEndToEnd(unittest.TestCase):
    """End-to-end through the real Agent tool: a long-running background agent
    that polls the abort signal (as query() does) STOPS early when killed."""

    def test_kill_halts_a_running_background_agent(self):
        import src.tool_system.tools.agent as agent_mod
        from src.tasks.local_agent import kill_async_agent
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry
        from src.tool_system.protocol import ToolCall
        from src.types.content_blocks import TextBlock
        from src.types.messages import AssistantMessage

        from src.utils.abort_controller import AbortController

        with TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace_root=Path(tmp))
            # A parent-turn controller — the async agent's controller must be
            # INDEPENDENT of it (critic property (b): killing the bg agent must
            # NOT abort the parent turn).
            ctx.abort_controller = AbortController()
            started = threading.Event()
            iterations = {"n": 0}
            SAFETY_CAP = 400  # if abort fails, the run hits this and the test fails

            async def _long_run(params):
                # Mirror query(): poll params.abort_controller.signal.aborted
                # at each yield point; keep "working" until aborted.
                import asyncio
                while not params.abort_controller.signal.aborted:
                    iterations["n"] += 1
                    started.set()
                    yield AssistantMessage(
                        content=[TextBlock(text=f"step {iterations['n']}")])
                    await asyncio.sleep(0.01)  # ~10ms/step → growth is visible
                    if iterations["n"] >= SAFETY_CAP:
                        break

            with patch.object(agent_mod, "run_agent", _long_run):
                registry = build_default_registry(provider=object())
                res = registry.dispatch(ToolCall(name="Agent", input={
                    "description": "long bg job", "prompt": "work forever",
                    "run_in_background": True,
                }), ctx)
                task_id = str(res.output["agent_id"])

                self.assertTrue(started.wait(3), "bg agent never started")

                # KILL it — from this (main) thread while it polls on the bg loop.
                kill_async_agent(task_id, ctx.runtime_tasks,
                                 enqueue_notification=False)
                self.assertEqual(_wait_terminal(ctx, task_id), "killed")

                # THE LOAD-BEARING CHECK: after the kill, the run must STOP
                # advancing. Status flips to "killed" regardless of the abort
                # wire, so we measure whether the RUN keeps iterating. With the
                # abort wire it freezes (≤1 in-flight step); without it, ~10ms/
                # step means ~40 more steps in 0.4s (this is what caught the
                # neg-control: a vacuous "iterations < cap" check passed even
                # with abort() disabled because the bg thread hadn't caught up).
                n1 = iterations["n"]
                time.sleep(0.4)
                n2 = iterations["n"]

        self.assertLessEqual(
            n2 - n1, 2,
            f"kill did not stop the run — it advanced {n2 - n1} steps after "
            f"the kill (abort wire not effective)",
        )
        self.assertLess(iterations["n"], SAFETY_CAP)  # never ran to the cap
        # ISOLATION (critic (b)): killing the bg agent did NOT abort the
        # parent-turn controller.
        self.assertFalse(ctx.abort_controller.signal.aborted)

    def test_aborting_parent_does_not_stop_background_agent(self):
        # ISOLATION reverse (critic (b)): a parent-turn interrupt (aborting
        # ctx.abort_controller) must NOT stop the independent background agent.
        import src.tool_system.tools.agent as agent_mod
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry
        from src.tool_system.protocol import ToolCall
        from src.types.content_blocks import TextBlock
        from src.types.messages import AssistantMessage
        from src.utils.abort_controller import AbortController

        with TemporaryDirectory() as tmp:
            ctx = ToolContext(workspace_root=Path(tmp))
            ctx.abort_controller = AbortController()
            started = threading.Event()
            iterations = {"n": 0}
            CAP = 30

            async def _run(params):
                import asyncio
                while not params.abort_controller.signal.aborted:
                    iterations["n"] += 1
                    started.set()
                    yield AssistantMessage(content=[TextBlock(text="x")])
                    await asyncio.sleep(0.01)
                    if iterations["n"] >= CAP:
                        break

            with patch.object(agent_mod, "run_agent", _run):
                registry = build_default_registry(provider=object())
                registry.dispatch(ToolCall(name="Agent", input={
                    "description": "bg", "prompt": "work",
                    "run_in_background": True,
                }), ctx)
                self.assertTrue(started.wait(3))
                n1 = iterations["n"]
                # Interrupt the PARENT turn.
                ctx.abort_controller.abort("parent interrupted")
                time.sleep(0.15)
                n2 = iterations["n"]

        # The bg agent kept advancing despite the parent abort (independent
        # controller) — until its own natural CAP.
        self.assertGreater(n2, n1, "parent abort wrongly stopped the bg agent")


if __name__ == "__main__":
    unittest.main()
