"""Clipboard / dropped-path image ingestion.

Covers ``src/utils/image_paste.py`` (the port of
``typescript/src/utils/imagePaste.ts``) and the agent-server control handlers
that queue an attached image onto the next prompt.

Before this existed, ui-tui called ``image.attach`` / ``input.detect_drop`` on
every pasted path and ``gatewayClient`` had no case for either, so both resolved
``{}`` and the paste silently degraded to inserting the path as literal text.
There was no clipboard-bytes path at all — pasting a screenshot did nothing.
"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest import mock

from src.utils import image_paste as ip
from src.utils.image_processor import ImageDimensions


def _png(width: int = 40, height: int = 20, color=(10, 20, 200)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _bmp(width: int = 30, height: int = 30) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 10, 10)).save(buf, format="BMP")
    return buf.getvalue()


class TestPathNormalization(unittest.TestCase):
    def test_plain_image_paths(self) -> None:
        for path in ("/tmp/a.png", "/tmp/a.JPG", "shot.jpeg", "x.gif", "y.webp", "z.bmp"):
            with self.subTest(path=path):
                self.assertTrue(ip.is_image_file_path(path))

    def test_non_image_paths_declined(self) -> None:
        for path in ("/tmp/notes.txt", "/tmp/archive.tar.gz", "README", "a.png.txt"):
            with self.subTest(path=path):
                self.assertFalse(ip.is_image_file_path(path))

    def test_outer_quotes_stripped(self) -> None:
        self.assertEqual(ip.as_image_file_path('"/tmp/b.jpeg"'), "/tmp/b.jpeg")
        self.assertEqual(ip.as_image_file_path("'/tmp/b.jpeg'"), "/tmp/b.jpeg")

    @unittest.skipIf(sys.platform == "win32", "backslashes are separators on Windows")
    def test_shell_escapes_removed(self) -> None:
        """A terminal escapes a dragged path; the file name has no backslashes."""
        self.assertEqual(
            ip.as_image_file_path(r"/tmp/name\ \(15\).png"), "/tmp/name (15).png"
        )

    @unittest.skipIf(sys.platform == "win32", "backslashes are separators on Windows")
    def test_double_backslash_is_a_literal_backslash(self) -> None:
        self.assertEqual(ip.as_image_file_path(r"/tmp/a\\b.png"), "/tmp/a\\b.png")

    @unittest.skipIf(sys.platform == "win32", "backslashes are separators on Windows")
    def test_placeholder_in_filename_cannot_steer_unescaping(self) -> None:
        """The salted placeholder defends against a crafted filename.

        A fixed sentinel would let a path containing that sentinel come back out
        as a backslash. The salt makes the sentinel unguessable per call.

        Pin it properly: force a KNOWN salt, then feed the exact sentinel that
        salt produces. Without the salt (a fixed sentinel) this input would come
        back with a backslash spliced in. A test using an arbitrary
        ``__DOUBLE_BACKSLASH_deadbeef__`` string passes with or without the salt
        and proves nothing.
        """
        salt = "ab" * 8  # b16encode(os.urandom(8)) is 16 hex chars
        sentinel = f"__DOUBLE_BACKSLASH_{salt}__"
        crafted = f"/tmp/{sentinel}.png"
        with mock.patch("os.urandom", return_value=bytes.fromhex(salt.lower())):
            got = ip.as_image_file_path(crafted)
        self.assertEqual(got, crafted, "a crafted sentinel must survive verbatim")
        self.assertNotIn("\\", got)

    def test_looks_like_dropped_path_rejects_prose(self) -> None:
        self.assertFalse(ip.looks_like_dropped_path("here is a\nmultiline paste"))
        self.assertFalse(ip.looks_like_dropped_path("   "))
        self.assertTrue(ip.looks_like_dropped_path("/tmp/a.png"))


class TestReadFromPath(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = mock.patch.dict("os.environ", {}, clear=False)
        self._tmp.start()
        self.addCleanup(self._tmp.stop)

    def _write(self, name: str, data: bytes) -> Path:
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / name
        p.write_bytes(data)
        return p

    def test_reads_absolute_path(self) -> None:
        p = self._write("a.png", _png())
        got = ip.try_read_image_from_path(str(p))
        self.assertIsNotNone(got)
        self.assertEqual(got.media_type, "image/png")
        self.assertEqual(got.source_path, str(p))
        self.assertTrue(base64.b64decode(got.base64).startswith(b"\x89PNG"))

    def test_reads_relative_path_against_cwd(self) -> None:
        p = self._write("rel.png", _png())
        got = ip.try_read_image_from_path("rel.png", cwd=p.parent)
        self.assertIsNotNone(got)
        self.assertEqual(got.media_type, "image/png")

    def test_downsamples_past_the_dimension_cap(self) -> None:
        from src.utils.image_processor import IMAGE_MAX_WIDTH

        p = self._write("big.png", _png(2400, 1400))
        got = ip.try_read_image_from_path(str(p))
        self.assertIsNotNone(got)
        self.assertEqual(got.dimensions.original_width, 2400)
        self.assertEqual(got.dimensions.display_width, IMAGE_MAX_WIDTH)
        self.assertLess(got.dimensions.display_width, got.dimensions.original_width)

    def test_bmp_converted_to_png(self) -> None:
        """The API rejects image/bmp, and Windows/WSL2 paste BMP for a screenshot."""
        p = self._write("shot.bmp", _bmp())
        got = ip.try_read_image_from_path(str(p))
        self.assertIsNotNone(got)
        self.assertEqual(got.media_type, "image/png")
        self.assertTrue(base64.b64decode(got.base64).startswith(b"\x89PNG"))

    def test_missing_file_declines(self) -> None:
        self.assertIsNone(ip.try_read_image_from_path("/nonexistent/nope.png"))

    def test_empty_file_declines(self) -> None:
        p = self._write("empty.png", b"")
        self.assertIsNone(ip.try_read_image_from_path(str(p)))

    def test_non_image_bytes_with_image_extension_decline(self) -> None:
        """A .png that isn't a PNG must not reach the API as one."""
        p = self._write("liar.png", b"this is not an image")
        self.assertIsNone(ip.try_read_image_from_path(str(p)))

    def test_non_image_extension_never_read(self) -> None:
        p = self._write("notes.txt", b"hello")
        self.assertIsNone(ip.try_read_image_from_path(str(p)))

    def test_basename_falls_back_to_clipboard_path(self) -> None:
        """VS Code pastes only the filename; the clipboard holds the directory."""
        p = self._write("vscode.png", _png())
        with mock.patch.object(ip, "get_image_path_from_clipboard", return_value=str(p)):
            got = ip.try_read_image_from_path("vscode.png", cwd=Path("/nonexistent"))
        self.assertIsNotNone(got)
        self.assertEqual(got.source_path, str(p))


