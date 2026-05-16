"""Tests that ``@file.pdf`` and other binary @-mentions are routed to a
"binary" attachment instead of being opened in text mode (the Bug A
pattern that produced mojibake the model hallucinated from).

PDF in TS is surfaced through the Read tool's ``pages`` parameter, not
auto-inlined via @-mention. Python now matches: we emit a small system
reminder pointing the model at Read, rather than dumping replacement
chars into the prompt.

Coverage:
- Known binary extensions (.pdf, .zip, .docx, ...) skip the text read.
- PDF reminder mentions the Read tool's ``pages`` parameter specifically.
- Files with NUL bytes are caught by the content sniffer even when their
  extension isn't enumerated (defense in depth).
- Normal text files still flow through the unchanged ``kind="file"`` path.
- ``format_at_mention_attachments`` renders the new "binary" kind without
  leaking content bytes.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.command_system.input_processing import (
    expand_at_mentions,
    format_at_mention_attachments,
)


def _render(atts: list[dict]) -> bytes:
    """Helper: render attachments and return the bytes that would be
    embedded in the prompt. Tests assert on the bytes (not the str) so
    NUL chars and utf-8 replacement chars are detectable.
    """
    return format_at_mention_attachments(atts).encode("utf-8", errors="surrogatepass")


class TestPdfAtMention(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.cwd, name)
        Path(path).write_bytes(data)
        return path

    def test_pdf_extension_routes_to_binary_attachment(self) -> None:
        # Synthetic PDF magic header + opaque bytes — never opened as text.
        self._write("doc.pdf", b"%PDF-1.7\n\x00\x01\x02\x03 lorem")
        _, atts = expand_at_mentions("look at @doc.pdf", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        att = atts[0]
        self.assertEqual(att["kind"], "binary")
        self.assertEqual(att["ext"], "pdf")
        self.assertEqual(att["display_path"], "doc.pdf")
        # PDF hint must mention Read tool + pages parameter so the model
        # has a concrete next step.
        self.assertIn("Read tool", att["hint"])
        self.assertIn("pages", att["hint"])

    def test_pdf_attachment_does_not_leak_bytes(self) -> None:
        """Critical: the binary attachment must NOT carry the file content.
        Bug A's failure mode was shipping mojibake into the prompt; the
        whole point of the binary branch is to avoid that."""
        self._write("doc.pdf", b"%PDF-1.7\n\x00binarygarbagehere")
        _, atts = expand_at_mentions("@doc.pdf", cwd=self.cwd)
        att = atts[0]
        self.assertNotIn("content", att)
        rendered = format_at_mention_attachments(atts)
        self.assertNotIn("binarygarbagehere", rendered)
        # The reminder structure should be intact and reference the file.
        self.assertIn("<system-reminder>", rendered)
        self.assertIn("doc.pdf", rendered)
        self.assertIn("binary", rendered.lower())

    def test_archive_extension_routes_to_binary(self) -> None:
        self._write("payload.zip", b"PK\x03\x04 archive bytes here")
        _, atts = expand_at_mentions("@payload.zip", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "binary")
        # Generic hint for non-PDF binaries.
        self.assertNotIn("pages", atts[0]["hint"])

    def test_docx_routes_to_binary(self) -> None:
        # docx is just a zip; same extension-based detection should fire.
        self._write("report.docx", b"PK\x03\x04 docx-ish bytes")
        _, atts = expand_at_mentions("@report.docx", cwd=self.cwd)
        self.assertEqual(atts[0]["kind"], "binary")

    def test_text_file_unaffected(self) -> None:
        self._write("notes.txt", b"hello world\nline two\n")
        _, atts = expand_at_mentions("@notes.txt", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertIn("hello world", atts[0]["content"])

    def test_unknown_extension_with_nul_bytes_caught_by_sniffer(self) -> None:
        """Defense in depth: a misnamed binary (e.g. .dat / .txt) whose
        extension we haven't enumerated should still be classified as
        binary based on a NUL byte in the head of the file. Prevents the
        mojibake regression from sneaking in via any extension we missed."""
        self._write("blob.dat", b"\x00\x01\x02 actual binary content")
        _, atts = expand_at_mentions("@blob.dat", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "binary")

    def test_text_without_nul_bytes_still_text(self) -> None:
        """Regression: the sniffer must only fire on actual NULs, not on
        legitimate non-ASCII text. UTF-8 source code with snowmen and
        accents must still land as ``kind=file``."""
        self._write("notes.md", "héllo ☃ wörld\n".encode("utf-8"))
        _, atts = expand_at_mentions("@notes.md", cwd=self.cwd)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertIn("héllo", atts[0]["content"])

    def test_utf16_le_bom_decoded_properly_no_mojibake(self) -> None:
        """Windows-emitted UTF-16-LE files have NUL bytes for every ASCII
        char and would trip the NUL sniffer. A BOM prefix tells us the
        file is real text that just needs a different decoder; the text
        branch picks the codec from the BOM and produces clean text — NOT
        the mojibake-with-embedded-NULs that an unconditional utf-8 read
        would produce (that would re-introduce the exact failure mode
        this whole work was designed to prevent).

        The test asserts BOTH on the attachment content AND on the
        rendered system-reminder bytes, because the prompt-time failure
        mode is mojibake-in-system-reminder. Asserting only the routing
        decision (kind=file) would pin a passing bug.
        """
        self._write("win.log", b"\xff\xfe" + "hello world\nsecond line\n".encode("utf-16-le"))
        _, atts = expand_at_mentions("@win.log", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertEqual(atts[0]["content"], "hello world\nsecond line\n")
        # CRITICAL: rendered prompt bytes must NOT contain NULs or utf-8
        # replacement chars. A regression here is exactly Bug A.
        rendered = _render(atts)
        self.assertNotIn(b"\x00", rendered)
        self.assertNotIn(b"\xef\xbf\xbd", rendered)  # utf-8 of U+FFFD replacement char
        self.assertIn(b"hello world", rendered)

    def test_utf16_be_bom_decoded_properly(self) -> None:
        self._write("be.log", b"\xfe\xff" + "hello\n".encode("utf-16-be"))
        _, atts = expand_at_mentions("@be.log", cwd=self.cwd)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertEqual(atts[0]["content"], "hello\n")
        rendered = _render(atts)
        self.assertNotIn(b"\x00", rendered)
        self.assertNotIn(b"\xef\xbf\xbd", rendered)
        self.assertIn(b"hello", rendered)

    def test_utf8_bom_stripped(self) -> None:
        """UTF-8 BOM (rare, but Windows-emitted) — must be transparently
        decoded so the BOM character itself doesn't end up in the prompt
        content. ``utf-8-sig`` codec handles this."""
        self._write("bom.md", b"\xef\xbb\xbfhello\n")
        _, atts = expand_at_mentions("@bom.md", cwd=self.cwd)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertEqual(atts[0]["content"], "hello\n")
        # BOM character (U+FEFF) must NOT appear in either content or render.
        self.assertNotIn("﻿", atts[0]["content"])
        rendered = _render(atts)
        self.assertNotIn(b"\xef\xbb\xbf", rendered)

    def test_utf32_le_bom_decoded(self) -> None:
        self._write("u32.log", b"\xff\xfe\x00\x00" + "hi\n".encode("utf-32-le"))
        _, atts = expand_at_mentions("@u32.log", cwd=self.cwd)
        self.assertEqual(atts[0]["kind"], "file")
        self.assertEqual(atts[0]["content"], "hi\n")
        rendered = _render(atts)
        self.assertNotIn(b"\x00", rendered)

    def test_no_bom_utf16_le_still_classified_binary(self) -> None:
        """A UTF-16 LE file WITHOUT a BOM still has NULs and we have no
        signal to tell it from a binary blob. Falls through to the
        binary branch — strictly safer than shipping NUL-laden mojibake."""
        self._write("nobom.log", "hello world\n".encode("utf-16-le"))
        _, atts = expand_at_mentions("@nobom.log", cwd=self.cwd)
        self.assertEqual(atts[0]["kind"], "binary")

    def test_spoofed_bom_binary_blob_does_not_leak_bug_a_signatures(self) -> None:
        """Adversarial case: a binary blob whose extension is NOT in
        ``_AT_MENTION_BINARY_EXTENSIONS`` and whose head bytes start
        with a valid-looking UTF-16 BOM. UTF-16 decodes every 2-byte
        pair to *some* Unicode codepoint, so the BOM short-circuit
        produces a wall of nonsensical CJK/symbol characters rather
        than NUL- or U+FFFD-laden mojibake.

        The Bug A failure family is specifically about NUL bytes and
        utf-8 replacement chars being smuggled into a system-reminder
        and the model latching onto recognisable ASCII fragments. This
        test pins THAT invariant — the rendered prompt bytes must not
        contain NUL or U+FFFD — and accepts that an adversarial blob
        may still get inlined as text. A stronger garble detector that
        flagged this case would false-positive on legitimate
        non-Latin-script UTF-16 files (Chinese/Japanese logs, etc.).

        If a future failure mode hinges on recognisable English-ASCII
        fragments surviving the UTF-16 decode, that's the trigger to
        revisit — not this case.
        """
        # Bytes after the BOM decode to high-plane codepoints (no
        # English text in the output), so even if the wall-of-glyphs
        # ships to the model there's no ASCII to latch onto.
        payload = b"\xff\xfe" + bytes(range(1, 200)) * 4
        self._write("blob.dat", payload)
        _, atts = expand_at_mentions("@blob.dat", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        # CRITICAL: Bug A invariants — no NULs, no U+FFFD replacement chars
        # in the rendered prompt bytes. The walk-of-glyphs is acceptable;
        # NUL- or U+FFFD-laden mojibake is not.
        rendered = _render(atts)
        self.assertNotIn(b"\x00", rendered)
        self.assertNotIn(b"\xef\xbf\xbd", rendered)

    def test_nul_heavy_decode_falls_back_to_binary(self) -> None:
        """Adversarial case found mid-review: a binary blob with a
        spoofed UTF-16 BOM and NUL-heavy body. The UTF-16 decode maps
        each NUL pair to U+0000 — Python str chars that round-trip
        BACK to NUL bytes when encoded to utf-8 for the API. Without
        the NUL-char check in ``_decoded_text_looks_garbled`` the
        prompt bytes would carry literal NULs — exactly Bug A's
        failure mode."""
        payload = b"\xff\xfe" + b"\x00" * 400
        self._write("nuly.dat", payload)
        _, atts = expand_at_mentions("@nuly.dat", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "binary",
                         "NUL-heavy decode must NOT inline as kind=file")
        rendered = _render(atts)
        self.assertNotIn(b"\x00", rendered)

    def test_high_replacement_char_decode_falls_back_to_binary(self) -> None:
        """A file whose decoded form is *mostly* U+FFFD replacement
        characters is rejected from the text branch — that pattern is
        a high-confidence signal that the file isn't the encoding the
        BOM (or default utf-8) implied. Specifically pins the
        ``_decoded_text_looks_garbled`` threshold so a regression that
        opens the floodgates again gets caught.

        Constructed without a BOM so it falls through to utf-8 decode,
        where deliberately-invalid utf-8 byte sequences produce U+FFFD.
        """
        # Mostly 0x80-0xBF bytes (utf-8 continuation bytes with no
        # leading byte → each one becomes U+FFFD). Mixed with some
        # printable ASCII to keep len > 0.
        payload = b"!" + b"\x80\x81\x82\x83" * 200
        self._write("noisy.bad", payload)
        _, atts = expand_at_mentions("@noisy.bad", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["kind"], "binary",
                         "decoded text with high U+FFFD fraction must NOT inline as kind=file")
        rendered = _render(atts)
        self.assertNotIn(b"\xef\xbf\xbd", rendered)

    def test_image_still_routes_to_image_branch(self) -> None:
        """Regression: image extensions must keep going through the image
        pipeline -- binary branch must come AFTER image branch in the
        dispatch order, so PNG/JPEG never lose their inline behaviour."""
        # Build a real 1x1 PNG via Pillow so the image pipeline actually
        # produces an attachment; otherwise the test couldn't tell apart
        # "image branch dropped silently" from "binary branch fired".
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow required")
        import io
        buf = io.BytesIO()
        Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, format="PNG")
        self._write("pixel.png", buf.getvalue())
        _, atts = expand_at_mentions("@pixel.png", cwd=self.cwd)
        self.assertEqual(len(atts), 1)
        # Image attachment, NOT binary.
        self.assertEqual(atts[0]["kind"], "image")


class TestBinaryAttachmentRendering(unittest.TestCase):
    def test_rendered_reminder_for_pdf(self) -> None:
        att = {
            "kind": "binary",
            "path": "/x/y/report.pdf",
            "display_path": "report.pdf",
            "ext": "pdf",
            "hint": "PDFs cannot be inlined as @-mention text. Use the Read tool with the ``pages`` parameter.",
        }
        rendered = format_at_mention_attachments([att])
        self.assertIn("<system-reminder>", rendered)
        self.assertIn("report.pdf", rendered)
        self.assertIn("binary", rendered.lower())
        self.assertIn("Read tool", rendered)

    def test_rendered_reminder_for_generic_binary(self) -> None:
        att = {
            "kind": "binary",
            "path": "/x/y/blob.bin",
            "display_path": "blob.bin",
            "ext": "bin",
            "hint": "This file is binary and cannot be inlined as text.",
        }
        rendered = format_at_mention_attachments([att])
        self.assertIn("blob.bin", rendered)
        self.assertIn("binary", rendered.lower())


if __name__ == "__main__":
    unittest.main()
