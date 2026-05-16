"""Regression tests for Bug A: ``@image.png`` @-mentions.

Before the fix, ``expand_at_mentions`` opened image files with
``open(path, "r", encoding="utf-8", errors="replace")`` and shipped the
resulting mojibake to the model inside a
``<system-reminder>Contents of foo.png:...```...</system-reminder>`` block.
The model would latch onto ASCII fragments (e.g. PNG XMP metadata strings)
and hallucinate. The fix routes image files through the same image
pipeline the Read tool uses (magic-byte sniff + resize) and returns a
``kind="image"`` attachment carrying base64 + media_type, which the REPL
inlines as an actual image content block on the user message.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.command_system.input_processing import (
    build_image_content_blocks,
    expand_at_mentions,
    format_at_mention_attachments,
)
from src.types.content_blocks import ImageBlock, TextBlock
from src.types.messages import UserMessage, normalize_messages_for_api


def _write_png(path: Path, *, size: tuple[int, int] = (64, 64), color: str = "red") -> None:
    Image.new("RGB", size, color=color).save(path, format="PNG")


def _write_jpeg(path: Path, *, size: tuple[int, int] = (64, 64), color: str = "blue") -> None:
    Image.new("RGB", size, color=color).save(path, format="JPEG", quality=80)


class TestExpandAtMentionsImages(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_png_at_mention_returns_image_attachment(self) -> None:
        """``@foo.png`` produces a ``kind="image"`` attachment with
        base64 + media_type, NOT a text ``kind="file"`` with mojibake."""
        png = self.tmp / "screenshot.png"
        _write_png(png)
        _, atts = expand_at_mentions(f"see @{png}", cwd=str(self.tmp))
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "image")
        self.assertEqual(atts[0]["media_type"], "image/png")
        self.assertGreater(len(atts[0]["base64"]), 0)
        # The old bug shipped raw bytes under "content" -- the image
        # attachment must NOT carry that field, so the text formatter
        # cannot accidentally fall back to wrapping it.
        self.assertNotIn("content", atts[0])

    def test_jpeg_at_mention_returns_image_attachment(self) -> None:
        jpg = self.tmp / "photo.jpg"
        _write_jpeg(jpg)
        _, atts = expand_at_mentions(f"see @{jpg}", cwd=str(self.tmp))
        self.assertEqual(atts[0]["kind"], "image")
        self.assertEqual(atts[0]["media_type"], "image/jpeg")

    def test_misnamed_png_with_jpeg_bytes_detects_as_jpeg(self) -> None:
        """Magic-byte detection wins over extension: a ``.png`` file
        containing JPEG bytes returns ``media_type=image/jpeg`` so the
        Anthropic API doesn't reject the wrong-typed image. Mirrors the
        Read tool's behaviour at ``src/tool_system/tools/read.py``."""
        mis = self.tmp / "wrongext.png"
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), color="green").save(buf, format="JPEG", quality=80)
        mis.write_bytes(buf.getvalue())
        _, atts = expand_at_mentions(f"see @{mis}", cwd=str(self.tmp))
        self.assertEqual(atts[0]["kind"], "image")
        self.assertEqual(
            atts[0]["media_type"], "image/jpeg",
            "magic-byte sniffing should override the .png extension",
        )

    def test_empty_image_dropped_silently(self) -> None:
        """Empty image files produce no attachment (rather than shipping
        empty base64 or text-mode garbage)."""
        empty = self.tmp / "empty.png"
        empty.write_bytes(b"")
        _, atts = expand_at_mentions(f"see @{empty}", cwd=str(self.tmp))
        self.assertEqual(atts, [])

    def test_undecodable_png_dropped_silently(self) -> None:
        """Files with an image extension but garbage bytes are dropped
        rather than shipped — Pillow refuses to decode, and we'd rather
        skip than send bytes the API will reject."""
        fake = self.tmp / "fake.png"
        fake.write_bytes(b"not an image at all, just bytes")
        _, atts = expand_at_mentions(f"see @{fake}", cwd=str(self.tmp))
        self.assertEqual(atts, [])

    def test_text_at_mention_still_works(self) -> None:
        """Non-image @-mentions remain unchanged: ``kind="file"`` with
        the file's contents as text."""
        md = self.tmp / "doc.md"
        md.write_text("# title\nbody\n")
        _, atts = expand_at_mentions(f"see @{md}", cwd=str(self.tmp))
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertIn("title", atts[0]["content"])

    def test_directory_at_mention_still_works(self) -> None:
        """Directory @-mentions remain unchanged: ``kind="directory"``."""
        sub = self.tmp / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("a")
        _, atts = expand_at_mentions(f"see @{sub}", cwd=str(self.tmp))
        self.assertEqual(atts[0]["kind"], "directory")
        self.assertIn("a.txt", atts[0]["content"])


