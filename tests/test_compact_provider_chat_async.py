"""Regression tests for the provider interface compaction depends on.

Two bugs broke context compaction on OpenAI-compatible providers
(DeepSeek/GLM/OpenAI/OpenRouter), surfacing as::

    Compact LLM call failed: 'DeepSeekProvider' object has no attribute
    'chat_async', sync fallback: Completions.create() got an unexpected
    keyword argument 'system', using text extraction

1. No provider implemented ``chat_async``; ``compact_conversation`` calls it
   unguarded, so the first attempt always raised ``AttributeError`` and fell
   through to the sync fallback.
2. The sync fallback (and the async path) passed an Anthropic-style
   ``system=`` kwarg, which ``OpenAICompatibleProvider`` forwarded straight
   into ``completions.create()`` — a ``TypeError``. Compaction then degraded
   to the low-quality text-extraction fallback.

The existing ``test_compact.py`` suite mocks ``provider.chat_async`` directly,
so it never exercised either real code path. These tests use real providers.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import MagicMock

from src.providers.base import BaseProvider, ChatResponse
from src.providers.openai_compatible import OpenAICompatibleProvider
from src.types.content_blocks import TextBlock
from src.types.messages import Message, UserMessage, AssistantMessage
from src.services.compact.compact import (
    CompactContext,
    CompactionResult,
    compact_conversation,
)


class _SyncOnlyProvider(BaseProvider):
    """Provider exposing only the synchronous ``chat`` — like every real
    provider before the fix (none defined ``chat_async``)."""

    def __init__(self) -> None:
        super().__init__(api_key="k", model="m")
        self.calls: list[dict] = []
        self.chat_thread: int | None = None

    def chat(self, messages, tools=None, **kwargs) -> ChatResponse:
        self.chat_thread = threading.get_ident()
        self.calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        return ChatResponse(
            content="SUMMARY_TEXT", model="m", usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="stop",
        )

    def chat_stream(self, messages, tools=None, **kwargs):
        yield "x"

    def get_available_models(self):
        return ["m"]


class _FakeOpenAICompatProvider(OpenAICompatibleProvider):
    """An OpenAI-compatible provider whose SDK client is a stub that faithfully
    rejects a ``system=`` kwarg, exactly like the real ``Completions.create``."""

    def __init__(self, summary: str = "REAL_LLM_SUMMARY") -> None:
        super().__init__(api_key="k", model="m")
        self.create_kwargs: dict = {}
        self._summary = summary
        self._client = self._make_stub_client()

    def _make_stub_client(self):
        provider = self

        def fake_create(**kwargs):
            provider.create_kwargs = kwargs
            if "system" in kwargs:
                raise TypeError(
                    "Completions.create() got an unexpected keyword argument 'system'"
                )
            msg = MagicMock()
            msg.content = provider._summary
            msg.tool_calls = None
            msg.reasoning_content = None
            choice = MagicMock()
            choice.message = msg
            choice.finish_reason = "stop"
            resp = MagicMock()
            resp.choices = [choice]
            resp.model = "m"
            resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            return resp

        client = MagicMock()
        client.chat.completions.create.side_effect = fake_create
        client.with_options.return_value = client  # see _apply_client_timeout
        return client

    def _create_client(self):  # pragma: no cover - client injected in __init__
        return self._client

    def get_available_models(self):
        return ["m"]


def _make_messages(count: int = 4) -> list[Message]:
    messages: list[Message] = []
    for i in range(count):
        messages.append(UserMessage(content=f"User message {i} " * 20))
        messages.append(AssistantMessage(content=[TextBlock(text=f"Assistant {i} " * 20)]))
    return messages


def _summary_text(result: CompactionResult) -> str:
    """Concatenate the text of the produced summary message(s)."""
    out: list[str] = []
    for msg in result.summary_messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, TextBlock):
                    out.append(block.text)
                elif isinstance(block, dict):
                    out.append(block.get("text", ""))
    return "\n".join(out)


class TestBaseProviderChatAsync(unittest.TestCase):
    """``BaseProvider.chat_async`` is the async entry point compaction needs."""

    def test_chat_async_exists_on_every_provider(self):
        # The reported AttributeError was 'no attribute chat_async'.
        self.assertTrue(hasattr(_SyncOnlyProvider(), "chat_async"))

    def test_chat_async_runs_sync_chat_and_forwards_kwargs(self):
        provider = _SyncOnlyProvider()
        resp = asyncio.run(
            provider.chat_async(
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                model="m",
                max_tokens=10,
                system="SYS",
            )
        )
        self.assertEqual(resp.content, "SUMMARY_TEXT")
        self.assertEqual(len(provider.calls), 1)
        # Every kwarg (including system) reaches the sync chat untouched.
        self.assertEqual(provider.calls[0]["kwargs"]["system"], "SYS")
        self.assertEqual(provider.calls[0]["kwargs"]["max_tokens"], 10)

    def test_chat_async_offloads_to_worker_thread(self):
        # Must not run inline on the event-loop thread (would block the loop /
        # the whole turn during compaction).
        provider = _SyncOnlyProvider()
        asyncio.run(provider.chat_async(messages=[{"role": "user", "content": "hi"}]))
        self.assertIsNotNone(provider.chat_thread)
        self.assertNotEqual(provider.chat_thread, threading.get_ident())


class TestOpenAICompatSystemKwarg(unittest.TestCase):
    """``system=`` must become a leading system message, never an SDK kwarg."""

    def test_system_kwarg_becomes_leading_message(self):
        provider = _FakeOpenAICompatProvider()
        resp = provider.chat(
            [{"role": "user", "content": "hello"}],
            tools=None,
            model="m",
            max_tokens=8192,
            system="SUMMARIZE_SYSTEM",
        )
        self.assertEqual(resp.content, "REAL_LLM_SUMMARY")
        # system not forwarded to the SDK (would raise TypeError otherwise).
        self.assertNotIn("system", provider.create_kwargs)
        sent = provider.create_kwargs["messages"]
        self.assertEqual(sent[0], {"role": "system", "content": "SUMMARIZE_SYSTEM"})
        self.assertEqual(sent[1]["role"], "user")

    def test_no_system_kwarg_leaves_messages_unchanged(self):
        provider = _FakeOpenAICompatProvider()
        provider.chat([{"role": "user", "content": "hello"}], tools=None, model="m")
        sent = provider.create_kwargs["messages"]
        self.assertEqual([m["role"] for m in sent], ["user"])

    def test_empty_system_kwarg_adds_no_message(self):
        provider = _FakeOpenAICompatProvider()
        provider.chat([{"role": "user", "content": "hi"}], model="m", system="")
        sent = provider.create_kwargs["messages"]
        self.assertEqual([m["role"] for m in sent], ["user"])

    def test_list_system_blocks_are_joined(self):
        provider = _FakeOpenAICompatProvider()
        provider.chat(
            [{"role": "user", "content": "hi"}],
            model="m",
            system=[{"type": "text", "text": "A"}, {"type": "text", "text": "B"}],
        )
        sent = provider.create_kwargs["messages"]
        self.assertEqual(sent[0], {"role": "system", "content": "A\nB"})


class TestCompactionWithRealProvider(unittest.TestCase):
    """End-to-end: the exact path that failed for DeepSeek must now produce a
    real LLM summary instead of the text-extraction fallback."""

    def test_compaction_uses_llm_summary_not_fallback(self):
        provider = _FakeOpenAICompatProvider(summary="THE_REAL_SUMMARY")
        ctx = CompactContext(
            provider=provider,
            model="m",
            messages=_make_messages(4),
            trigger="manual",
        )
        result = asyncio.run(compact_conversation(ctx))

        self.assertIsInstance(result, CompactionResult)
        summary = _summary_text(result)
        # The LLM summary is used...
        self.assertIn("THE_REAL_SUMMARY", summary)
        # ...and the text-extraction fallback marker is NOT present.
        self.assertNotIn("Conversation had", summary)
        # The summarizer system prompt was supplied as a leading message.
        self.assertNotIn("system", provider.create_kwargs)
        self.assertEqual(provider.create_kwargs["messages"][0]["role"], "system")
        # Usage was captured from the real call.
        self.assertEqual(result.compaction_usage, {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


if __name__ == "__main__":
    unittest.main()
