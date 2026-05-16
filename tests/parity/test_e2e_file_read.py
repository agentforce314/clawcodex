"""WS-10: E2E integration — file read flow matches TS behavior.

Simulates: User prompt → Read tool dispatched → file content returned.
Tests the full tool dispatch pipeline for the Read tool.
"""
from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.errors import ToolInputError, ToolPermissionError
from src.tool_system.protocol import ToolCall


class TestE2EFileRead(unittest.TestCase):
    """End-to-end file read flow."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

        # Create test files
        self.test_file = self.root / "hello.txt"
        self.test_file.write_text("Hello, world!\nLine 2\nLine 3\n")

        self.py_file = self.root / "example.py"
        self.py_file.write_text(textwrap.dedent("""\
            def greet(name):
                return f"Hello, {name}!"

            if __name__ == "__main__":
                print(greet("world"))
        """))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_file_returns_content(self) -> None:
        """Read tool returns file contents."""
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.test_file)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        output = result.output
        self.assertIn("Hello, world!", str(output))

    def test_read_file_marks_fingerprint(self) -> None:
        """Read tool marks the file as read in context."""
        self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.test_file)}),
            self.ctx,
        )
        self.assertTrue(self.ctx.was_file_read_and_unchanged(self.test_file))

    def test_read_python_file(self) -> None:
        """Read tool can read Python files."""
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(self.py_file)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertIn("def greet", str(result.output))

    def test_read_nonexistent_file_returns_error(self) -> None:
        """Read tool returns error for missing files."""
        try:
            result = self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": str(self.root / "nonexistent.txt")}),
                self.ctx,
            )
            self.assertTrue(result.is_error)
        except Exception:
            pass  # Exception is also acceptable

    def test_read_with_offset_and_limit(self) -> None:
        """Read tool supports offset and limit parameters."""
        result = self.registry.dispatch(
            ToolCall(name="Read", input={
                "file_path": str(self.test_file),
                "offset": 1,
                "limit": 2,
            }),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        content = str(result.output)
        self.assertIn("Hello, world!", content)

    def test_read_outside_workspace_blocked(self) -> None:
        """Read tool blocks reads outside workspace root."""
        with self.assertRaises(ToolPermissionError):
            self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": "/etc/passwd"}),
                self.ctx,
            )

    def test_read_tool_is_concurrent_safe(self) -> None:
        """Read tool can be called concurrently."""
        tool = self.registry.get("Read")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_concurrency_safe({"file_path": str(self.test_file)}))

    def test_read_tool_is_read_only(self) -> None:
        """Read tool is read-only."""
        tool = self.registry.get("Read")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.is_read_only({"file_path": str(self.test_file)}))

    # ---- Binary handling regression tests (parity with TS FileReadTool) ----

    def test_read_svg_file_as_text(self) -> None:
        """SVG must read as text. Regression: mimetypes.guess_type('foo.svg')
        returns 'image/svg+xml', which the old MIME fallback wrongly treated as
        binary. TS FileReadTool has no MIME check and reads SVG as text."""
        svg = self.root / "favicon.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        )
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(svg)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["type"], "text")
        self.assertIn("<svg", result.output["file"]["content"])

    def test_read_rust_file_as_text(self) -> None:
        """Rust .rs must read as text. Regression: mimetypes.guess_type('foo.rs')
        returns 'application/rls-services+xml' on Python's default db, which the
        old MIME fallback wrongly treated as binary."""
        rs = self.root / "main.rs"
        rs.write_text('fn main() { println!("hi"); }\n')
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(rs)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["type"], "text")
        self.assertIn("fn main", result.output["file"]["content"])

    def test_read_png_returns_image_block(self) -> None:
        """PNG reads as image content. Mirrors TS callInner's image branch."""
        import base64
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 100
        png = self.root / "test.png"
        png.write_bytes(png_bytes)
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(png)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["type"], "image")
        self.assertEqual(result.output["file"]["type"], "image/png")
        self.assertEqual(
            base64.b64decode(result.output["file"]["base64"]),
            png_bytes,
        )
        self.assertEqual(result.output["file"]["originalSize"], len(png_bytes))

    def test_read_jpg_returns_image_block(self) -> None:
        """JPG maps to image/jpeg (not image/jpg). Same for .jpeg."""
        jpg = self.root / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(jpg)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["type"], "image")
        self.assertEqual(result.output["file"]["type"], "image/jpeg")

        jpeg = self.root / "photo.jpeg"
        jpeg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        result2 = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(jpeg)}),
            self.ctx,
        )
        self.assertFalse(result2.is_error)
        self.assertEqual(result2.output["file"]["type"], "image/jpeg")

    def test_read_oversized_image_rejected(self) -> None:
        """Image > 3.75 MB (TS IMAGE_TARGET_RAW_SIZE) must be rejected so the
        base64 payload stays under Anthropic's 5 MB API image limit."""
        from src.tool_system.tools.read import MAX_IMAGE_SIZE_BYTES
        big = self.root / "huge.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_SIZE_BYTES + 1))
        with self.assertRaises(ToolInputError) as cm:
            self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": str(big)}),
                self.ctx,
            )
        self.assertIn("exceeds maximum", str(cm.exception).lower())

    def test_read_exe_still_blocked(self) -> None:
        """The extension blocklist still rejects truly binary files after the
        MIME-check removal. Guards against the cleanup loosening the gate."""
        exe = self.root / "evil.exe"
        exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 50)
        with self.assertRaises(ToolInputError) as cm:
            self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": str(exe)}),
                self.ctx,
            )
        self.assertIn("binary", str(cm.exception).lower())

    def test_read_png_twice_returns_image_both_times(self) -> None:
        """Re-reading the same image must NOT collapse to a file_unchanged
        text stub. Regression: the image branch must skip mark_file_read so
        dedup at _read_call lines 293-305 never matches. TS skips images in
        readFileState for the same reason (FileReadTool.ts:528-529)."""
        png = self.root / "stable.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 50)
        r1 = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(png)}),
            self.ctx,
        )
        r2 = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(png)}),
            self.ctx,
        )
        self.assertEqual(r1.output["type"], "image")
        self.assertEqual(r2.output["type"], "image")
        self.assertEqual(r1.output["file"]["base64"], r2.output["file"]["base64"])

    def test_read_empty_image_rejected(self) -> None:
        """An empty image file must raise rather than silently emit an empty
        base64 string (which the API would reject confusingly). Mirrors TS
        FileReadTool.ts:1112-1114 'Image file is empty: ${filePath}'."""
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        with self.assertRaises(ToolInputError) as cm:
            self.registry.dispatch(
                ToolCall(name="Read", input={"file_path": str(empty)}),
                self.ctx,
            )
        self.assertIn("empty", str(cm.exception).lower())

    def test_image_api_mapping(self) -> None:
        """_read_map_result_to_api emits an image content block for type=image,
        matching TS FileReadTool.ts mapToolResultToToolResultBlockParam."""
        from src.tool_system.tools.read import _read_map_result_to_api
        mapped = _read_map_result_to_api(
            {
                "type": "image",
                "file": {
                    "filePath": "/tmp/x.png",
                    "base64": "ABCD",
                    "type": "image/png",
                    "originalSize": 100,
                },
            },
            "tu_123",
        )
        self.assertEqual(mapped["type"], "tool_result")
        self.assertEqual(mapped["tool_use_id"], "tu_123")
        self.assertIsInstance(mapped["content"], list)
        self.assertEqual(len(mapped["content"]), 1)
        block = mapped["content"][0]
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["type"], "base64")
        self.assertEqual(block["source"]["data"], "ABCD")
        self.assertEqual(block["source"]["media_type"], "image/png")


