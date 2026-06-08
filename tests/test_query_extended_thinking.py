"""Tests for the extended-thinking injection in ``_call_model_sync``.

Mirrors the TS reference's behavior: when the active provider is the
Anthropic SDK and the model is in the Claude 4.x family (or newer),
``client.messages.create`` / ``messages.stream`` receives
``thinking={"type": "adaptive"}`` and ``output_config={"effort": ...}``
without any caller having to ask for it. Older Claude versions and
non-Anthropic providers don't see the kwargs.

The tests drive the agent loop through one turn against a ``MagicMock``
Anthropic provider and assert the kwargs landed on the streaming-fallback
``chat()`` call.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import ChatResponse
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController

from src.query.query import (
    QueryParams,
    _model_supports_extended_thinking,
    query,
)


def _run(coro):
    return asyncio.run(coro)


def _make_anthropic_mock(model: str) -> MagicMock:
    """A MagicMock that passes ``isinstance(x, AnthropicProvider)``.

    ``_call_model_sync`` discriminates by ``isinstance(provider,
    AnthropicProvider)``, so a plain MagicMock would land in the non-
    Anthropic branch and never see the thinking injection.
    """
    provider = MagicMock(spec=AnthropicProvider)
    provider.model = model
    # Force the streaming path into the chat() fallback so test assertions
    # can read the kwargs straight off ``chat.call_args``.
    provider.chat_stream_response.side_effect = NotImplementedError()
    provider.chat.return_value = ChatResponse(
        content="ok",
        model=model,
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="end_turn",
        tool_uses=None,
    )
    return provider


class TestModelSupportsThinking(unittest.TestCase):
    """The standalone gating helper."""

    def test_claude_4_family(self):
        for m in (
            "claude-opus-4-6",
            "claude-opus-4-7-20260201",
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5",
        ):
            self.assertTrue(_model_supports_extended_thinking(m), m)

    def test_legacy_models(self):
        for m in (
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
        ):
            self.assertFalse(_model_supports_extended_thinking(m), m)

    def test_non_anthropic(self):
        for m in ("gpt-4o", "deepseek-v4-pro", "gemini-2.5-pro", "", None):
            self.assertFalse(_model_supports_extended_thinking(m), m)


class TestExtendedThinkingInjection(unittest.TestCase):
    """End-to-end: drive the loop and inspect kwargs the provider saw."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)
        self.abort = AbortController()

    def tearDown(self):
        self.tmp.cleanup()

    def _drive_one_turn(self, provider: MagicMock, **extra_params) -> dict:
        """Run one ``query()`` turn and return the kwargs the provider
        observed on its (mock) ``chat()`` call."""
        params = QueryParams(
            messages=[UserMessage(content="hi")],
            system_prompt="hello",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=1,
            **extra_params,
        )

        async def run():
            async for _ in query(params):
                pass

        _run(run())
        self.assertTrue(provider.chat.called, "provider.chat() should have been invoked")
        return provider.chat.call_args.kwargs

    def test_anthropic_opus_4_gets_thinking_by_default(self):
        provider = _make_anthropic_mock("claude-opus-4-6")
        kw = self._drive_one_turn(provider)
        self.assertEqual(kw.get("thinking"), {"type": "adaptive"})
        self.assertEqual(kw.get("output_config"), {"effort": "medium"})

    def test_anthropic_sonnet_4_gets_thinking_by_default(self):
        provider = _make_anthropic_mock("claude-sonnet-4-5")
        kw = self._drive_one_turn(provider)
        self.assertEqual(kw.get("thinking"), {"type": "adaptive"})

    def test_anthropic_legacy_3x_does_NOT_get_thinking(self):
        # 3.x models reject the parameter at the API layer — the helper
        # must withhold it.
        provider = _make_anthropic_mock("claude-3-5-sonnet-20241022")
        kw = self._drive_one_turn(provider)
        self.assertNotIn("thinking", kw)
        self.assertNotIn("output_config", kw)

    def test_explicit_opt_out_suppresses_thinking(self):
        provider = _make_anthropic_mock("claude-opus-4-6")
        kw = self._drive_one_turn(provider, extended_thinking=False)
        self.assertNotIn("thinking", kw)
        self.assertNotIn("output_config", kw)

    def test_explicit_opt_in_overrides_unknown_model(self):
        # An out-of-band model name (e.g. proxy alias) should still get
        # thinking if the caller forces it. Lets users opt into thinking
        # on a custom Claude alias the helper hasn't been taught about.
        provider = _make_anthropic_mock("custom-proxy-claude-model")
        kw = self._drive_one_turn(provider, extended_thinking=True)
        self.assertEqual(kw.get("thinking"), {"type": "adaptive"})

    def test_custom_effort_propagates(self):
        provider = _make_anthropic_mock("claude-opus-4-6")
        kw = self._drive_one_turn(provider, thinking_effort="high")
        self.assertEqual(kw.get("output_config"), {"effort": "high"})


if __name__ == "__main__":
    unittest.main()
