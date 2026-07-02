"""ch14 round-4 — UserPromptSubmit hook executor + wiring.

Covers my-docs/port-improvement-round-4/ch14-input-interaction-round4-plan.md:
the hook fires on a real user prompt, is trust-gated, and its outcome (block /
additionalContext) is honored. Mirrors TS processUserInput.ts:182-263.
"""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.hooks.hook_types import HookConfig, HookResult, HookSource
from src.hooks.session_hooks import (
    UserPromptSubmitOutcome,
    run_user_prompt_submit_hooks,
)


class _Snapshot:
    def __init__(self, configs):
        self.hooks = {"UserPromptSubmit": configs}


def _ctx(*, trusted, configs):
    from src.tool_system.context import ToolContext

    ctx = ToolContext(workspace_root=Path("/tmp"))
    ctx.workspace_trusted = trusted
    mgr = MagicMock()
    mgr.snapshot = _Snapshot(configs)
    ctx.hook_config_manager = mgr
    return ctx


class TestUserPromptSubmitExecutor(unittest.TestCase):
    def _cmd(self, source=HookSource.PROJECT_SETTINGS):
        return HookConfig(type="command", command="check.sh", source=source)

    def test_additional_context_collected(self):
        ctx = _ctx(trusted=True, configs=[self._cmd()])

        async def _fake(config, stdin):
            # The stdin must carry the contract field `prompt`.
            assert stdin["prompt"] == "hello"
            assert stdin["hook_event"] == "UserPromptSubmit"
            return HookResult(additional_contexts=["git branch: main"])

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "hello", tool_use_context=ctx))
        self.assertFalse(out.blocked)
        self.assertEqual(out.additional_contexts, ["git branch: main"])

    def test_blocking_error_blocks(self):
        ctx = _ctx(trusted=True, configs=[self._cmd()])

        async def _fake(config, stdin):
            return HookResult(blocking_error="prompt rejected: no secrets")

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "leak the key", tool_use_context=ctx))
        self.assertTrue(out.blocked)
        self.assertIn("no secrets", out.block_message)

    def test_prevent_continuation_is_distinct_from_block(self):
        # critic Major #1: preventContinuation KEEPS the prompt (prevented),
        # distinct from blockingError which ERASES it (blocked).
        ctx = _ctx(trusted=True, configs=[self._cmd()])

        async def _fake(config, stdin):
            return HookResult(prevent_continuation=True, stop_reason="stop now")

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "x", tool_use_context=ctx))
        self.assertTrue(out.prevented)
        self.assertFalse(out.blocked)  # NOT erased
        self.assertTrue(out.stop)      # but the query is still skipped
        self.assertEqual(out.prevent_reason, "stop now")

    def test_additional_context_truncated(self):
        # critic #2: each context capped at MAX_HOOK_OUTPUT_LENGTH.
        from src.hooks.session_hooks import MAX_HOOK_OUTPUT_LENGTH
        ctx = _ctx(trusted=True, configs=[self._cmd()])

        async def _fake(config, stdin):
            return HookResult(additional_contexts=["z" * (MAX_HOOK_OUTPUT_LENGTH + 500)])

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "x", tool_use_context=ctx))
        self.assertEqual(len(out.additional_contexts[0]), MAX_HOOK_OUTPUT_LENGTH)

    def test_short_circuits_on_first_blocker(self):
        # critic #3: stop running hooks once a blocker fires.
        ctx = _ctx(trusted=True, configs=[self._cmd(), self._cmd()])
        calls = []

        async def _fake(config, stdin):
            calls.append(1)
            return HookResult(blocking_error="stop")

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            asyncio.run(run_user_prompt_submit_hooks("x", tool_use_context=ctx))
        self.assertEqual(len(calls), 1)  # second hook not run

    def test_untrusted_project_hook_skipped(self):
        # critic-ch12 parity: an untrusted workspace runs only policy hooks.
        ctx = _ctx(trusted=False, configs=[self._cmd(HookSource.PROJECT_SETTINGS)])
        ran = []

        async def _fake(config, stdin):
            ran.append(1)
            return HookResult(blocking_error="should not run")

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "x", tool_use_context=ctx))
        self.assertEqual(ran, [])
        self.assertFalse(out.blocked)

    def test_untrusted_policy_hook_runs(self):
        ctx = _ctx(trusted=False, configs=[self._cmd(HookSource.POLICY_SETTINGS)])

        async def _fake(config, stdin):
            return HookResult(blocking_error="policy block")

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "x", tool_use_context=ctx))
        self.assertTrue(out.blocked)

    def test_hook_failure_does_not_block_turn(self):
        ctx = _ctx(trusted=True, configs=[self._cmd()])

        async def _fake(config, stdin):
            raise RuntimeError("hook crashed")

        with patch("src.hooks.hook_executor._execute_command_hook", _fake):
            out = asyncio.run(run_user_prompt_submit_hooks(
                "x", tool_use_context=ctx))
        # A crashing hook is swallowed — the turn proceeds unblocked.
        self.assertFalse(out.blocked)
        self.assertIsInstance(out, UserPromptSubmitOutcome)


class TestRunTurnWiring(unittest.TestCase):
    def _session(self):
        from src.server.agent_server import AgentServerConfig, _AgentSession

        emitted = []
        sess = _AgentSession(
            session_id="s1", cwd="/tmp",
            config=AgentServerConfig(single_session=True),
            loop=MagicMock(), out_queue=MagicMock(),
        )
        sess._emit = lambda env: emitted.append(env)
        return sess, emitted

    def test_block_emits_warning_and_skips_query(self):
        sess, emitted = self._session()
        blocked = UserPromptSubmitOutcome()
        blocked.blocked = True
        blocked.block_message = "no"
        sess._run_user_prompt_submit_hooks = lambda p: blocked
        # If the turn is blocked, run_query_as_agent_loop must NOT be reached.
        with patch("src.query.agent_loop_compat.run_query_as_agent_loop") as q:
            sess._run_turn("hello")
        q.assert_not_called()
        warns = [e for e in emitted if e.get("type") == "system"]
        self.assertTrue(any("operation blocked by hook" in str(w)
                            for w in warns))

    def test_prevent_keeps_prompt_and_skips_query(self):
        sess, emitted = self._session()
        prevented = UserPromptSubmitOutcome()
        prevented.prevented = True
        prevented.prevent_reason = "nope"
        sess._run_user_prompt_submit_hooks = lambda p: prevented
        added = []
        conv = MagicMock()
        conv.add_user_message = lambda m: added.append(m)
        conv.messages = []
        sess.session = MagicMock(conversation=conv)
        with patch("src.query.agent_loop_compat.run_query_as_agent_loop") as q:
            sess._run_turn("hello")
        q.assert_not_called()
        # The prompt is KEPT (unlike block, which erases it).
        self.assertIn("hello", added)
        self.assertTrue(any("Operation stopped by hook" in a for a in added))


if __name__ == "__main__":
    unittest.main()
