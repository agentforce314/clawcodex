"""The truncation-escalation retry must respect known output limits (#958).

When a reply is cut off, the query loop re-sends the same request once with
a larger ``max_tokens`` (``ESCALATED_MAX_TOKENS``, 64,000). That value used to
be unconditional, so a model whose endpoint accepts less (the issue's
``tsubasa-fast``, configured with ``maxOutputTokens: 4096``) had the retry
itself rejected, and the run died on the 400 it was recovering from.

These tests drive the real ``query()`` loop on BOTH wires (an
``AnthropicProvider``, which sends ``max_tokens`` on every request, and an
``OpenAIProvider`` on a loopback gateway, which sends none on the first
attempt) and assert what each request actually carries:

* the retry is bounded by the output ceiling: ``modelLimits`` for an
  uncatalogued model, a catalog row's recorded maximum for a known one, and
  ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``;
* the retry is bounded by the context window left after the input;
* a retry that could not allow more output than the truncated request had is
  skipped, and the loop goes straight to the recovery lane with the partial
  reply kept (never an identical re-send, never a lower "escalation");
* with no known limit the historical 64,000 is unchanged;
* ``modelLimits`` lookups survive a malformed config and match model ids
  that contain a colon;
* compaction never asks for more than an ordinary request for the model.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.context import (
    get_context_window_for_model,
    get_max_output_tokens_ceiling,
    get_model_max_output_tokens,
)
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import ChatResponse
from src.providers.openai_provider import OpenAIProvider
from src.query.query import ESCALATED_MAX_TOKENS, QueryParams, query
from src.services.compact.compact import (
    COMPACT_MAX_OUTPUT_TOKENS,
    CompactContext,
    compact_conversation,
)
from src.settings.types import ModelLimitSettings, SettingsSchema
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.content_blocks import TextBlock
from src.types.messages import AssistantMessage, UserMessage
from src.utils.abort_controller import AbortController

PARTIAL = "Partial reply, cut off mid-"
RESUME_NUDGE = "Output token limit hit."
GATEWAY = "http://localhost:4000/v1"


def _settings(limits=None) -> SettingsSchema:
    return SettingsSchema(model_limits=dict(limits or {}))


def _reply(*, finish: str, input_tokens: int, output_tokens: int, content: str):
    return ChatResponse(
        content=content,
        model="test",
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        finish_reason=finish,
        tool_uses=None,
    )


def _truncated(*, wire: str, input_tokens: int = 120, output_tokens: int):
    return _reply(
        finish="length" if wire == "openai" else "max_tokens",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        content=PARTIAL,
    )


def _done():
    return _reply(finish="stop", input_tokens=120, output_tokens=40, content="Done.")


def _openai(model: str, base_url: str = GATEWAY) -> OpenAIProvider:
    return OpenAIProvider(api_key="k", base_url=base_url, model=model)


def _anthropic(model: str, base_url: str | None = None) -> AnthropicProvider:
    return AnthropicProvider(api_key="k", base_url=base_url, model=model)


class _EnvIsolated(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", None)


class _LoopCase(_EnvIsolated):
    def setUp(self):
        super().setUp()
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=Path(temp_dir.name))

    def _run(self, provider, replies, settings: SettingsSchema | None = None):
        """Run one turn against ``replies``; return ``(args, kwargs)`` per call."""
        provider.chat_stream_response = MagicMock(side_effect=NotImplementedError())
        provider.chat = MagicMock(side_effect=list(replies))
        params = QueryParams(
            messages=[UserMessage(content="Greet briefly")],
            system_prompt="You are helpful.",
            tools=self.registry.list_tools(),
            tool_registry=self.registry,
            tool_use_context=self.context,
            provider=provider,
            abort_controller=AbortController(),
            max_turns=10,
        )

        async def run():
            async for _ in query(params):
                pass

        with patch(
            "src.settings.settings.get_settings",
            return_value=settings or _settings(),
        ):
            asyncio.run(run())
        return [(c.args, c.kwargs) for c in provider.chat.call_args_list]

    def assertRecoveryCall(self, call):
        """The call continues the partial reply rather than re-sending."""
        sent = str(call[0][0])
        self.assertIn(PARTIAL, sent)
        self.assertIn(RESUME_NUDGE, sent)

    def assertEscalationCall(self, call, max_tokens: int):
        """The call re-sends the original request at a larger ``max_tokens``."""
        self.assertEqual(call[1].get("max_tokens"), max_tokens)
        sent = str(call[0][0])
        self.assertNotIn(PARTIAL, sent)
        self.assertNotIn(RESUME_NUDGE, sent)


class TestEscalationOnOpenAIWire(_LoopCase):
    """First attempt carries no ``max_tokens``; the endpoint picks."""

    def test_configured_cap_bounds_the_retry(self):
        """The issue's case: an endpoint default (1,024) below the 4,096 cap
        leaves room to escalate, but only to 4,096, never 64,000."""
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(
                context_window=32_768, max_output_tokens=4_096
            )
        })
        calls = self._run(
            _openai("tsubasa-fast"),
            [_truncated(wire="openai", output_tokens=1_024), _done()],
            settings,
        )
        self.assertEqual(len(calls), 2)
        self.assertNotIn("max_tokens", calls[0][1])
        self.assertEscalationCall(calls[1], 4_096)

    def test_retry_that_cannot_exceed_the_cap_is_skipped(self):
        """The reply already used the whole 4,096: a retry at 4,096 would be
        cut at the same point, so the loop goes straight to recovery."""
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(max_output_tokens=4_096)
        })
        calls = self._run(
            _openai("tsubasa-fast"),
            [_truncated(wire="openai", output_tokens=4_096), _done()],
            settings,
        )
        self.assertEqual(len(calls), 2)
        self.assertNotIn("max_tokens", calls[1][1])
        self.assertRecoveryCall(calls[1])

    def test_retry_fits_the_known_context_window(self):
        """20,000 input tokens in a 32,768 window leave 11,768 after the
        1,000-token margin, so the retry asks for that, not 64,000."""
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(context_window=32_768)
        })
        calls = self._run(
            _openai("tsubasa-fast"),
            [
                _truncated(wire="openai", input_tokens=20_000, output_tokens=2_000),
                _done(),
            ],
            settings,
        )
        self.assertEscalationCall(calls[1], 32_768 - 20_000 - 1_000)

    def test_full_context_window_skips_the_retry(self):
        """Only 1,268 tokens of window are left, fewer than the 1,500 already
        produced, so no larger retry is possible."""
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(context_window=32_768)
        })
        calls = self._run(
            _openai("tsubasa-fast"),
            [
                _truncated(wire="openai", input_tokens=30_500, output_tokens=1_500),
                _done(),
            ],
            settings,
        )
        self.assertNotIn("max_tokens", calls[1][1])
        self.assertRecoveryCall(calls[1])

    def test_known_model_uses_its_catalog_ceiling(self):
        """gpt-4o's documented maximum is 16,384 completion tokens."""
        calls = self._run(
            _openai("gpt-4o", base_url="https://api.openai.com/v1"),
            [_truncated(wire="openai", output_tokens=4_000), _done()],
        )
        self.assertEscalationCall(calls[1], 16_384)

    def test_unknown_model_without_limits_keeps_64k(self):
        calls = self._run(
            _openai("tsubasa-fast"),
            [_truncated(wire="openai", output_tokens=4_000), _done()],
        )
        self.assertEscalationCall(calls[1], ESCALATED_MAX_TOKENS)

    def test_configured_cap_above_64k_does_not_raise_the_retry(self):
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(max_output_tokens=100_000)
        })
        calls = self._run(
            _openai("tsubasa-fast"),
            [_truncated(wire="openai", output_tokens=4_000), _done()],
            settings,
        )
        self.assertEscalationCall(calls[1], ESCALATED_MAX_TOKENS)

    def test_env_cap_bounds_the_retry(self):
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "6000"
        calls = self._run(
            _openai("tsubasa-fast"),
            [_truncated(wire="openai", output_tokens=2_000), _done()],
        )
        self.assertEscalationCall(calls[1], 6_000)

    def test_colon_model_id_matches_its_bare_key(self):
        """Ollama-style ids carry a colon; a bare exact key must apply."""
        settings = _settings({
            "llama3.1:8b": ModelLimitSettings(max_output_tokens=4_096)
        })
        calls = self._run(
            _openai("llama3.1:8b", base_url="http://localhost:11434/v1"),
            [_truncated(wire="openai", output_tokens=1_000), _done()],
            settings,
        )
        self.assertEscalationCall(calls[1], 4_096)

    def test_malformed_model_limits_do_not_crash_the_retry(self):
        """``"modelLimits": [...]`` survives ``from_dict`` as a list; it must
        read as "nothing configured", not raise out of ``query()``."""
        settings = SettingsSchema.from_dict({"modelLimits": ["tsubasa-fast"]})
        self.assertIsInstance(settings.model_limits, list)
        calls = self._run(
            _openai("tsubasa-fast"),
            [_truncated(wire="openai", output_tokens=4_000), _done()],
            settings,
        )
        self.assertEscalationCall(calls[1], ESCALATED_MAX_TOKENS)


