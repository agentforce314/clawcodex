"""Plan-mode integration tests — the LIVE entry points.

Sweep lesson: test through the live path, not the unit. These drive:

* ``registry.dispatch`` for EnterPlanMode/ExitPlanMode (permission ask →
  handler reply → chosen_updates setMode → tool call), asserting the mode
  flips, the stash restores, the one-shot flags fire, and the model-facing
  texts are the ported ones.
* ``run_query_as_agent_loop`` for the attachment pipeline (full on turn 1,
  absent on turns 2-5, SPARSE on turn 6 — the critic's cadence note), with
  ``on_attachment`` persistence into a real conversation list.
* The legacy ``src/plan`` module staying intact (M2 scope correction).
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from typing import Any

from src.bootstrap.state import (
    set_has_exited_plan_mode,
    set_needs_plan_mode_exit_attachment,
)
from src.permissions.types import (
    PermissionAskReply,
    PermissionUpdateSetMode,
)
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall
from src.tool_system.registry import ToolRegistry
from src.tool_system.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool
from src.utils import plans


def _reset_state():
    set_has_exited_plan_mode(False)
    set_needs_plan_mode_exit_attachment(False)
    plans.clear_all_plan_slugs()


class TestPlanToolsThroughDispatch(unittest.TestCase):
    def setUp(self):
        _reset_state()
        self.registry = ToolRegistry([EnterPlanModeTool, ExitPlanModeTool])
        self.ctx = ToolContext(workspace_root=Path("."))
        self.ctx.permission_context.mode = "default"
        self.mode_pushes: list[str] = []
        self.ctx.on_permission_mode_change = self.mode_pushes.append

    def tearDown(self):
        try:
            plans.get_plan_file_path().unlink(missing_ok=True)
        except OSError:
            pass
        _reset_state()

    def test_enter_plan_mode_no_ask_and_stash(self):
        asked: list[Any] = []
        self.ctx.permission_handler = lambda req: (
            asked.append(req),
            PermissionAskReply(behavior="deny", message="should not be asked"),
        )[1]
        self.ctx.permission_context.mode = "acceptEdits"

        result = self.registry.dispatch(ToolCall(name="EnterPlanMode", input={}), self.ctx)

        self.assertFalse(result.is_error)
        self.assertEqual(asked, [])  # auto-allow: the handler never ran
        self.assertEqual(self.ctx.permission_context.mode, "plan")
        self.assertEqual(self.ctx.permission_context.pre_plan_mode, "acceptEdits")
        self.assertEqual(self.mode_pushes, ["plan"])

    def test_exit_plan_mode_approve_flips_mode_before_call(self):
        # Enter plan from default, then approve "auto-accept edits".
        self.registry.dispatch(ToolCall(name="EnterPlanMode", input={}), self.ctx)
        self.mode_pushes.clear()

        plan_file = plans.get_plan_file_path()
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text("# The Plan", encoding="utf-8")

        seen: list[Any] = []

        def handler(req):
            seen.append(req)
            return PermissionAskReply(
                behavior="allow",
                chosen_updates=(
                    PermissionUpdateSetMode(
                        type="setMode", destination="session", mode="acceptEdits"
                    ),
                ),
            )

        self.ctx.permission_handler = handler
        result = self.registry.dispatch(ToolCall(name="ExitPlanMode", input={}), self.ctx)

        self.assertFalse(result.is_error)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].message, "Exit plan mode?")
        # chosen_updates flipped the mode BEFORE call() → the fallback no-oped
        # and the final mode is the dialog's choice, not pre_plan_mode.
        self.assertEqual(self.ctx.permission_context.mode, "acceptEdits")
        self.assertEqual(self.mode_pushes, ["acceptEdits"])
        self.assertEqual(result.output["plan"], "# The Plan")

        from src.bootstrap.state import (
            has_exited_plan_mode_in_session,
            needs_plan_mode_exit_attachment,
        )

        self.assertTrue(has_exited_plan_mode_in_session())
        self.assertTrue(needs_plan_mode_exit_attachment())

    def test_exit_plan_mode_deny_keeps_planning_with_feedback(self):
        self.registry.dispatch(ToolCall(name="EnterPlanMode", input={}), self.ctx)
        self.mode_pushes.clear()

        self.ctx.permission_handler = lambda req: PermissionAskReply(
            behavior="deny", message="also update the README"
        )
        result = self.registry.dispatch(ToolCall(name="ExitPlanMode", input={}), self.ctx)

        self.assertTrue(result.is_error)
        # The model-facing rejection is the ported REJECT_MESSAGE_WITH_REASON
        # text with the user's keep-planning feedback appended.
        self.assertIn("the user said:\nalso update the README", result.output["error"])
        self.assertEqual(self.ctx.permission_context.mode, "plan")
        self.assertEqual(self.mode_pushes, [])

    def test_exit_plan_mode_outside_plan_is_validation_error(self):
        result = self.registry.dispatch(ToolCall(name="ExitPlanMode", input={}), self.ctx)
        self.assertTrue(result.is_error)
        self.assertIn("You are not in plan mode", result.output["error"])


class _ScriptedProvider:
    """Minimal provider: returns a plain-text assistant message per call."""

    model = "fake-model"

    def __init__(self):
        self.calls: list[list[Any]] = []

    def chat_stream_response(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    def chat(self, messages, **kwargs):  # pragma: no cover - some paths probe it
        raise NotImplementedError


class TestAttachmentCadenceLive(unittest.TestCase):
    """Drive run_query_as_agent_loop turn-by-turn with a fake provider and a
    REAL persisted conversation list; assert the critic's cadence: full @1,
    absent @2-5, SPARSE @6."""

    def setUp(self):
        _reset_state()

    def tearDown(self):
        try:
            plans.get_plan_file_path().unlink(missing_ok=True)
        except OSError:
            pass
        _reset_state()

    def test_cadence_over_persisted_turns(self):
        from src.query import agent_loop_compat as alc

        ctx = ToolContext(workspace_root=Path("."))
        ctx.permission_context.mode = "plan"

        conversation: list[Any] = []  # the persisted history (role/content objs)

        class _Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        def persist(m):
            conversation.append(_Msg(m.role, m.content))

        # Patch the query loop itself out — we only exercise the injection
        # seam at the head of run_query_as_agent_loop, then abort the run by
        # raising from the fake query. The messages_for_query list the seam
        # built is captured for assertions.
        captured: dict[str, Any] = {}

        class _StopProbe(Exception):
            pass

        async def fake_query(params, terminal_holder=None):
            captured["messages"] = list(params.messages)
            raise _StopProbe()
            yield  # pragma: no cover — makes this an async generator

        original_query = alc.query
        alc.query = fake_query
        try:
            def run_turn(user_text):
                conversation.append(_Msg("user", user_text))
                try:
                    asyncio.run(
                        alc.run_query_as_agent_loop(
                            initial_messages=list(conversation),
                            provider=_ScriptedProvider(),
                            tool_registry=ToolRegistry([]),
                            tool_context=ctx,
                            system_prompt="sys",
                            on_attachment=persist,
                        )
                    )
                except _StopProbe:
                    pass
                except Exception as exc:  # noqa: BLE001
                    # run_query_as_agent_loop wraps errors; the probe's
                    # StopProbe surfaces as the terminal error. Tolerate.
                    if "_StopProbe" not in type(exc).__name__ and "StopProbe" not in str(exc):
                        raise
                return captured.get("messages", [])

            # Turn 1: full attachment injected + persisted.
            msgs1 = run_turn("turn 1")
            in_query_1 = [
                m for m in msgs1
                if "Plan mode is active" in str(getattr(m, "content", ""))
            ]
            self.assertEqual(len(in_query_1), 1)
            persisted_1 = [
                m for m in conversation
                if "Plan mode is active" in str(getattr(m, "content", ""))
            ]
            self.assertEqual(len(persisted_1), 1)

            # Turns 2-5: throttled (no new attachment; the persisted one is
            # still in history so the model keeps seeing it).
            for i in range(2, 6):
                msgs = run_turn(f"turn {i}")
                fresh = [
                    m for m in msgs
                    if "Plan mode still active" in str(getattr(m, "content", ""))
                ]
                self.assertEqual(fresh, [], f"turn {i} should be throttled")

            # Turn 6 (5 human turns since attachment #1): attachment #2 —
            # SPARSE, not full (critic note).
            msgs6 = run_turn("turn 6")
            sparse = [
                m for m in msgs6
                if "Plan mode still active" in str(getattr(m, "content", ""))
            ]
            self.assertEqual(len(sparse), 1)
            fulls = [
                m for m in conversation
                if "Plan mode is active" in str(getattr(m, "content", ""))
            ]
            self.assertEqual(len(fulls), 1, "no second FULL attachment yet")
        finally:
            alc.query = original_query

    def test_exit_attachment_fires_once_after_leaving_plan(self):
        from src.query import agent_loop_compat as alc

        ctx = ToolContext(workspace_root=Path("."))
        ctx.permission_context.mode = "default"
        set_needs_plan_mode_exit_attachment(True)

        captured: dict[str, Any] = {}

        class _StopProbe(Exception):
            pass

        async def fake_query(params, terminal_holder=None):
            captured["messages"] = list(params.messages)
            raise _StopProbe()
            yield  # pragma: no cover

        original_query = alc.query
        alc.query = fake_query
        try:
            class _Msg:
                def __init__(self, role, content):
                    self.role = role
                    self.content = content

            def run_turn():
                try:
                    asyncio.run(
                        alc.run_query_as_agent_loop(
                            initial_messages=[_Msg("user", "hello")],
                            provider=_ScriptedProvider(),
                            tool_registry=ToolRegistry([]),
                            tool_context=ctx,
                            system_prompt="sys",
                        )
                    )
                except _StopProbe:
                    pass
                except Exception as exc:  # noqa: BLE001
                    if "StopProbe" not in type(exc).__name__ and "StopProbe" not in str(exc):
                        raise
                return captured.get("messages", [])

            msgs = run_turn()
            exits = [
                m for m in msgs
                if "## Exited Plan Mode" in str(getattr(m, "content", ""))
            ]
            self.assertEqual(len(exits), 1)

            msgs2 = run_turn()
            exits2 = [
                m for m in msgs2
                if "## Exited Plan Mode" in str(getattr(m, "content", ""))
            ]
            self.assertEqual(exits2, [], "exit attachment is one-shot")
        finally:
            alc.query = original_query


class TestLegacyPlanModuleIntact(unittest.TestCase):
    """M2 scope pin — the hermes-legacy system-prompt plan stays untouched
    (bootstrap/resume call _compose_with_plan; only the control changed)."""

    def test_legacy_module_round_trip(self):
        import tempfile

        from src.plan import clear_plan, get_plan, set_plan

        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(get_plan(td))  # legacy contract: '' when unset
            set_plan(td, "legacy plan text")
            self.assertEqual(get_plan(td), "legacy plan text")
            clear_plan(td)
            self.assertFalse(get_plan(td))


if __name__ == "__main__":
    unittest.main()
