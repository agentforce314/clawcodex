"""Ch5/G.1+G.2 — QueryDeps injection seam tests.

Verifies the contract from chapter 5 §"Dependency Injection":
  G.1 — QueryDeps exposes 4 slots (call_model, microcompact,
        autocompact, uuid) with a production_deps() factory.
  G.2 — query() routes the model call through deps.call_model; a
        custom QueryDeps injected via QueryParams.deps replaces the
        production wiring without monkey-patching module imports.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, UserMessage
from src.types.content_blocks import TextBlock
from src.utils.abort_controller import AbortController

from src.query.query import QueryParams, query
from src.query.deps import QueryDeps, production_deps
from src.query.transitions import TerminalHolder


def _run(coro):
    return asyncio.run(coro)


class TestProductionDeps(unittest.TestCase):
    """G.1 — production_deps() wires the canonical imports."""

    def test_production_deps_has_all_four_slots(self):
        deps = production_deps()
        self.assertTrue(callable(deps.call_model))
        self.assertTrue(callable(deps.microcompact))
        self.assertTrue(callable(deps.autocompact))
        self.assertTrue(callable(deps.uuid))

    def test_uuid_factory_returns_hex_string(self):
        deps = production_deps()
        u = deps.uuid()
        self.assertIsInstance(u, str)
        # uuid4().hex returns 32 hex chars without dashes.
        self.assertEqual(len(u), 32)


class TestInjectedDeps(unittest.TestCase):
    """G.2 — query() routes call_model through deps.call_model."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_custom_call_model_is_invoked(self):
        """When QueryParams.deps is set, query() uses deps.call_model
        instead of the default _call_model_sync."""
        call_count = {"n": 0}
        from unittest.mock import MagicMock

        # The default provider's chat methods must NOT be called when a
        # custom call_model is injected. We attach an explicit assertion.
        provider = MagicMock()
        provider.chat.side_effect = AssertionError(
            "Provider.chat must not be called when custom deps "
            "intercepts call_model"
        )
        provider.chat_stream_response.side_effect = AssertionError(
            "Provider.chat_stream_response must not be called when "
            "custom deps intercepts call_model"
        )

        async def fake_call_model(**kw):
            call_count["n"] += 1
            return (
                [AssistantMessage(
                    content=[TextBlock(text="OK from fake")],
                    stop_reason="end_turn",
                )],
                [],
            )

        # Use production_deps() as a base, then override call_model.
        deps = production_deps()
        deps.call_model = fake_call_model

        params = QueryParams(
            messages=[UserMessage(content="hi")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
            deps=deps,
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(holder.value.reason, "completed")

    def test_default_falls_back_to_production_deps(self):
        """When QueryParams.deps is None, query() resolves
        production_deps() — the existing test suite already exercises
        this path implicitly; this test makes the contract explicit."""
        from unittest.mock import MagicMock
        from src.providers.base import ChatResponse
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="OK",
            model="test",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )

        params = QueryParams(
            messages=[UserMessage(content="hi")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=5,
            # deps=None — exercise the default path.
        )
        holder = TerminalHolder()

        async def run():
            async for _ in query(params, terminal_holder=holder):
                pass

        _run(run())

        self.assertEqual(holder.value.reason, "completed")
        # provider.chat WAS called — proving the default production_deps
        # routed through _call_model_sync → provider.
        self.assertGreaterEqual(provider.chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
