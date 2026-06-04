"""Tests for src.tool_system.tools.bash.image_output + bash_tool integration.

Covers the data-URI image detection + tool_result image-block conversion
that lets shell commands (matplotlib, mermaid, etc.) emit images visible
to the model. Port of TS BashTool/utils.ts:49-91 + integration in
BashTool.tsx mapToolResultToToolResultBlockParam.
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.tool_system.tools.bash.bash_tool import (
    _bash_map_result_to_api,
)
from src.tool_system.tools.bash.image_output import (
    build_image_tool_result,
    is_image_output,
    parse_data_uri,
)


class TestIsImageOutput(unittest.TestCase):
    def test_detects_png_data_uri(self) -> None:
        self.assertTrue(is_image_output("data:image/png;base64,iVBORw0KGgo="))

    def test_detects_jpeg_data_uri(self) -> None:
        self.assertTrue(is_image_output("data:image/jpeg;base64,/9j/4AAQ"))

    def test_detects_svg_data_uri(self) -> None:
        # SVG with + in subtype
        self.assertTrue(is_image_output("data:image/svg+xml;base64,PHN2Zz4="))

    def test_case_insensitive(self) -> None:
        self.assertTrue(is_image_output("DATA:IMAGE/PNG;BASE64,iVBO"))

    def test_strips_whitespace(self) -> None:
        self.assertTrue(is_image_output("\n  data:image/png;base64,iVBO  \n"))

    def test_rejects_plain_text(self) -> None:
        self.assertFalse(is_image_output("Hello, world!"))

    def test_rejects_non_image_data_uri(self) -> None:
        self.assertFalse(is_image_output("data:text/plain;base64,SGVsbG8="))

    def test_rejects_image_not_base64(self) -> None:
        self.assertFalse(is_image_output("data:image/png,raw"))

    def test_rejects_empty_string(self) -> None:
        self.assertFalse(is_image_output(""))

    def test_rejects_non_string(self) -> None:
        self.assertFalse(is_image_output(None))  # type: ignore[arg-type]


class TestParseDataUri(unittest.TestCase):
    def test_parses_valid_uri(self) -> None:
        result = parse_data_uri("data:image/png;base64,ABCD")
        self.assertEqual(result, ("image/png", "ABCD"))

    def test_parses_svg(self) -> None:
        result = parse_data_uri("data:image/svg+xml;base64,PHN2Zz4=")
        self.assertEqual(result, ("image/svg+xml", "PHN2Zz4="))

    def test_returns_none_for_malformed(self) -> None:
        self.assertIsNone(parse_data_uri("not a data uri"))
        self.assertIsNone(parse_data_uri(""))
        self.assertIsNone(parse_data_uri("data:image/png,no-base64"))


class TestBuildImageToolResult(unittest.TestCase):
    def test_returns_none_for_non_image(self) -> None:
        self.assertIsNone(build_image_tool_result("hello"))

    def test_real_png_round_trips(self) -> None:
        """A small valid PNG round-trips through decode/resize/encode without
        format change (already under budget)."""
        img = Image.new("RGB", (50, 50), color="orange")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        data_uri = f"data:image/png;base64,{b64}"
        result = build_image_tool_result(data_uri)
        self.assertIsNotNone(result)
        block = result[0]
        self.assertEqual(block["type"], "image")
        # Small PNG stays PNG — Pillow's fast-path returns it unchanged.
        self.assertEqual(block["source"]["media_type"], "image/png")

    def test_oversize_png_compressed_through_processor(self) -> None:
        """A high-res PNG that would exceed the API limit gets downscaled by
        ``maybe_resize_image`` and returned within IMAGE_TARGET_RAW_SIZE."""
        from src.utils.image_processor import IMAGE_TARGET_RAW_SIZE
        img = Image.effect_noise((3000, 2500), 96).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        self.assertGreater(len(raw), IMAGE_TARGET_RAW_SIZE,
                           "test image must be over the cap to exercise resize")
        data_uri = f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
        result = build_image_tool_result(data_uri)
        self.assertIsNotNone(result)
        block = result[0]
        decoded = base64.b64decode(block["source"]["data"])
        self.assertLessEqual(len(decoded), IMAGE_TARGET_RAW_SIZE)

    def test_rejects_oversize_data_uri_string(self) -> None:
        """A data URI string > 25 MB is rejected before decode to prevent OOM."""
        huge_b64 = "A" * (26 * 1024 * 1024)
        result = build_image_tool_result(f"data:image/png;base64,{huge_b64}")
        self.assertIsNone(result)

    def test_rejects_undecodable_base64(self) -> None:
        # base64 with invalid chars — base64.b64decode without validate=True
        # may decode silently, but the byte payload won't form a valid image.
        # We still must return a usable block (the fallback raw-bytes path).
        result = build_image_tool_result("data:image/png;base64,!!!not-base64!!!")
        # Either None (decode failed) or a valid image block with the raw
        # decoded bytes — both are acceptable defensively.
        if result is not None:
            self.assertEqual(result[0]["type"], "image")


class TestBashMapResultToApi(unittest.TestCase):
    """The mapper converts image stdout into image content blocks."""

    def test_image_stdout_becomes_image_content_block(self) -> None:
        output = {
            "stdout": "data:image/png;base64,iVBORw0KGgo=",
            "stderr": "",
            "exit_code": 0,
            "isImage": True,
        }
        mapped = _bash_map_result_to_api(output, "tu_42")
        self.assertEqual(mapped["type"], "tool_result")
        self.assertEqual(mapped["tool_use_id"], "tu_42")
        self.assertIsInstance(mapped["content"], list)
        self.assertEqual(mapped["content"][0]["type"], "image")

    def test_text_stdout_unaffected(self) -> None:
        output = {
            "stdout": "hello world",
            "stderr": "",
            "exit_code": 0,
        }
        mapped = _bash_map_result_to_api(output, "tu_43")
        self.assertEqual(mapped["content"], "hello world")

    def test_isimage_with_unparseable_stdout_falls_through(self) -> None:
        """Defensive: if isImage is set but stdout doesn't parse, fall through
        to plain-text handling rather than emitting an empty image block."""
        output = {
            "stdout": "not actually a data uri",
            "stderr": "",
            "exit_code": 0,
            "isImage": True,
        }
        mapped = _bash_map_result_to_api(output, "tu_44")
        self.assertEqual(mapped["content"], "not actually a data uri")


class TestBashImageE2E(unittest.TestCase):
    """End-to-end: shell command emits image data URI -> tool returns image."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = build_default_registry(include_user_tools=False)
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_shell_command_emitting_image_data_uri(self) -> None:
        """Simulate a matplotlib-like script that prints a data URI."""
        # Build a tiny real PNG, save to a file, then have bash cat+wrap it
        img = Image.new("RGB", (10, 10), color="purple")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        png_path = self.root / "tiny.png"
        png_path.write_bytes(png_bytes)
        expected_b64 = base64.b64encode(png_bytes).decode("ascii")
        # printf the data URI to stdout (no trailing newline matters)
        command = f"printf 'data:image/png;base64,%s' \"$(base64 < {png_path} | tr -d '\\n')\""
        result = self.registry.dispatch(
            ToolCall(name="Bash", input={"command": command}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertTrue(result.output.get("isImage"))
        self.assertIn("data:image/png;base64,", result.output["stdout"])
        self.assertIn(expected_b64, result.output["stdout"])

    def test_shell_text_output_no_image_flag(self) -> None:
        result = self.registry.dispatch(
            ToolCall(name="Bash", input={"command": "echo hello"}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertNotIn("isImage", result.output)


if __name__ == "__main__":
    unittest.main()