class TestFormatAtMentionAttachmentsImages(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_image_only_produces_no_text_wrapper(self) -> None:
        """An image-only attachment list renders to an empty string.

        Critical: NO ``<system-reminder>Contents of ...``` text — the old
        bug wrapped binary bytes there and the model hallucinated."""
        png = self.tmp / "x.png"
        _write_png(png)
        _, atts = expand_at_mentions(f"@{png}", cwd=str(self.tmp))
        rendered = format_at_mention_attachments(atts)
        self.assertEqual(rendered, "")

    def test_mixed_image_and_text_renders_only_text(self) -> None:
        """When the user mixes an image and a text file mention, the
        text wrapper covers the text file only; the image is excluded
        from the system-reminder."""
        png = self.tmp / "x.png"
        _write_png(png)
        md = self.tmp / "y.md"
        md.write_text("# y")
        _, atts = expand_at_mentions(f"@{png} @{md}", cwd=str(self.tmp))
        rendered = format_at_mention_attachments(atts)
        self.assertIn("Contents of y.md", rendered)
        self.assertNotIn("Contents of x.png", rendered)


class TestBuildImageContentBlocks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_image_attachments_become_image_blocks(self) -> None:
        png = self.tmp / "x.png"
        _write_png(png)
        _, atts = expand_at_mentions(f"@{png}", cwd=str(self.tmp))
        blocks = build_image_content_blocks(atts)
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], ImageBlock)
        self.assertEqual(blocks[0].source["type"], "base64")
        self.assertEqual(blocks[0].source["media_type"], "image/png")
        self.assertGreater(len(blocks[0].source["data"]), 0)

    def test_non_image_attachments_are_ignored(self) -> None:
        md = self.tmp / "x.md"
        md.write_text("# hi")
        _, atts = expand_at_mentions(f"@{md}", cwd=str(self.tmp))
        blocks = build_image_content_blocks(atts)
        self.assertEqual(blocks, [])

    def test_empty_attachments_returns_empty_list(self) -> None:
        self.assertEqual(build_image_content_blocks([]), [])

    def test_attachment_missing_base64_or_media_type_skipped(self) -> None:
        """Defensive: a buggy producer dropping ``base64`` or ``media_type``
        should not crash; the malformed attachment is just skipped."""
        blocks = build_image_content_blocks(
            [{"kind": "image", "base64": "abc"}],
        )
        self.assertEqual(blocks, [])
        blocks = build_image_content_blocks(
            [{"kind": "image", "media_type": "image/png"}],
        )
        self.assertEqual(blocks, [])


class TestImageAttachmentCompressionFallback(unittest.TestCase):
    """Regression coverage for the BLOCKER fix added in
    ``_try_build_image_attachment``: when ``maybe_resize_image`` returns a
    still-oversize buffer, we run ``compress_image_to_token_budget`` from
    the ORIGINAL buffer to force the payload under the 5 MB base64 API
    cap; if BOTH attempts are oversize, we drop the attachment.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_oversize_image_compressed_under_api_limit(self) -> None:
        """A real oversize image (random-noise PNG that doesn't compress
        well even after resize) lands under
        ``API_IMAGE_MAX_BASE64_SIZE`` thanks to the token-budget
        compression fallback. Pre-fix, the same input lands a >5 MB
        base64 payload that the Anthropic provider rejects at the next
        turn via ``validate_images_for_api``."""
        import os

        from src.utils.image_processor import API_IMAGE_MAX_BASE64_SIZE
        from src.command_system.input_processing import _try_build_image_attachment

        # 4000x4000 of random-ish noise to defeat PNG palette compression
        # and force the resize+JPEG fallback in maybe_resize_image to
        # still exceed the 3.75 MB raw / 5 MB base64 envelope. Pillow
        # encodes this >10 MB.
        rng = os.urandom(4000 * 4000 * 3)
        oversized = self.tmp / "oversized.png"
        Image.frombytes("RGB", (4000, 4000), rng).save(oversized, format="PNG")
        self.assertGreater(
            oversized.stat().st_size, API_IMAGE_MAX_BASE64_SIZE,
            "test setup: random-noise PNG must exceed the API cap for the "
            "compression branch to engage",
        )
        att = _try_build_image_attachment(str(oversized), "oversized.png")
        self.assertIsNotNone(att)
        b64_len = len(att["base64"])
        self.assertLessEqual(
            b64_len, API_IMAGE_MAX_BASE64_SIZE,
            f"Compression fallback failed to bring base64 under the API "
            f"limit: {b64_len} > {API_IMAGE_MAX_BASE64_SIZE}",
        )

    def test_doubly_oversize_image_dropped_silently(self) -> None:
        """If both ``maybe_resize_image`` and
        ``compress_image_to_token_budget`` return oversize buffers,
        ``_try_build_image_attachment`` returns ``None`` rather than
        shipping a payload the API would reject. Monkeypatched here so
        we don't need a pathological input that defeats both steps."""
        from src.command_system import input_processing as ip_mod
        from src.utils import image_processor as ip_proc

        png = self.tmp / "tiny.png"
        _write_png(png)

        real_resize = ip_proc.maybe_resize_image
        real_compress = ip_proc.compress_image_to_token_budget

        # Force the resize step to return a "still oversize" payload
        # (synthetic 6 MB ASCII) so the compress branch engages.
        def fake_resize(buf, original_size, format_hint=None):
            return ip_proc.ResizeResult(
                data=b"x" * (6 * 1024 * 1024),
                media_type="image/png",
                dimensions=None,
            )

        # Then force the compress step to ALSO return oversize, so the
        # drop branch engages.
        def fake_compress(buf, max_tokens, media_type=None):
            return ip_proc.ResizeResult(
                data=b"x" * (6 * 1024 * 1024),
                media_type="image/png",
                dimensions=None,
            )

        ip_proc.maybe_resize_image = fake_resize
        ip_proc.compress_image_to_token_budget = fake_compress
        try:
            att = ip_mod._try_build_image_attachment(str(png), "tiny.png")
            self.assertIsNone(att,
                "Both resize and compress oversize -> attachment must be "
                "dropped to avoid the API rejecting the next turn")
        finally:
            ip_proc.maybe_resize_image = real_resize
            ip_proc.compress_image_to_token_budget = real_compress


