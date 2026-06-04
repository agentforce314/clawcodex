"""Pin that ``_call_provider_for_turn`` re-raises ``ImageSizeError``
instead of swallowing it via the generic ``except Exception`` fallback.

If the streaming path's ``_prepare_messages`` raises ``ImageSizeError``,
falling back to ``provider.chat()`` is pointless: ``chat()`` calls the
same ``_prepare_messages`` and will raise again. Propagating the
streaming-path exception lets the outer query loop translate it into a
clean media_size error message immediately.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.tool_system.agent_loop import _call_provider_for_turn
from src.utils.image_validation import ImageSizeError


class TestImageSizeErrorPropagation(unittest.TestCase):
    def _make_provider(self, stream_exc: Exception, chat_response: Any = None) -> MagicMock:
        provider = MagicMock()
        provider.chat_stream_response.side_effect = stream_exc
        if chat_response is None:
            # Should not be reached in the propagation test; trip if so.
            provider.chat.side_effect = AssertionError(
                "chat() should not be invoked after streaming ImageSizeError"
            )
        else:
            provider.chat.return_value = chat_response
        return provider

    def test_streaming_imagesize_propagates_without_chat_fallback(self) -> None:
        oversize = ImageSizeError([(6 * 1024 * 1024, 5 * 1024 * 1024)])
        provider = self._make_provider(oversize)
        with self.assertRaises(ImageSizeError):
            _call_provider_for_turn(
                provider=provider,
                api_messages=[{"role": "user", "content": "x"}],
                call_kwargs={},
                stream=True,
                on_text_chunk=None,
            )
        # chat() must NOT have been called because we shouldn't double-attempt
        provider.chat.assert_not_called()

    def test_streaming_not_implemented_still_falls_back_to_chat(self) -> None:
        """Regression: only ImageSizeError is the new re-raise. Existing
        fallback behavior for NotImplementedError must stay intact."""
        ok = ChatResponse(
            content="hi",
            model="test",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider = self._make_provider(NotImplementedError(), chat_response=ok)
        response, streamed = _call_provider_for_turn(
            provider=provider,
            api_messages=[{"role": "user", "content": "x"}],
            call_kwargs={},
            stream=True,
            on_text_chunk=None,
        )
        self.assertEqual(response.content, "hi")
        self.assertFalse(streamed)
        provider.chat.assert_called_once()

    def test_streaming_generic_exception_still_falls_back_to_chat(self) -> None:
        """Regression: pre-existing ``except Exception: pass`` fallback must
        keep working for unrelated stream-time errors so user behavior is
        unchanged for transient SDK glitches."""
        ok = ChatResponse(
            content="hi",
            model="test",
            usage={"input_tokens": 1, "output_tokens": 1},
            finish_reason="end_turn",
            tool_uses=None,
        )
        provider = self._make_provider(RuntimeError("stream blew up"), chat_response=ok)
        response, streamed = _call_provider_for_turn(
            provider=provider,
            api_messages=[{"role": "user", "content": "x"}],
            call_kwargs={},
            stream=True,
            on_text_chunk=None,
        )
        self.assertEqual(response.content, "hi")
        self.assertFalse(streamed)
        provider.chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