class TestEscalationOnAnthropicWire(_LoopCase):
    """Every request carries ``max_tokens``, the first one included."""

    def test_configured_cap_is_not_re_sent_identically(self):
        """The first attempt already asked for the whole 4,096 cap, so the
        old bounded retry would have been byte-identical. Recovery instead."""
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(max_output_tokens=4_096)
        })
        calls = self._run(
            _anthropic("tsubasa-fast", base_url="http://localhost:4000"),
            [_truncated(wire="anthropic", output_tokens=4_096), _done()],
            settings,
        )
        self.assertEqual([c[1]["max_tokens"] for c in calls], [4_096, 4_096])
        self.assertRecoveryCall(calls[1])

    def test_current_model_keeps_the_32k_to_64k_escalation(self):
        calls = self._run(
            _anthropic("claude-opus-4-8"),
            [_truncated(wire="anthropic", output_tokens=32_000), _done()],
        )
        self.assertEqual(calls[0][1]["max_tokens"], 32_000)
        self.assertEscalationCall(calls[1], ESCALATED_MAX_TOKENS)

    def test_legacy_model_ceiling_skips_the_retry(self):
        """claude-3-5-haiku accepts at most 8,192, which the first attempt
        already asked for; 64,000 would be rejected outright."""
        calls = self._run(
            _anthropic("claude-3-5-haiku-20241022"),
            [_truncated(wire="anthropic", output_tokens=8_192), _done()],
        )
        self.assertEqual([c[1]["max_tokens"] for c in calls], [8_192, 8_192])
        self.assertRecoveryCall(calls[1])

    def test_model_limits_never_lower_a_catalogued_model(self):
        """The catalog is authoritative (README ``settings.modelLimits``): an
        exact key or a broad ``claude`` prefix changes neither the first
        attempt nor the escalation, so they can never disagree."""
        settings = _settings({
            "claude-opus-4-8": ModelLimitSettings(max_output_tokens=8_192),
            "claude": ModelLimitSettings(max_output_tokens=4_096),
        })
        calls = self._run(
            _anthropic("claude-opus-4-8"),
            [_truncated(wire="anthropic", output_tokens=32_000), _done()],
            settings,
        )
        self.assertEqual(calls[0][1]["max_tokens"], 32_000)
        self.assertEscalationCall(calls[1], ESCALATED_MAX_TOKENS)

    def test_env_cap_disables_the_escalation(self):
        """TS skips the escalation while CLAUDE_CODE_MAX_OUTPUT_TOKENS is set;
        here the env value is both the first attempt and the ceiling."""
        os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "16000"
        calls = self._run(
            _anthropic("claude-opus-4-8"),
            [_truncated(wire="anthropic", output_tokens=16_000), _done()],
        )
        self.assertEqual([c[1]["max_tokens"] for c in calls], [16_000, 16_000])
        self.assertRecoveryCall(calls[1])

    def test_malformed_model_limits_do_not_crash_the_first_attempt(self):
        """Every Anthropic request resolves ``max_tokens`` through
        ``modelLimits`` for an uncatalogued model, so a list-shaped value
        used to raise before the first request was sent."""
        settings = SettingsSchema.from_dict({"modelLimits": ["tsubasa-fast"]})
        calls = self._run(
            _anthropic("tsubasa-fast", base_url="http://localhost:4000"),
            [_truncated(wire="anthropic", output_tokens=8_192), _done()],
            settings,
        )
        self.assertEqual(calls[0][1]["max_tokens"], 8_192)
        self.assertEscalationCall(calls[1], ESCALATED_MAX_TOKENS)


