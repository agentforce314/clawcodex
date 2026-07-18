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
    _model_supports_adaptive_thinking,
    _model_supports_effort,
    _model_supports_extended_thinking,
    _model_supports_max_effort,
    query,
    resolve_thinking_effort,
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


class TestThinkingAllowlists(unittest.TestCase):
    """Direct allowlist tables for the adaptive / effort gates.

    Documents the exact per-model capability matrix (ported from TS
    thinking.ts:152-169 and effort.ts:32-51) so a change to either allowlist
    fails here with a clear model name rather than only end-to-end.
    """

    # (model, supports_thinking, supports_adaptive, supports_effort)
    MATRIX = [
        ("claude-sonnet-4-6", True, True, True),
        ("claude-sonnet-4-6-20250929", True, True, True),
        ("claude-opus-4-6", True, True, True),
        ("claude-opus-4-8", True, True, True),
        ("claude-fable-5", True, True, True),
        ("claude-opus-4-7", True, True, False),   # adaptive but NOT effort
        ("claude-sonnet-4-5", True, False, False),
        ("claude-haiku-4-5", True, False, False),
        ("claude-opus-4-1", True, False, False),
        ("claude-3-5-sonnet-20241022", False, False, False),
        (None, False, False, False),
    ]

    def test_capability_matrix(self):
        for model, thinking, adaptive, effort in self.MATRIX:
            self.assertEqual(_model_supports_extended_thinking(model), thinking, model)
            self.assertEqual(_model_supports_adaptive_thinking(model), adaptive, model)
            self.assertEqual(_model_supports_effort(model), effort, model)

    def test_max_effort_allowlist(self):
        # TS modelSupportsMaxEffort (effort.ts:65-77): opus-4-6 only.
        self.assertTrue(_model_supports_max_effort("claude-opus-4-6"))
        self.assertTrue(_model_supports_max_effort("claude-opus-4-6-20260101"))
        for model in ("claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5", None):
            self.assertFalse(_model_supports_max_effort(model), model)