class TestEndToEndAtImageMention(unittest.TestCase):
    """Bug A end-to-end: ``@png`` → mixed-content user message → API."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _compose(self, user_text: str) -> dict:
        _, atts = expand_at_mentions(user_text, cwd=str(self.tmp))
        attachment_text = format_at_mention_attachments(atts)
        text_part = (
            f"{attachment_text}\n\n{user_text}" if attachment_text else user_text
        )
        image_blocks = build_image_content_blocks(atts)
        um = UserMessage(content=[TextBlock(text=text_part), *image_blocks])
        return normalize_messages_for_api([um])[0]

    def test_at_image_mention_yields_text_plus_image_api_payload(self) -> None:
        png = self.tmp / "screenshot.png"
        _write_png(png, size=(160, 100), color=(120, 60, 200))
        msg = self._compose(f"what is this @{png}")
        self.assertEqual(msg["role"], "user")
        self.assertEqual([b["type"] for b in msg["content"]], ["text", "image"])
        self.assertNotIn("Contents of screenshot.png", msg["content"][0]["text"])
        src = msg["content"][1]["source"]
        self.assertEqual(src["type"], "base64")
        self.assertEqual(src["media_type"], "image/png")
        self.assertGreater(len(src["data"]), 0)

    def test_multi_image_at_mention_yields_text_plus_multiple_image_blocks(self) -> None:
        a = self.tmp / "a.png"
        b = self.tmp / "b.png"
        _write_png(a, size=(60, 40), color="red")
        _write_png(b, size=(60, 40), color="blue")
        msg = self._compose(f"compare these @{a} @{b}")
        # Exactly one text + two image blocks, in order.
        self.assertEqual([t["type"] for t in msg["content"]], ["text", "image", "image"])
        # Each image carries distinct bytes (sanity: not the same file
        # served twice).
        d1 = msg["content"][1]["source"]["data"]
        d2 = msg["content"][2]["source"]["data"]
        self.assertNotEqual(d1, d2)

    def test_mixed_text_and_image_at_mentions_compose_correctly(self) -> None:
        png = self.tmp / "screenshot.png"
        _write_png(png, size=(60, 40))
        md = self.tmp / "notes.md"
        md.write_text("# investigate\nFollow up here.\n")
        msg = self._compose(f"see @{md} and @{png}")
        # One text block carries the markdown contents (via the text
        # @-mention's system-reminder wrap) and the user's own prompt;
        # the image lands as a separate image block. NO mojibake from
        # the .png.
        self.assertEqual([b["type"] for b in msg["content"]], ["text", "image"])
        text = msg["content"][0]["text"]
        self.assertIn("Contents of notes.md", text)
        self.assertIn("Follow up here", text)
        self.assertNotIn("Contents of screenshot.png", text)


if __name__ == "__main__":
    unittest.main()
