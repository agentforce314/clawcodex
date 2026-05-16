"""Tests that ``BaseProvider._prepare_messages`` runs the same
``validate_images_for_api`` guard that the Anthropic-direct ``call_model``
path runs, so every provider — anthropic, openai-compatible, glm,
minimax, deepseek, openrouter — rejects oversized base64 images
client-side instead of letting the API return an opaque error.

Pre-fix gap: the validator only ran on ``services/api/claude.py:call_model``,
which is the parity-streaming path. Production paths go through
``provider.chat_stream_response`` and skipped the check entirely.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.providers.base import BaseProvider, ChatMessage
from src.providers.openai_compatible import OpenAICompatibleProvider
from src.utils.image_processor import API_IMAGE_MAX_BASE64_SIZE
from src.utils.image_validation import ImageSizeError


class _StubProvider(BaseProvider):
    """Concrete subclass that exposes ``_prepare_messages`` for direct test
    invocation without standing up a real chat client."""

    def chat(self, messages, tools=None, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    def chat_stream(self, messages, tools=None, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    def get_available_models(self):  # pragma: no cover - unused
        return []


class _StubOpenAICompat(OpenAICompatibleProvider):
    """Concrete OpenAI-compatible subclass for exercising the override path."""

    def _create_client(self):  # pragma: no cover - unused
        raise NotImplementedError

    def get_available_models(self):  # pragma: no cover - unused
        return []


def _img_block(b64_len: int) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "A" * b64_len},
    }


def _msg(content) -> dict[str, Any]:
    return {"role": "user", "content": content}


class TestBaseProviderImageValidation(unittest.TestCase):
    def test_oversize_image_raises_imagesizeerror(self) -> None:
        provider = _StubProvider(api_key="test")
        with self.assertRaises(ImageSizeError):
            provider._prepare_messages([_msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 1)])])

    def test_image_at_limit_passes(self) -> None:
        provider = _StubProvider(api_key="test")
        out = provider._prepare_messages([_msg([_img_block(API_IMAGE_MAX_BASE64_SIZE)])])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")

    def test_no_images_passes(self) -> None:
        provider = _StubProvider(api_key="test")
        out = provider._prepare_messages([_msg([{"type": "text", "text": "hi"}])])
        self.assertEqual(out, [_msg([{"type": "text", "text": "hi"}])])

    def test_text_only_string_content_passes(self) -> None:
        """Regression: providers without image support should be unaffected."""
        provider = _StubProvider(api_key="test")
        out = provider._prepare_messages([_msg("just a string")])
        self.assertEqual(out, [_msg("just a string")])

    def test_chat_message_input_converted_and_validated(self) -> None:
        """ChatMessage dataclass input still flows through to_dict() and is
        validated. Defensive — most callers already pass dicts."""
        provider = _StubProvider(api_key="test")
        out = provider._prepare_messages([ChatMessage(role="user", content="hi")])
        self.assertEqual(out, [{"role": "user", "content": "hi"}])

    def test_oversize_aggregated_with_other_messages(self) -> None:
        provider = _StubProvider(api_key="test")
        with self.assertRaises(ImageSizeError) as cm:
            provider._prepare_messages([
                _msg([{"type": "text", "text": "intro"}]),
                _msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 100)]),
                _msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 200)]),
            ])
        self.assertEqual(len(cm.exception.oversized), 2)


class TestOpenAICompatPrepareValidatesBeforeTranslation(unittest.TestCase):
    """The OpenAI-compatible subclass MUST validate the Anthropic-shape
    payload (via ``super()._prepare_messages``) BEFORE translation. After
    translation the block carries a ``data:image/png;base64,...`` URL and
    the base64 walker can no longer find it."""

    def test_oversize_image_raises_before_translation(self) -> None:
        provider = _StubOpenAICompat(api_key="test")
        with self.assertRaises(ImageSizeError):
            provider._prepare_messages([_msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 1)])])

    def test_in_limit_image_translates_to_image_url(self) -> None:
        provider = _StubOpenAICompat(api_key="test")
        out = provider._prepare_messages([_msg([_img_block(1000)])])
        # Single user message; image block translated to OpenAI ``image_url`` shape.
        self.assertEqual(len(out), 1)
        blocks = out[0]["content"]
        self.assertEqual(blocks[0]["type"], "image_url")
        self.assertTrue(blocks[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_tool_result_with_oversize_image_raises(self) -> None:
        """Images embedded inside tool_result content (Read tool image path)
        must also be validated before the Anthropic→OpenAI split."""
        provider = _StubOpenAICompat(api_key="test")
        tool_result_with_image = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": [
                        {"type": "text", "text": "image content:"},
                        _img_block(API_IMAGE_MAX_BASE64_SIZE + 1),
                    ],
                }
            ],
        }
        with self.assertRaises(ImageSizeError):
            provider._prepare_messages([tool_result_with_image])


if __name__ == "__main__":
    unittest.main()
