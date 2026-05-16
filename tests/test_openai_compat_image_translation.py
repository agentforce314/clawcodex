"""Tests for Anthropic→OpenAI image-block translation in the
OpenAI-compatible provider converter.

Anthropic shape: ``{"type": "image", "source": {"type": "base64",
"media_type": "image/png", "data": "..."}}``.
OpenAI shape:    ``{"type": "image_url", "image_url": {"url":
"data:image/png;base64,..."}}``.

Required because two features now ship Anthropic-shape image blocks
through the user-message and tool_result paths:
1. ``@image.png`` @-mentions in the REPL inline an image block on the
   user message.
2. The Read tool returns image content blocks in its tool_result.

Without translation, OpenAI-compatible providers (OpenAI, GLM, Minimax,
DeepSeek, OpenRouter) either reject the request outright or silently
drop the image.
"""

from __future__ import annotations

import unittest

from src.providers.openai_compatible import (
    _anthropic_image_block_to_openai,
    _convert_anthropic_messages_to_openai,
)


class TestAnthropicImageBlockToOpenAI(unittest.TestCase):
    def test_valid_block_translates_to_image_url(self) -> None:
        out = _anthropic_image_block_to_openai({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "ABCD"},
        })
        self.assertEqual(out, {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,ABCD"},
        })

    def test_jpeg_media_type_preserved(self) -> None:
        out = _anthropic_image_block_to_openai({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": "XYZ"},
        })
        self.assertEqual(out["image_url"]["url"], "data:image/jpeg;base64,XYZ")

    def test_non_image_block_returns_none(self) -> None:
        self.assertIsNone(_anthropic_image_block_to_openai({"type": "text", "text": "hi"}))

    def test_missing_source_returns_none(self) -> None:
        self.assertIsNone(_anthropic_image_block_to_openai({"type": "image"}))

    def test_url_source_returns_none(self) -> None:
        """We only translate base64 sources; URL sources aren't supported
        by the Anthropic image branch in this codebase, so reject."""
        self.assertIsNone(_anthropic_image_block_to_openai({
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/x.png"},
        }))

    def test_missing_media_type_defaults_to_png(self) -> None:
        """Defensive: an attachment missing media_type still translates
        with a safe default so the API doesn't reject ``data:;base64,``."""
        out = _anthropic_image_block_to_openai({
            "type": "image",
            "source": {"type": "base64", "data": "AAA"},
        })
        self.assertEqual(out["image_url"]["url"], "data:image/png;base64,AAA")

    def test_empty_data_returns_none(self) -> None:
        """An empty ``data`` field would produce ``data:image/png;base64,``
        which OpenAI rejects with a confusing error. The translator
        returns ``None`` so the caller keeps the original (malformed)
        block, surfacing the producer bug instead of papering over it."""
        self.assertIsNone(_anthropic_image_block_to_openai({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": ""},
        }))
        self.assertIsNone(_anthropic_image_block_to_openai({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png"},
        }))


class TestConvertUserMessageWithImage(unittest.TestCase):
    """Image blocks in user messages (e.g. from @image.png @-mentions)
    must come out as OpenAI ``image_url`` blocks."""

    def test_user_text_plus_image_translates(self) -> None:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": "ABCD"}},
            ],
        }]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")
        self.assertEqual([b["type"] for b in out[0]["content"]], ["text", "image_url"])
        self.assertEqual(
            out[0]["content"][1]["image_url"]["url"],
            "data:image/png;base64,ABCD",
        )

    def test_multi_image_user_message_translates_all_images(self) -> None:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "compare"},
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": "BBB"}},
            ],
        }]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual([b["type"] for b in out[0]["content"]], ["text", "image_url", "image_url"])
        self.assertIn("png;base64,AAA", out[0]["content"][1]["image_url"]["url"])
        self.assertIn("jpeg;base64,BBB", out[0]["content"][2]["image_url"]["url"])

    def test_user_message_text_only_unchanged(self) -> None:
        """A plain-text user message must pass through unchanged so we
        don't regress the existing string-content path."""
        messages = [{"role": "user", "content": "plain text"}]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual(out, [{"role": "user", "content": "plain text"}])


class TestConvertToolResultWithImage(unittest.TestCase):
    """Image blocks inside tool_result content (from the Read tool) get
    extracted into a synthetic user message with ``image_url`` since
    OpenAI's ``role=tool`` message only accepts string content."""

    def test_tool_result_with_image_splits_into_tool_then_user_image(self) -> None:
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": "XYZ"}},
                ]},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        # 1) assistant w/ tool_calls
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[0]["tool_calls"][0]["id"], "tu_1")
        # 2) role=tool with placeholder text (image-only originals get a
        #    pointer so the tool_call has SOMETHING to pair with).
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(out[1]["tool_call_id"], "tu_1")
        self.assertTrue(out[1]["content"], "tool message content must be non-empty")
        # 3) synthetic user message carrying the image_url
        self.assertEqual(out[2]["role"], "user")
        self.assertEqual(out[2]["content"][0]["type"], "image_url")
        self.assertIn("png;base64,XYZ", out[2]["content"][0]["image_url"]["url"])

    def test_tool_result_with_text_and_image_splits_text_to_tool_image_to_user(self) -> None:
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_2", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_2", "content": [
                    {"type": "text", "text": "Here is the image"},
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/jpeg", "data": "JJJ"}},
                ]},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        tool_msgs = [m for m in out if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("Here is the image", tool_msgs[0]["content"])
        # The image becomes its own following user message.
        idx = out.index(tool_msgs[0])
        self.assertEqual(out[idx + 1]["role"], "user")
        self.assertEqual(out[idx + 1]["content"][0]["type"], "image_url")

    def test_tool_result_text_only_unchanged_no_extra_user_message(self) -> None:
        """A text-only tool_result must NOT spawn an extra synthetic user
        message — regression guard for the split."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_t", "name": "Bash", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_t", "content": "hello world"},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        # assistant + tool message exactly (no extra user message)
        roles = [m["role"] for m in out]
        self.assertEqual(roles, ["assistant", "tool"])


if __name__ == "__main__":
    unittest.main()