class TestClipboardRead(unittest.TestCase):
    def test_returns_none_when_helper_missing(self) -> None:
        with mock.patch.object(ip, "_run", return_value=None):
            self.assertIsNone(ip.get_image_from_clipboard())
            self.assertFalse(ip.has_image_in_clipboard())

    def test_returns_none_when_clipboard_has_no_image(self) -> None:
        fail = mock.Mock(returncode=1, stdout=b"", stderr=b"")
        with mock.patch.object(ip, "_run", return_value=fail):
            self.assertIsNone(ip.get_image_from_clipboard())

    def test_decodes_written_bytes(self) -> None:
        """The helper writes a temp PNG; the reader picks it up and cleans up."""
        import shutil
        import tempfile

        payload = _png(60, 30)
        # _screenshot_path() mints a FRESH private mkdtemp per call (so a shared
        # /tmp cannot be symlink-attacked and no quote can reach the AppleScript
        # literal), so pin it here rather than calling it twice.
        holder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(holder, ignore_errors=True))
        target = holder / "shot.png"

        def fake_run(argv, *, shell=False):
            target.write_bytes(payload)
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(ip, "_screenshot_path", return_value=target), \
                mock.patch.object(ip, "_run", side_effect=fake_run):
            got = ip.get_image_from_clipboard()

        self.assertIsNotNone(got)
        self.assertEqual(got.media_type, "image/png")
        self.assertIsNone(got.source_path, "clipboard images have no source path")
        self.assertFalse(target.exists(), "temp screenshot must be cleaned up")

    def test_temp_path_is_private_and_unpredictable(self) -> None:
        """No fixed name in a shared temp dir (CWE-377/59 on Linux)."""
        import stat

        first = ip._screenshot_path()
        second = ip._screenshot_path()
        self.addCleanup(lambda: first.parent.rmdir())
        self.addCleanup(lambda: second.parent.rmdir())
        self.assertNotEqual(first.parent, second.parent)
        mode = stat.S_IMODE(first.parent.stat().st_mode)
        self.assertEqual(mode, 0o700, f"temp dir must be private, got {oct(mode)}")
        self.assertNotIn("'", str(first))
        self.assertNotIn('"', str(first))

    def test_timeout_is_bounded(self) -> None:
        """A wedged clipboard helper must not hang the turn forever."""
        import subprocess

        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=1),
        ):
            self.assertIsNone(ip._run(["osascript", "-e", "x"]))
        self.assertLessEqual(ip.CLIPBOARD_TIMEOUT_S, 60)


