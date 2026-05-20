"""Pin that ImageSizeError from the streaming path propagates cleanly
into the canonical ``_call_model_sync`` (query.py) — it must NOT be
silently retried via the non-streaming ``chat()`` fallback (which would
re-run the same ``_prepare_messages`` and re-raise) and it must surface
as a typed media_size error message AssistantMessage so the outer query
loop's ``_is_withheld_media_size`` recovery path engages.

Originally tested against ``agent_loop._call_provider_for_turn``;
after Stage 4 consolidation, the equivalent code lives in
``src.query.query._call_model_sync``. The invariant is the same — only
the call site is different.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.query.query import _call_model_sync
from src.types.messages import UserMessage
from src.utils.image_validation import ImageSizeError


def _run(provider: MagicMock) -> tuple[Any, list[Any]]:
    """Drive ``_call_model_sync`` once with a single user message."""
    return asyncio.run(_call_model_sync(
        provider=provider,
        messages=[UserMessage(content="x")],
        system_prompt="hi",
        tools=[],
    ))


class TestImageSizeErrorPropagation(unittest.TestCase):
    def _make_provider(
        self,
        stream_exc: Exception,
        chat_response: Any = None,
    ) -> MagicMock:
        provider = MagicMock()
        provider.model = "claude-test"
        provider.chat_stream_response.side_effect = stream_exc
        if chat_response is None:
            # Should not be reached in the propagation test; trip if so.
            provider.chat.side_effect = AssertionError(
                "chat() should not be invoked after streaming ImageSizeError"
            )
        else:
            provider.chat.return_value = chat_response
        return provider

    def test_streaming_imagesize_surfaces_as_media_size_error_message(self) -> None:
        # query.py catches ImageSizeError at the outer try and returns
        # a synthetic AssistantMessage tagged with
        # ``_api_error = "media_size"``. That's the contract the
        # recovery path (B.1 in the query loop) reads to withhold +
        # reactive-compact. Test the user-visible invariant: the error
        # surfaces as a typed AssistantMessage, and ``chat()`` does
        # NOT get retried (which would re-trigger the same error).
        oversize = ImageSizeError([(6 * 1024 * 1024, 5 * 1024 * 1024)])
        provider = self._make_provider(oversize)
        result_msgs, tool_use_blocks = _run(provider)
        self.assertEqual(len(result_msgs), 1)
        self.assertTrue(getattr(result_msgs[0], "isApiErrorMessage", False))
        self.assertEqual(
            getattr(result_msgs[0], "_api_error", None), "media_size"
        )
        provider.chat.assert_not_called()

    def test_streaming_not_implemented_still_falls_back_to_chat(self) -> None:
        """Regression: NotImplementedError on the streaming path must
        still fall back to ``chat()``. Only ImageSizeError gets
        re-raised; unrelated stream-time errors keep their existing
        fallback behavior."""
        ok = ChatResponse(
            content="hi",
            model="test",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider = self._make_provider(NotImplementedError(), chat_response=ok)
        result_msgs, tool_use_blocks = _run(provider)
        self.assertEqual(len(result_msgs), 1)
        content = result_msgs[0].content
        if isinstance(content, list):
            text = content[0].text
        else:
            text = content
        self.assertEqual(text, "hi")
        provider.chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
