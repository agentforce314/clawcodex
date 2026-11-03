"""Length-recovery escalation must respect configured ``modelLimits``.

Issue #958: the truncation-escalation lane in ``query()`` retries with
the unconditional ``ESCALATED_MAX_TOKENS`` (64,000). For an
OpenAI-compatible model whose operator configured a smaller output
limit in settings (``modelLimits.<model>.maxOutputTokens`` — the only
limit that exists for private/unknown models), the retry itself
exceeds the endpoint's ceiling and dies with the same class of 400 it
was recovering from: a model configured with a 4,096 output limit was
retried at 64,000 tokens.

These tests drive the real query loop with a mock OpenAI-compatible
provider whose first reply truncates (``finish_reason="length"``) and
assert the ``max_tokens`` the retry actually carries:

- a configured limit caps the escalation (unknown model, issue case);
- a configured limit also caps a known/catalogued model — the
  configured value is the operator's statement of their endpoint's
  ceiling, and the old code bypassed ``modelLimits`` entirely;
- with no configured limit the historical 64,000 is preserved exactly,
  for known and unknown models alike (the per-model table's
  ``max_output_tokens`` is a first-attempt convention, not a ceiling);
- a configured limit above 64,000 does not raise the escalation.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.providers.base import ChatResponse
from src.query.query import ESCALATED_MAX_TOKENS, QueryParams, query
from src.settings.types import ModelLimitSettings, SettingsSchema
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


def _settings(limits: dict[str, ModelLimitSettings]) -> SettingsSchema:
    return SettingsSchema(model_limits=limits)


class TestEscalationRespectsModelLimits(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=Path(self.temp_dir.name))
        self.abort = AbortController()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _retry_max_tokens(self, model: str, limits) -> int:
        """Run one truncated turn; return the retry's ``max_tokens``."""
        provider = MagicMock()
        provider.model = model
        provider.base_url = "http://localhost:4000/v1"
        provider.chat_stream_response.side_effect = NotImplementedError()
        truncated = ChatResponse(
            content="Partial output...",
            model=model,
            usage={"input_tokens": 10, "output_tokens": 4000},
            finish_reason="length",  # OpenAI vocabulary for truncation
            tool_uses=None,
        )
        full = ChatResponse(
            content="Complete output.",
            model=model,
            usage={"input_tokens": 10, "output_tokens": 500},
            finish_reason="stop",
            tool_uses=None,
        )
        provider.chat.side_effect = [truncated, full]
        params = QueryParams(
            messages=[UserMessage(content="Greet briefly")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=self.abort,
            max_turns=10,
        )

        async def run():
            async for _ in query(params):
                pass

        with patch(
            "src.settings.settings.get_settings",
            return_value=_settings(limits),
        ):
            asyncio.run(run())

        self.assertEqual(provider.chat.call_count, 2)
        second_call = provider.chat.call_args_list[1]
        return second_call[1].get("max_tokens")

    def test_configured_unknown_model_caps_escalation(self):
        """The issue's case: ``tsubasa-fast`` configured with a 4,096
        output limit must be retried at 4,096, not 64,000."""
        limits = {
            "tsubasa-fast": ModelLimitSettings(
                context_window=32_768, max_output_tokens=4_096
            )
        }
        self.assertEqual(self._retry_max_tokens("tsubasa-fast", limits), 4_096)

    def test_configured_limit_caps_known_model_too(self):
        """A configured limit binds even when the model also has a
        catalog entry: ``modelLimits`` is the operator's ceiling for
        their endpoint (e.g. a private gateway reselling a known id)."""
        limits = {"claude-opus-4-8": ModelLimitSettings(max_output_tokens=8_192)}
        self.assertEqual(self._retry_max_tokens("claude-opus-4-8", limits), 8_192)

    def test_no_configured_limit_preserves_escalation(self):
        """Guard: with nothing configured, the retry still escalates to
        the full 64,000 — for a known model and for an unknown one."""
        self.assertEqual(
            self._retry_max_tokens("claude-opus-4-8", {}),
            ESCALATED_MAX_TOKENS,
        )
        self.assertEqual(
            self._retry_max_tokens("tsubasa-fast", {}),
            ESCALATED_MAX_TOKENS,
        )

    def test_configured_limit_above_escalation_does_not_raise_it(self):
        """Guard: a generous configured limit never inflates the retry
        past the historical 64,000."""
        limits = {"tsubasa-fast": ModelLimitSettings(max_output_tokens=100_000)}
        self.assertEqual(
            self._retry_max_tokens("tsubasa-fast", limits),
            ESCALATED_MAX_TOKENS,
        )


if __name__ == "__main__":
    unittest.main()