class TestLinuxCommands(unittest.TestCase):
    def test_check_command_covers_every_mime(self) -> None:
        cmd = ip.build_linux_clipboard_check_command()
        for mime in ip.LINUX_CLIPBOARD_IMAGE_MIME_TYPES:
            self.assertIn(mime.replace("/", r"\/"), cmd)
        self.assertIn("xclip", cmd)
        self.assertIn("wl-paste", cmd)

    def test_save_command_tries_both_backends(self) -> None:
        cmd = ip.build_linux_clipboard_save_command("/tmp/x.png")
        self.assertIn("xclip -selection clipboard -t image/png", cmd)
        self.assertIn("wl-paste --type image/png", cmd)
        self.assertIn(" || ", cmd)


class TestDimensionsWire(unittest.TestCase):
    def test_prefers_display_size(self) -> None:
        dims = ImageDimensions(
            original_width=2400, original_height=1400,
            display_width=1568, display_height=914,
        )
        self.assertEqual(ip.dimensions_to_wire(dims), {"width": 1568, "height": 914})

    def test_falls_back_to_original(self) -> None:
        dims = ImageDimensions(original_width=40, original_height=20)
        self.assertEqual(ip.dimensions_to_wire(dims), {"width": 40, "height": 20})

    def test_none_is_empty(self) -> None:
        self.assertEqual(ip.dimensions_to_wire(None), {})



class TestFileUriDecoding(unittest.TestCase):
    """macOS puts a percent-encoded file:// URI on the clipboard for a dragged
    screenshot, and the client's looksLikeDroppedPath deliberately accepts those.
    Without decoding here every such paste missed on disk and degraded to text.
    """

    def test_percent_encoded_screenshot_uri(self) -> None:
        uri = (
            "file:///var/folders/x/T/TemporaryItems/"
            "Screenshot%202026-04-21%20at%201.04.43%20PM.png"
        )
        self.assertEqual(
            ip.as_image_file_path(uri),
            "/var/folders/x/T/TemporaryItems/Screenshot 2026-04-21 at 1.04.43 PM.png",
        )

    def test_plain_file_uri(self) -> None:
        self.assertEqual(ip.as_image_file_path("file:///tmp/plain.png"), "/tmp/plain.png")

    def test_localhost_authority_is_local(self) -> None:
        self.assertEqual(
            ip.as_image_file_path("file://localhost/tmp/local.png"), "/tmp/local.png"
        )

    def test_unc_authority_is_not_treated_as_local(self) -> None:
        """file://server/share is a remote host, not a path on this machine."""
        self.assertIsNone(ip.as_image_file_path("file://server/share/remote.png"))

    def test_remote_urls_declined(self) -> None:
        """A pasted link must fall through to a text paste, not error as a path."""
        for url in (
            "https://example.com/image.png",
            "http://localhost/x.png",
            "ftp://host/y.jpg",
            "data:image/png;base64,AAAA",
        ):
            with self.subTest(url=url):
                self.assertIsNone(ip.as_image_file_path(url))

    def test_reads_a_real_file_through_a_uri(self) -> None:
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / "shot with spaces.png"
        p.write_bytes(_png())
        uri = "file://" + str(p).replace(" ", "%20")
        got = ip.try_read_image_from_path(uri)
        self.assertIsNotNone(got)
        self.assertEqual(got.source_path, str(p))


