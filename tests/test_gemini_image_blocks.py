"""Gemini's image-block conversion.

The converter had branches for text / tool_use / tool_result and **no image
branch and no else**, so an image block was silently skipped: the request went
out text-only, and because a pasted image also carries an ``[Image: source: …]``
metadata line, the model received a DESCRIPTION of an image it never saw and
confabulated an answer. A wrong answer delivered confidently is a worse failure
than an error.

``google-genai`` is not installed in this environment, so these tests stub
``genai_types``. That is enough to pin the dispatch — which constructor is
called, with which keywords, and how each failure is classified — because
``_gemini_image_part`` is pure. It does NOT prove the SDK accepts the Part; that
needs a machine with the SDK.
"""

from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace
from unittest import mock

from src.providers import gemini_provider as gp

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")


def _block(data: str = PNG, media_type: str = "image/png", **overrides) -> dict:
    source = {"type": "base64", "media_type": media_type, "data": data}
    source.update(overrides.pop("source", {}))
    return {"type": "image", "source": source, **overrides}


class _ModernPart:
    """Stub exposing only the modern Part.from_bytes constructor."""

    calls: list = []

    @staticmethod
    def from_bytes(*, data, mime_type):
        _ModernPart.calls.append({"data": data, "mime_type": mime_type})
        return SimpleNamespace(kind="from_bytes", data=data, mime_type=mime_type)


class TestModernConstructor(unittest.TestCase):
    def setUp(self) -> None:
        _ModernPart.calls = []
        self.types = SimpleNamespace(Part=_ModernPart)  # no Blob attribute

    def test_uses_from_bytes_with_documented_keywords(self) -> None:
        with mock.patch.object(gp, "genai_types", self.types):
            part = gp._gemini_image_part(_block())
        self.assertEqual(part.kind, "from_bytes")
        self.assertEqual(_ModernPart.calls[0]["mime_type"], "image/png")
        self.assertEqual(_ModernPart.calls[0]["data"], base64.b64decode(PNG))

    def test_media_type_is_forwarded(self) -> None:
        with mock.patch.object(gp, "genai_types", self.types):
            gp._gemini_image_part(_block(media_type="image/jpeg"))
        self.assertEqual(_ModernPart.calls[0]["mime_type"], "image/jpeg")

    def test_missing_media_type_defaults_to_png(self) -> None:
        block = {"type": "image", "source": {"type": "base64", "data": PNG}}
        with mock.patch.object(gp, "genai_types", self.types):
            gp._gemini_image_part(block)
        self.assertEqual(_ModernPart.calls[0]["mime_type"], "image/png")


class TestLegacyConstructor(unittest.TestCase):
    def test_falls_back_to_inline_data_blob(self) -> None:
        """An SDK pin must not silently disable image input."""
        made: list = []

        class _Blob:
            def __init__(self, *, data, mime_type):
                made.append({"data": data, "mime_type": mime_type})
                self.data, self.mime_type = data, mime_type

        class _Part:
            def __init__(self, *, inline_data):
                self.inline_data = inline_data

        types = SimpleNamespace(Part=_Part, Blob=_Blob)  # no from_bytes
        with mock.patch.object(gp, "genai_types", types):
            part = gp._gemini_image_part(_block())
        self.assertIsInstance(part.inline_data, _Blob)
        self.assertEqual(made[0]["mime_type"], "image/png")


class TestCapabilityGap(unittest.TestCase):
    def test_raises_when_neither_constructor_exists(self) -> None:
        types = SimpleNamespace(Part=SimpleNamespace())  # no from_bytes, no Blob
        with mock.patch.object(gp, "genai_types", types):
            with self.assertRaises(gp._GeminiImageUnsupported) as ctx:
                gp._gemini_image_part(_block())
        self.assertIn("Switch to an Anthropic", str(ctx.exception))


class TestMalformedBlocks(unittest.TestCase):
    """A corrupt payload is NOT a capability gap.

    Both used to return None and produce the same "switch models" message, which
    is useless advice for someone holding a bad base64 string.
    """

    def setUp(self) -> None:
        self.types = SimpleNamespace(Part=_ModernPart)

    def _declines(self, block: dict) -> None:
        with mock.patch.object(gp, "genai_types", self.types):
            self.assertIsNone(gp._gemini_image_part(block))

    def test_url_source_declined(self) -> None:
        self._declines({"type": "image", "source": {"type": "url", "url": "http://x/y.png"}})

    def test_missing_source_declined(self) -> None:
        self._declines({"type": "image"})

    def test_empty_data_declined(self) -> None:
        self._declines(_block(data=""))

    def test_non_string_data_declined(self) -> None:
        self._declines({"type": "image", "source": {"type": "base64", "data": 123}})

    def test_undecodable_base64_declined(self) -> None:
        self._declines(_block(data="!!!not base64!!!"))

    def test_whitespace_only_base64_declined(self) -> None:
        self._declines(_block(data="   "))


class TestConverterIntegration(unittest.TestCase):
    """The image branch reaches parts, and a malformed block does not kill the turn."""

    def _convert(self, content):
        types = SimpleNamespace(
            Part=_ModernPart,
            Content=lambda **kw: SimpleNamespace(**kw),
        )
        types.Part.from_text = staticmethod(
            lambda *, text: SimpleNamespace(kind="text", text=text)
        )
        provider = gp.GeminiProvider.__new__(gp.GeminiProvider)
        # _convert_messages gates on _ensure_sdk(), which needs a non-None
        # `genai`. Stub it: the conversion itself only touches genai_types.
        with mock.patch.object(gp, "genai_types", types), \
                mock.patch.object(gp, "genai", SimpleNamespace()):
            return provider._convert_messages([{"role": "user", "content": content}])

    def test_image_block_reaches_parts(self) -> None:
        _ModernPart.calls = []
        contents, _system = self._convert(
            [{"type": "text", "text": "what is this?"}, _block()]
        )
        kinds = [getattr(p, "kind", None) for p in contents[0].parts]
        self.assertIn("from_bytes", kinds, f"image block was dropped: {kinds}")
        self.assertIn("text", kinds)

    def test_malformed_image_block_is_skipped_not_fatal(self) -> None:
        _ModernPart.calls = []
        contents, _system = self._convert(
            [{"type": "text", "text": "hi"}, _block(data="!!bad!!")]
        )
        kinds = [getattr(p, "kind", None) for p in contents[0].parts]
        self.assertEqual(kinds, ["text"])


if __name__ == "__main__":
    unittest.main()
