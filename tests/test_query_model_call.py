"""
Parity target: B3. Query refactor I / model call boundary
Reference target: Claude Code 2.1.88
Contract:
  - streaming provider 是首选路径；
  - provider 不支持 streaming 时回退 chat；
  - abort/error 不在 transport 提取层被吞掉。
Allowed divergence:
  - Python 后台调用通过 asyncio.to_thread 避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.providers.base import ChatResponse
from src.query.model_call import invoke_provider
from src.utils.abort_controller import AbortError


def _response(text: str) -> ChatResponse:
    return ChatResponse(
        content=text,
        model="test",
        usage={"input_tokens": 1, "output_tokens": 1},
        finish_reason="end_turn",
    )


def test_invoke_provider_prefers_streaming() -> None:
    provider = MagicMock()
    provider.chat_stream_response.return_value = _response("stream")

    result = asyncio.run(invoke_provider(
        provider=provider,
        api_messages=[{"role": "user", "content": "hi"}],
        call_kwargs={"tools": []},
    ))

    assert result.content == "stream"
    provider.chat_stream_response.assert_called_once()
    provider.chat.assert_not_called()


def test_invoke_provider_falls_back_to_chat_and_emits_text() -> None:
    provider = MagicMock()
    provider.chat_stream_response.side_effect = NotImplementedError()
    provider.chat.return_value = _response("fallback")
    chunks: list[str] = []

    result = asyncio.run(invoke_provider(
        provider=provider,
        api_messages=[{"role": "user", "content": "hi"}],
        call_kwargs={"tools": []},
        on_text_chunk=chunks.append,
    ))

    assert result.content == "fallback"
    assert "".join(chunks) == "fallback"
    provider.chat.assert_called_once()


def test_invoke_provider_propagates_abort() -> None:
    provider = MagicMock()
    provider.chat_stream_response.side_effect = AbortError("cancelled")

    with pytest.raises(AbortError):
        asyncio.run(invoke_provider(
            provider=provider,
            api_messages=[{"role": "user", "content": "hi"}],
            call_kwargs={"tools": []},
        ))