class TestApiFormatEnvelope(unittest.TestCase):
    """Everything handed to the API must BE one of its four formats.

    Two silent-failure bugs lived here. (1) ``format_hint`` was passed as a bare
    extension, but ``maybe_resize_image`` uses it as the media-type fallback and
    then hands it to the encoder -- so every BMP over the size/dimension caps
    raised "Unsupported image encoding type: png" and was dropped. That is
    exactly the full-screen Windows/WSL2 screenshot BMP exists for, and the
    original 30x30 BMP test took the fast path and never reached the encoder.
    (2) ``detect_image_format_from_buffer`` DEFAULTS to image/png on unknown
    magic, so a TIFF in a .png shipped labeled image/png and 400'd.
    """

    def _write(self, name: str, data: bytes) -> Path:
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / name
        p.write_bytes(data)
        return p

    def _save(self, fmt: str, size=(1920, 1080)) -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", size, (40, 80, 160)).save(buf, format=fmt)
        return buf.getvalue()

    def test_oversized_bmp_survives_the_resize_path(self) -> None:
        from src.utils.image_processor import IMAGE_MAX_WIDTH

        got = ip.try_read_image_from_path(str(self._write("big.bmp", self._save("BMP"))))
        self.assertIsNotNone(got, "an oversized BMP must not be dropped")
        self.assertEqual(got.media_type, "image/png")
        self.assertEqual(got.dimensions.display_width, IMAGE_MAX_WIDTH)
        self.assertTrue(base64.b64decode(got.base64).startswith(b"\x89PNG"))

    def test_unsupported_extension_declined_before_reading(self) -> None:
        """``.tiff`` is not an accepted input extension, so it never gets read.

        The gate is the extension allow-list (``IMAGE_EXTENSION_RE``), matching
        the reference's ``IMAGE_EXTENSION_REGEX``. TIFF *content* inside an
        accepted extension is a different case -- covered below, where it must be
        re-encoded rather than relabeled.
        """
        self.assertIsNone(
            ip.try_read_image_from_path(str(self._write("shot.tiff", self._save("TIFF"))))
        )

    def test_tiff_bytes_in_a_png_filename(self) -> None:
        got = ip.try_read_image_from_path(
            str(self._write("liar.png", self._save("TIFF", (20, 20))))
        )
        self.assertIsNotNone(got)
        raw = base64.b64decode(got.base64)
        self.assertEqual(got.media_type, "image/png")
        self.assertTrue(raw.startswith(b"\x89PNG"), "must not ship TIFF bytes as PNG")

    def test_declared_media_type_always_matches_the_bytes(self) -> None:
        magic = {
            "image/png": b"\x89PNG",
            "image/jpeg": b"\xff\xd8\xff",
            "image/gif": b"GIF8",
            "image/webp": b"RIFF",
        }
        # Every extension the input allow-list accepts, at a size that forces the
        # resize path (where the encoder is reached and the old hint bug fired).
        for fmt, name in (("PNG", "a.png"), ("JPEG", "b.jpg"), ("GIF", "c.gif"),
                          ("WEBP", "d.webp"), ("BMP", "e.bmp")):
            with self.subTest(fmt=fmt):
                got = ip.try_read_image_from_path(
                    str(self._write(name, self._save(fmt, (2400, 1400))))
                )
                self.assertIsNotNone(got, f"{fmt} was dropped")
                self.assertIn(got.media_type, magic, f"{got.media_type} not API-supported")
                self.assertTrue(
                    base64.b64decode(got.base64).startswith(magic[got.media_type]),
                    f"{fmt} declared {got.media_type} but the bytes disagree",
                )

    def test_as_media_type_normalizes_extensions(self) -> None:
        self.assertEqual(ip._as_media_type("png"), "image/png")
        self.assertEqual(ip._as_media_type("jpg"), "image/jpeg")
        self.assertEqual(ip._as_media_type("jpeg"), "image/jpeg")
        self.assertEqual(ip._as_media_type("bmp"), "image/png")
        self.assertEqual(ip._as_media_type("tiff"), "image/png")
        self.assertEqual(ip._as_media_type("image/webp"), "image/webp")
        self.assertEqual(ip._as_media_type("image/bmp"), "image/png")
        self.assertEqual(ip._as_media_type(None), "image/png")