class TestModelLimitLookups(_EnvIsolated):
    def _with(self, settings):
        return patch("src.settings.settings.get_settings", return_value=settings)

    def test_colon_keys_bare_and_host_qualified(self):
        settings = _settings({
            "llama3.1:8b": ModelLimitSettings(context_window=8_192),
            "localhost:11434:llama3.1:8b": ModelLimitSettings(
                context_window=32_768
            ),
            "gpu-box:11434:qwen3:32b": ModelLimitSettings(context_window=65_536),
        })
        with self._with(settings):
            self.assertEqual(get_context_window_for_model("llama3.1:8b"), 8_192)
            self.assertEqual(
                get_context_window_for_model(
                    "llama3.1:8b", base_url="http://localhost:11434/v1"
                ),
                32_768,
            )
            # Qualified for another host: never matches here.
            self.assertEqual(
                get_context_window_for_model(
                    "qwen3:32b", base_url="http://localhost:11434/v1"
                ),
                200_000,
            )

    def test_non_mapping_model_limits_read_as_unset(self):
        for raw in (["tsubasa-fast"], "tsubasa-fast", 4_096):
            with self.subTest(raw=raw), self._with(
                SettingsSchema(model_limits=raw)  # type: ignore[arg-type]
            ):
                self.assertEqual(get_context_window_for_model("tsubasa-fast"), 200_000)
                self.assertEqual(get_model_max_output_tokens("tsubasa-fast"), 8_192)
                self.assertIsNone(get_max_output_tokens_ceiling("tsubasa-fast"))

    def test_output_ceiling_sources(self):
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(max_output_tokens=4_096),
            "claude-opus-4-8": ModelLimitSettings(max_output_tokens=8_192),
        })
        with self._with(settings):
            self.assertEqual(get_max_output_tokens_ceiling("tsubasa-fast"), 4_096)
            self.assertIsNone(get_max_output_tokens_ceiling("other-private"))
            # A row with a recorded maximum, including via its prefix claim.
            self.assertEqual(
                get_max_output_tokens_ceiling("claude-3-5-haiku-latest"), 8_192
            )
            # A row without one stays unknown, whatever modelLimits says.
            self.assertIsNone(get_max_output_tokens_ceiling("claude-opus-4-8"))
            # Prefix claims that reach newer, larger models record nothing.
            self.assertIsNone(get_max_output_tokens_ceiling("claude-opus-4-6"))