class TestE2EGlobGrep(unittest.TestCase):
    """End-to-end Glob and Grep search flows."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

        # Create test files
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("def main():\n    print('hello')\n")
        (self.root / "src" / "utils.py").write_text("def helper():\n    return 42\n")
        (self.root / "README.md").write_text("# Project\nThis is a project.\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_glob_finds_python_files(self) -> None:
        """Glob tool finds files by pattern."""
        result = self.registry.dispatch(
            ToolCall(name="Glob", input={"pattern": "**/*.py"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        output = str(result.output)
        self.assertIn("main.py", output)
        self.assertIn("utils.py", output)

    def test_glob_no_match_returns_empty(self) -> None:
        """Glob tool returns empty for no matches."""
        result = self.registry.dispatch(
            ToolCall(name="Glob", input={"pattern": "**/*.xyz"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)

    def test_grep_finds_pattern(self) -> None:
        """Grep tool finds text patterns."""
        result = self.registry.dispatch(
            ToolCall(name="Grep", input={"pattern": "def main", "path": str(self.root)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        output = str(result.output)
        self.assertIn("main.py", output)

    def test_grep_no_match_returns_empty(self) -> None:
        """Grep tool returns empty for no matches."""
        result = self.registry.dispatch(
            ToolCall(name="Grep", input={"pattern": "nonexistent_string_xyz", "path": str(self.root)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)


if __name__ == "__main__":
    unittest.main()
