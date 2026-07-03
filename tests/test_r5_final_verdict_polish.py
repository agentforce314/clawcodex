"""R5 final-verdict polish — the non-blocking nits the round-5 critics flagged
in their APPROVE verdicts:

  N1 (r5-1): /effort's _EffortProvider wrapper made the recall cost-pin's
     isinstance(AnthropicProvider) check False → pin bypassed in effort mode.
     Now the recall selector unwraps _inner first.
  KILLED (r5-3): the async terminal agent_progress hardcoded "completed"; a
     KILLED subagent should report "killed".
"""
from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch


class TestEffortProviderUnwrapForRecall(unittest.TestCase):
    """N1 — the /effort wrapper no longer hides the AnthropicProvider from the
    recall cost-pin."""

    def test_wrapped_bypasses_pin_unwrapped_restores_it(self):
        from src.memdir.find_relevant_memories import _resolve_recall_model
        from src.providers.anthropic_provider import AnthropicProvider
        from src.server.agent_server import _EffortProvider

        wrapped = _EffortProvider(AnthropicProvider(api_key="k"), "high")
        settings = MagicMock()
        settings.small_fast_model = "claude-3-5-haiku-20241022"

        with patch("src.settings.settings.get_settings", return_value=settings):
            # The wrapper is NOT an AnthropicProvider → the bug: no pin.
            self.assertIsNone(_resolve_recall_model(wrapped))
            # Unwrapped (what _maybe_recall_memories now does first) → pin.
            unwrapped = getattr(wrapped, "_inner", wrapped)
            self.assertEqual(_resolve_recall_model(unwrapped),
                             "claude-3-5-haiku-20241022")

    def test_maybe_recall_unwraps_effort_provider(self):
        # Drive _maybe_recall_memories with a wrapped provider and capture the
        # provider that reaches the recall — it must be the UNWRAPPED inner.
        from src.providers.anthropic_provider import AnthropicProvider
        from src.query import agent_loop_compat as alc
        from src.server.agent_server import _EffortProvider

        inner = AnthropicProvider(api_key="k")
        wrapped = _EffortProvider(inner, "high")
        captured = {}

        async def _fake_reminder(query, memdir, *, provider, already_surfaced,
                                 **kw):
            captured["provider"] = provider
            return None

        settings = MagicMock()
        settings.memory_relevance_prefetch_enabled = True

        # Isolate the unwrap→reminder path: stub the enable-gate and the
        # query-text extractor so we reach the reminder call deterministically.
        with patch("src.settings.settings.get_settings", return_value=settings), \
                patch.object(alc, "_last_user_text", return_value="what did we do"), \
                patch("src.memdir.get_auto_mem_path", return_value="/tmp"), \
                patch("src.memdir.surface_memories.get_relevant_memory_reminder",
                      _fake_reminder):
            asyncio.run(alc._maybe_recall_memories(
                [{"role": "user", "content": "what did we do"}],
                wrapped, MagicMock(), set()))

        self.assertIs(captured.get("provider"), inner)  # unwrapped, not wrapper

    def test_unwrap_is_noop_for_raw_provider(self):
        from src.providers.anthropic_provider import AnthropicProvider
        p = AnthropicProvider(api_key="k")
        self.assertIs(getattr(p, "_inner", p), p)


class TestAsyncKilledStatus(unittest.TestCase):
    """KILLED — a killed async subagent emits terminal 'killed', not
    'completed'."""

    def test_killed_async_emits_killed(self):
        import src.tool_system.tools.agent as agent_mod
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry
        from src.tool_system.protocol import ToolCall
        from src.types.content_blocks import TextBlock
        from src.types.messages import AssistantMessage

        with TemporaryDirectory() as tmp:
            emitted: list = []
            ctx = ToolContext(workspace_root=Path(tmp))
            ctx.agent_progress_emit = lambda ev: emitted.append(ev)

            async def _fake(_p):
                yield AssistantMessage(content=[TextBlock(text="partial")])

            # complete_agent_task is a local import from src.tasks.local_agent;
            # patch it there. Simulate a concurrent kill having marked the task
            # terminal "killed" (complete_agent_task no-ops on terminal state).
            def _mark_killed(agent_id, **kw):
                st = ctx.runtime_tasks.get(agent_id)
                if st is not None:
                    st.status = "killed"

            with patch.object(agent_mod, "run_agent", _fake), \
                    patch("src.tasks.local_agent.complete_agent_task",
                          _mark_killed):
                registry = build_default_registry(provider=object())
                res = registry.dispatch(ToolCall(name="Agent", input={
                    "description": "bg", "prompt": "work",
                    "run_in_background": True,
                }), ctx)
                task_id = str(res.output["agent_id"])
                deadline = time.time() + 2
                while time.time() < deadline and not any(
                    e.get("status") in ("killed", "completed", "failed")
                    for e in emitted
                ):
                    time.sleep(0.05)

            terminal = [e for e in emitted
                        if e.get("status") in ("killed", "completed", "failed")]
            self.assertTrue(terminal, "async lifecycle should emit terminal")
            self.assertEqual(terminal[-1]["status"], "killed")


if __name__ == "__main__":
    unittest.main()