class TestResolveThinkingEffort(unittest.TestCase):
    """Precedence (explicit > settings > default) + max clamping."""

    def _with_settings_effort(self, value):
        from unittest import mock as _mock
        from types import SimpleNamespace

        return _mock.patch(
            "src.settings.settings.get_settings",
            return_value=SimpleNamespace(effort=value),
        )

    def test_explicit_wins_over_settings(self):
        with self._with_settings_effort("low"):
            self.assertEqual(resolve_thinking_effort("high", "claude-opus-4-8"), "high")

    def test_settings_fallback_when_no_explicit(self):
        with self._with_settings_effort("high"):
            self.assertEqual(resolve_thinking_effort(None, "claude-opus-4-8"), "high")

    def test_default_medium_when_neither_set(self):
        with self._with_settings_effort(""):
            self.assertEqual(resolve_thinking_effort(None, "claude-opus-4-8"), "medium")

    def test_max_clamped_off_allowlist(self):
        # Explicit valid values never consult settings — no patch needed.
        self.assertEqual(resolve_thinking_effort("max", "claude-opus-4-8"), "high")
        self.assertEqual(resolve_thinking_effort("max", "claude-fable-5"), "high")

    def test_max_passes_through_on_opus_46(self):
        self.assertEqual(resolve_thinking_effort("max", "claude-opus-4-6"), "max")

    def test_settings_read_failure_falls_back_to_default(self):
        from unittest import mock as _mock

        with _mock.patch(
            "src.settings.settings.get_settings", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(resolve_thinking_effort(None, "claude-opus-4-8"), "medium")

    def test_settings_max_also_clamped_by_model(self):
        with self._with_settings_effort("max"):
            self.assertEqual(resolve_thinking_effort(None, "claude-opus-4-8"), "high")
            self.assertEqual(resolve_thinking_effort(None, "claude-opus-4-6"), "max")


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

    def test_anthropic_sonnet_46_gets_adaptive_and_effort(self):
        # Sonnet 4.6 is on both the adaptive and effort allowlists.
        provider = _make_anthropic_mock("claude-sonnet-4-6")
        kw = self._drive_one_turn(provider)
        self.assertEqual(kw.get("thinking"), {"type": "adaptive"})
        self.assertEqual(kw.get("output_config"), {"effort": "medium"})

    def test_anthropic_sonnet_45_gets_budget_thinking_not_adaptive(self):
        # Sonnet 4.5 supports thinking but NOT the adaptive type, and NOT
        # effort — sending either is a hard 400. It must get a token budget
        # (max_tokens-1) and no output_config. This is the exact bug the
        # subscription evaluation surfaced.
        provider = _make_anthropic_mock("claude-sonnet-4-5")
        kw = self._drive_one_turn(provider)
        thinking = kw.get("thinking") or {}
        self.assertEqual(thinking.get("type"), "enabled")
        self.assertIn("budget_tokens", thinking)
        self.assertGreaterEqual(thinking["budget_tokens"], 1024)
        self.assertLess(thinking["budget_tokens"], kw.get("max_tokens"))
        self.assertNotIn("output_config", kw)

    def test_anthropic_haiku_45_gets_budget_thinking_not_adaptive(self):
        provider = _make_anthropic_mock("claude-haiku-4-5")
        kw = self._drive_one_turn(provider)
        self.assertEqual((kw.get("thinking") or {}).get("type"), "enabled")
        self.assertNotIn("output_config", kw)

    def test_anthropic_opus_47_adaptive_but_no_effort(self):
        # The fix's most fragile case: opus-4-7 is on the adaptive allowlist
        # but NOT the (narrower) effort allowlist. Collapsing the two gates
        # would 400 every opus-4-7 request on an unsupported output_config.
        provider = _make_anthropic_mock("claude-opus-4-7")
        kw = self._drive_one_turn(provider)
        self.assertEqual(kw.get("thinking"), {"type": "adaptive"})
        self.assertNotIn("output_config", kw)

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

    def test_thinking_effort_param_reaches_output_config(self):
        # The --effort/-QueryParams channel: explicit value lands verbatim.
        provider = _make_anthropic_mock("claude-opus-4-8")
        kw = self._drive_one_turn(provider, thinking_effort="high")
        self.assertEqual(kw.get("thinking"), {"type": "adaptive"})
        self.assertEqual(kw.get("output_config"), {"effort": "high"})

    def test_settings_effort_reaches_output_config(self):
        # The persisted /effort setting is honored when no explicit value.
        from unittest import mock as _mock
        from types import SimpleNamespace

        provider = _make_anthropic_mock("claude-opus-4-8")
        with _mock.patch(
            "src.settings.settings.get_settings",
            return_value=SimpleNamespace(effort="high"),
        ):
            kw = self._drive_one_turn(provider)
        self.assertEqual(kw.get("output_config"), {"effort": "high"})

    def test_max_effort_clamped_on_wire_for_non_allowlisted_model(self):
        provider = _make_anthropic_mock("claude-opus-4-8")
        kw = self._drive_one_turn(provider, thinking_effort="max")
        self.assertEqual(kw.get("output_config"), {"effort": "high"})

    def test_explicit_opt_in_overrides_unknown_model(self):
        # An out-of-band model name (e.g. proxy alias) should still get
        # thinking if the caller forces it. Lets users opt into thinking
        # on a custom Claude alias the helper hasn't been taught about.
        # An unknown model isn't on the adaptive allowlist, so it takes the
        # safe budget form (adaptive is the type that 400s when unsupported).
        provider = _make_anthropic_mock("custom-proxy-claude-model")
        kw = self._drive_one_turn(provider, extended_thinking=True)
        self.assertEqual((kw.get("thinking") or {}).get("type"), "enabled")

    def test_custom_effort_propagates(self):
        provider = _make_anthropic_mock("claude-opus-4-6")
        kw = self._drive_one_turn(provider, thinking_effort="high")
        self.assertEqual(kw.get("output_config"), {"effort": "high"})


if __name__ == "__main__":
    unittest.main()