class TestDroppedPathPredicate(unittest.TestCase):
    """A faithful port of the client's looksLikeDroppedPath.

    ``useSubmission.ts`` runs ``input.detect_drop`` on submitted prompts, and
    wiring that RPC made the backend gate live for the first time. The original
    predicate only rejected tabs and NULs, so ``README.md`` matched, resolved to
    a real file, and the user's typed prompt would have been silently rewritten
    to ``@/abs/path/README.md``.
    """

    def test_prose_is_never_a_path(self) -> None:
        for text in (
            "hello world",
            "fix the bug in main.py",
            "what is this?",
            "README.md",
            "src/utils/image_paste.py",
            "explain README.md to me",
            "/help",
            "/model sonnet",
            "/api",
            "",
            "   ",
        ):
            with self.subTest(text=text):
                self.assertFalse(
                    ip.looks_like_dropped_path(text), f"{text!r} must not look like a path"
                )

    def test_explicit_path_forms_match(self) -> None:
        for text in (
            "/tmp/a.png",
            "/etc/hosts",
            "/usr/bin/test",
            "~/Desktop/shot.png",
            "./rel.png",
            "../up.png",
            '"/tmp/quoted abs.png"',
            "'/tmp/single.png'",
            "file:///tmp/z.png",
            "C:/Users/a/b.png",
            r"C:\Users\a\b.png",
            '"C:/Users/a/b.png"',
        ):
            with self.subTest(text=text):
                self.assertTrue(ip.looks_like_dropped_path(text), f"{text!r} is a path")

    def test_multiline_never_matches(self) -> None:
        self.assertFalse(ip.looks_like_dropped_path("/tmp/a.png\n/tmp/b.png"))

    def test_prose_ending_in_an_extension_does_not_shell_out(self) -> None:
        """A miss must not reach the clipboard just because it ends in .png."""
        with mock.patch.object(ip, "get_image_path_from_clipboard") as clip:
            ip.try_read_image_from_path("what is wrong with logo.png")
        clip.assert_not_called()


class TestWindowsFileUri(unittest.TestCase):
    def test_drive_letter_uri_is_absolute(self) -> None:
        """file:///C:/… parses to /C:/…, which PureWindowsPath reads as RELATIVE."""
        self.assertEqual(
            ip.as_image_file_path("file:///C:/Users/a/b.png"), "C:/Users/a/b.png"
        )

    def test_powershell_single_quotes_escaped(self) -> None:
        """%TEMP% for a user named O'Brien would otherwise be a parse error."""
        self.assertEqual(
            ip._escape_powershell_single_quoted(r"C:\Users\O'Brien\Temp"),
            r"C:\Users\O''Brien\Temp",
        )


if __name__ == "__main__":
    unittest.main()
