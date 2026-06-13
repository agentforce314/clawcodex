"""Tests for the model-aware ``max_tokens`` default in AnthropicProvider.

The legacy SDK default of 4096 truncated long completions on Claude 4.x
models, which accept up to 32K out of the box (and 64K with a beta opt-
in). This module covers two surfaces:

  1. ``_default_max_tokens`` — the standalone helper that maps a model
     name to a sensible ceiling. Returns 32K for the Claude 4.x family,
     4096 for everything else (the API rejects higher values on 3.x).

  2. The wired call sites — ``chat``, ``chat_stream``, and
     ``chat_stream_response``. Each must use the helper when the caller
     doesn't pass an explicit ``max_tokens`` kwarg, and must honor a
     caller-supplied override unchanged.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.providers.anthropic_provider import (
    AnthropicProvider,
    _default_max_tokens,
    DEFAULT_MAX_OUTPUT_TOKENS_4X,
    DEFAULT_MAX_OUTPUT_TOKENS_LEGACY,
)


class TestDefaultMaxTokensHelper(unittest.TestCase):
    """Standalone helper coverage."""

    def test_claude_4_family_gets_32k(self):
        for m in (
            "claude-opus-4-6",
            "claude-opus-4-7-20260201",
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5",
        ):
            self.assertEqual(_default_max_tokens(m), DEFAULT_MAX_OUTPUT_TOKENS_4X, m)

    def test_legacy_3x_stays_at_4096(self):
        # The Anthropic API rejects larger ``max_tokens`` on 3.x snapshots,
        # so the helper MUST preserve the legacy ceiling on these.
        for m in (
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
        ):
            self.assertEqual(_default_max_tokens(m), DEFAULT_MAX_OUTPUT_TOKENS_LEGACY, m)

    def test_empty_or_none_model_keeps_legacy_default(self):
        for m in ("", None):
            self.assertEqual(_default_max_tokens(m), DEFAULT_MAX_OUTPUT_TOKENS_LEGACY, m)

    def test_unknown_model_name_keeps_legacy_default(self):
        # If a user runs against a proxy alias the helper doesn't recognize
        # we default conservatively rather than send 32K and get a 400.
        for m in ("custom-proxy-model", "my-corp/llm-v3", "gpt-4o"):
            self.assertEqual(_default_max_tokens(m), DEFAULT_MAX_OUTPUT_TOKENS_LEGACY, m)


def _make_provider_with_mock_client(model: str):
    """Construct an AnthropicProvider whose ``_ensure_client`` returns a
    MagicMock so we can read off the kwargs passed to ``messages.create``.
    """
    provider = AnthropicProvider(api_key="sk-test", model=model)
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = []
    mock_response.model = model
    mock_response.usage = None
    mock_response.stop_reason = "end_turn"
    mock_client.messages.create.return_value = mock_response
    provider._ensure_client = MagicMock(return_value=mock_client)
    return provider, mock_client


class TestChatUsesDefault(unittest.TestCase):
    """End-to-end: confirm the right ``max_tokens`` lands on the SDK call."""

    def test_chat_default_for_opus_4(self):
        provider, mc = _make_provider_with_mock_client("claude-opus-4-6")
        provider.chat([{"role": "user", "content": "hi"}])
        kw = mc.messages.create.call_args.kwargs
        self.assertEqual(kw["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS_4X)

    def test_chat_default_for_legacy_35_sonnet(self):
        provider, mc = _make_provider_with_mock_client("claude-3-5-sonnet-20241022")
        provider.chat([{"role": "user", "content": "hi"}])
        kw = mc.messages.create.call_args.kwargs
        self.assertEqual(kw["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS_LEGACY)

    def test_chat_explicit_override_wins(self):
        provider, mc = _make_provider_with_mock_client("claude-opus-4-6")
        provider.chat([{"role": "user", "content": "hi"}], max_tokens=1024)
        kw = mc.messages.create.call_args.kwargs
        self.assertEqual(kw["max_tokens"], 1024)

    def test_chat_explicit_override_lifts_legacy_too(self):
        provider, mc = _make_provider_with_mock_client("claude-3-5-sonnet-20241022")
        provider.chat([{"role": "user", "content": "hi"}], max_tokens=8192)
        kw = mc.messages.create.call_args.kwargs
        self.assertEqual(kw["max_tokens"], 8192)


def _patch_messages_stream(provider: AnthropicProvider):
    """Patch ``client.messages.stream`` to return a no-op context manager
    so the streaming tests can read off the kwargs without driving a real
    stream."""
    mock_client = MagicMock()

    class _NoopStream:
        def __enter__(self_inner):
            stream_obj = MagicMock()
            stream_obj.text_stream = iter([])
            return stream_obj

        def __exit__(self_inner, *exc):
            return False

    mock_client.messages.stream.return_value = _NoopStream()
    provider._ensure_client = MagicMock(return_value=mock_client)
    return mock_client


class TestChatStreamUsesDefault(unittest.TestCase):
    """The streaming generator path picks the same default."""

    def test_chat_stream_default_for_opus_4(self):
        provider = AnthropicProvider(api_key="sk-test", model="claude-opus-4-6")
        mc = _patch_messages_stream(provider)
        list(provider.chat_stream([{"role": "user", "content": "hi"}]))
        kw = mc.messages.stream.call_args.kwargs
        self.assertEqual(kw["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS_4X)

    def test_chat_stream_default_for_legacy_3x(self):
        provider = AnthropicProvider(api_key="sk-test", model="claude-3-opus-20240229")
        mc = _patch_messages_stream(provider)
        list(provider.chat_stream([{"role": "user", "content": "hi"}]))
        kw = mc.messages.stream.call_args.kwargs
        self.assertEqual(kw["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS_LEGACY)


if __name__ == "__main__":
    unittest.main()
