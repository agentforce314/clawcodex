"""Regression test for the model-error swallowing bug.

Before this fix, when the inner ``query()`` loop caught an upstream API
exception (e.g. connection refused, prompt_too_long, 5xx), it set
``Terminal(reason="model_error", error=<exc>)`` and the adapter
``run_query_as_agent_loop`` returned **normally** with empty
``response_text``. Headless then shipped
``{"is_error": false, "num_turns": 0, "result": ""}`` — indistinguishable
from a legitimately empty completion. SWE-bench and other eval flows
that key off ``is_error`` could not detect the failure.

The fix re-raises the original exception so headless's
``except Exception`` branch sets ``exit_code=1`` and emits
``subtype:error / is_error:true``.

This test pins the new behavior so the swallowing can't silently
return.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController

from src.query.agent_loop_compat import (
    AgentLoopRunResult,
    run_query_as_agent_loop,
)


def _run(coro):
    return asyncio.run(coro)


class TestModelErrorPropagation(unittest.TestCase):
    """When the provider raises, the adapter must re-raise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def _provider_that_raises(self, exc: Exception) -> MagicMock:
        """A MagicMock provider whose ``chat`` raises ``exc``. Mirrors the
        real anthropic_provider's behavior when the SDK raises a
        ``ConnectionError`` / ``APIError`` from ``messages.create``."""
        provider = MagicMock()
        # Force fallback into ``chat()`` so the raise lands inside the
        # inner query loop's ``except Exception`` (which converts it to
        # terminal=model_error).
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = exc
        return provider

    def test_connection_error_re_raises(self):
        """The exception that originally hit the provider must surface
        to the caller — not get silently turned into an empty
        AgentLoopRunResult."""

        original = ConnectionError("Connection refused: localhost:4000")
        provider = self._provider_that_raises(original)

        with self.assertRaises(ConnectionError) as ctx:
            _run(run_query_as_agent_loop(
                initial_messages=[UserMessage(content="anything")],
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
                system_prompt="",
                max_turns=5,
            ))

        # The exact instance round-trips (not a wrapping).
        self.assertIs(ctx.exception, original)

    def test_generic_runtime_error_re_raises(self):
        """Generic upstream errors (not matched by query.py's special
        handlers like prompt_too_long / max_output_tokens) propagate
        through the adapter.

        ``prompt_too_long`` and ``max_output_tokens`` are intentionally
        handled differently by query.py — those get converted to
        "withheld" messages and re-prompted internally rather than
        terminating. So we use a generic message that won't trip the
        keyword detectors at query.py:660 / line 622.
        """

        original = RuntimeError("upstream model returned 502 Bad Gateway")
        provider = self._provider_that_raises(original)

        with self.assertRaises(RuntimeError) as ctx:
            _run(run_query_as_agent_loop(
                initial_messages=[UserMessage(content="hi")],
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
                system_prompt="",
                max_turns=5,
            ))

        self.assertIn("502", str(ctx.exception))

    def test_successful_run_does_not_raise(self):
        """The new branch only fires for ``terminal.reason == 'model_error'``.
        A clean completion still returns normally — guards against the
        re-raise condition matching too broadly."""

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="ok",
            model="test-model",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )

        result = _run(run_query_as_agent_loop(
            initial_messages=[UserMessage(content="hi")],
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            system_prompt="",
            max_turns=5,
        ))

        self.assertIsInstance(result, AgentLoopRunResult)
        self.assertEqual(result.terminal.reason, "completed")
        self.assertEqual(result.response_text, "ok")


if __name__ == "__main__":
    unittest.main()
