"""Tests for Anthropic→OpenAI document-block translation in the
OpenAI-compatible provider converter.

Anthropic shape: ``{"type": "document", "source": {"type": "base64",
"media_type": "application/pdf", "data": "..."}}``.
OpenAI shape:    ``{"type": "file", "file": {"filename": "document.pdf",
"file_data": "data:application/pdf;base64,..."}}``.

No production path currently produces ``DocumentBlock`` for an
OpenAI-compatible provider — PDFs flow through Read tool's
``_read_map_result_to_api`` as text. This translator is a defensive
addition so that if a future @-mention path or third-party tool
returns a PDF block, it lands in the OpenAI shape instead of silently
passing through as an Anthropic-shape block the provider can't parse.
"""

from __future__ import annotations

import unittest

from src.providers.openai_compatible import (
    _anthropic_document_block_to_openai,
    _convert_anthropic_messages_to_openai,
)


class TestAnthropicDocumentBlockToOpenAI(unittest.TestCase):
    def test_valid_block_translates_to_file(self) -> None:
        out = _anthropic_document_block_to_openai({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": "ABCD"},
        })
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "file")
        self.assertEqual(out["file"]["filename"], "document.pdf")
        self.assertEqual(out["file"]["file_data"], "data:application/pdf;base64,ABCD")

    def test_non_document_block_returns_none(self) -> None:
        # An image block must NOT be claimed by the document translator,
        # otherwise the multimodal dispatcher would emit the wrong shape.
        self.assertIsNone(_anthropic_document_block_to_openai({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "XYZ"},
        }))
        self.assertIsNone(_anthropic_document_block_to_openai({
            "type": "text", "text": "hi"}))

    def test_missing_source_returns_none(self) -> None:
        self.assertIsNone(_anthropic_document_block_to_openai({"type": "document"}))

    def test_url_source_returns_none(self) -> None:
        """Anthropic supports url-based document blocks too; we only
        translate base64 form here (data URI requires raw bytes)."""
        self.assertIsNone(_anthropic_document_block_to_openai({
            "type": "document",
            "source": {"type": "url", "url": "https://example.com/doc.pdf"},
        }))

    def test_missing_media_type_defaults_to_pdf(self) -> None:
        out = _anthropic_document_block_to_openai({
            "type": "document",
            "source": {"type": "base64", "data": "ABCD"},
        })
        self.assertIsNotNone(out)
        self.assertEqual(out["file"]["file_data"], "data:application/pdf;base64,ABCD")

    def test_empty_data_returns_none(self) -> None:
        """Producer-bug guard mirrors the image translator: empty
        ``data`` would produce ``data:application/pdf;base64,`` which
        the server rejects with a confusing error. Returning None lets
        the upstream serializer surface the malformed shape instead."""
        self.assertIsNone(_anthropic_document_block_to_openai({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": ""},
        }))
        self.assertIsNone(_anthropic_document_block_to_openai({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf"},
        }))

    def test_non_dict_source_returns_none(self) -> None:
        self.assertIsNone(_anthropic_document_block_to_openai({
            "type": "document",
            "source": "not-a-dict",
        }))


class TestConverterDocumentIntegration(unittest.TestCase):
    """End-to-end coverage through ``_convert_anthropic_messages_to_openai``."""

    def test_user_message_with_document_translates(self) -> None:
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Summarise this PDF"},
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": "PDFB64"}},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(out), 1)
        blocks = out[0]["content"]
        # Text passes through; document becomes a "file" block.
        self.assertEqual(blocks[0]["type"], "text")
        self.assertEqual(blocks[1]["type"], "file")
        self.assertIn("data:application/pdf;base64,PDFB64", blocks[1]["file"]["file_data"])

    def test_mixed_image_and_document_translate_to_distinct_shapes(self) -> None:
        """Image must become ``image_url``; document must become ``file``.
        Pins that the multimodal dispatcher doesn't accidentally route
        both through the same translator."""
        messages = [
            {"role": "user", "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": "IMG"}},
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": "DOC"}},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        blocks = out[0]["content"]
        self.assertEqual(blocks[0]["type"], "image_url")
        self.assertEqual(blocks[1]["type"], "file")

    def test_tool_result_with_document_splits_into_tool_then_user_file(self) -> None:
        """The image-tool-result split path must extend to documents:
        ``role=tool`` (text body / placeholder) + synthetic ``role=user``
        carrying the ``file`` content block."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_doc", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_doc", "content": [
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf", "data": "PDF"}},
                ]},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(out[1]["tool_call_id"], "tu_doc")
        # tool message must have non-empty content (OpenAI requires it)
        # AND must carry the tool_use_id correlation marker.
        self.assertTrue(out[1]["content"])
        self.assertIn("tu_doc", out[1]["content"])
        # Synthetic user message follows: correlation text block first,
        # then the translated file block.
        self.assertEqual(out[2]["role"], "user")
        self.assertEqual(out[2]["content"][0]["type"], "text")
        self.assertIn("tu_doc", out[2]["content"][0]["text"])
        self.assertEqual(out[2]["content"][1]["type"], "file")

    def test_tool_result_with_text_and_document_splits(self) -> None:
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_2", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_2", "content": [
                    {"type": "text", "text": "Here is the PDF"},
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf", "data": "PDF2"}},
                ]},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        tool_msgs = [m for m in out if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("Here is the PDF", tool_msgs[0]["content"])
        # Tool message must ALSO carry the tool_use_id correlation marker
        # in the text+document case (symmetry with text+image).
        self.assertIn("tu_2", tool_msgs[0]["content"])
        idx = out.index(tool_msgs[0])
        self.assertEqual(out[idx + 1]["role"], "user")
        # Correlation text block first, then translated file block.
        self.assertEqual(out[idx + 1]["content"][0]["type"], "text")
        self.assertIn("tu_2", out[idx + 1]["content"][0]["text"])
        self.assertEqual(out[idx + 1]["content"][1]["type"], "file")

    def test_tool_result_image_plus_document_both_carried_in_user_message(self) -> None:
        """A tool_result with BOTH an image and a document should produce
        a single synthetic user message containing BOTH translated
        blocks, in order."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_mix", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_mix", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": "I"}},
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf", "data": "D"}},
                ]},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        user_msgs = [m for m in out if m.get("role") == "user"]
        # The original user-with-tool_result is the input; only the
        # synthetic user message survives in the output (the original
        # was consumed by the tool_result branch).
        self.assertEqual(len(user_msgs), 1)
        blocks = user_msgs[0]["content"]
        types = [b.get("type") for b in blocks]
        self.assertIn("image_url", types)
        self.assertIn("file", types)


if __name__ == "__main__":
    unittest.main()