def _conversation() -> list:
    messages: list = []
    for i in range(4):
        messages.append(UserMessage(content=f"User message {i} " * 20))
        messages.append(
            AssistantMessage(content=[TextBlock(text=f"Assistant reply {i} " * 20)])
        )
    return messages


class TestCompactionRespectsLimits(_EnvIsolated):
    def _summary_max_tokens(self, model: str, settings=None) -> int:
        provider = MagicMock()
        provider.base_url = GATEWAY
        provider.chat_async = AsyncMock(
            return_value=ChatResponse(
                content="Summary.",
                model=model,
                usage={"input_tokens": 500, "output_tokens": 50},
                finish_reason="stop",
            )
        )
        ctx = CompactContext(
            provider=provider,
            model=model,
            messages=_conversation(),
            trigger="manual",
        )
        with patch(
            "src.settings.settings.get_settings",
            return_value=settings or _settings(),
        ):
            asyncio.run(compact_conversation(ctx))
        return provider.chat_async.call_args.kwargs["max_tokens"]

    def test_configured_cap_bounds_the_summary_request(self):
        settings = _settings({
            "tsubasa-fast": ModelLimitSettings(max_output_tokens=4_096)
        })
        self.assertEqual(self._summary_max_tokens("tsubasa-fast", settings), 4_096)

    def test_legacy_catalog_limit_bounds_the_summary_request(self):
        self.assertEqual(
            self._summary_max_tokens("claude-3-haiku-20240307"), 4_096
        )

    def test_unconstrained_model_keeps_the_compaction_default(self):
        self.assertEqual(
            self._summary_max_tokens("tsubasa-fast"), COMPACT_MAX_OUTPUT_TOKENS
        )
        self.assertEqual(
            self._summary_max_tokens("claude-opus-4-8"), COMPACT_MAX_OUTPUT_TOKENS
        )


if __name__ == "__main__":
    unittest.main()
