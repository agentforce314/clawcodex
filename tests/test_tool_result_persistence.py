"""Tests for src/services/tool_execution/tool_result_persistence.py.

Mirrors behaviors from typescript/src/utils/toolResultStorage.ts.
"""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.services.tool_execution.tool_result_persistence import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    PREVIEW_SIZE_BYTES,
    PersistedToolResult,
    PersistToolResultError,
    build_large_tool_result_message,
    generate_preview,
    get_persistence_threshold,
    is_persist_error,
    is_tool_result_content_empty,
    maybe_persist_large_tool_result,
    persist_tool_result,
    process_tool_result_block,
)


class TestGetPersistenceThreshold(unittest.TestCase):
    def test_declared_above_default_clamps_to_default(self) -> None:
        self.assertEqual(
            get_persistence_threshold("Bash", 100_000),
            DEFAULT_MAX_RESULT_SIZE_CHARS,
        )

    def test_declared_below_default_passes_through(self) -> None:
        self.assertEqual(get_persistence_threshold("MyTool", 5_000), 5_000)

    def test_infinity_passes_through(self) -> None:
        self.assertEqual(
            get_persistence_threshold("Read", math.inf),
            math.inf,
        )

    def test_default_value_matches_ts(self) -> None:
        # Parity assertion against TS's DEFAULT_MAX_RESULT_SIZE_CHARS.
        self.assertEqual(DEFAULT_MAX_RESULT_SIZE_CHARS, 50_000)


class TestIsToolResultContentEmpty(unittest.TestCase):
    def test_none(self) -> None:
        self.assertTrue(is_tool_result_content_empty(None))

    def test_empty_string(self) -> None:
        self.assertTrue(is_tool_result_content_empty(""))

    def test_whitespace_only(self) -> None:
        self.assertTrue(is_tool_result_content_empty("   \n\t "))

    def test_empty_list(self) -> None:
        self.assertTrue(is_tool_result_content_empty([]))

    def test_text_blocks_all_empty(self) -> None:
        self.assertTrue(is_tool_result_content_empty([
            {"type": "text", "text": ""},
            {"type": "text", "text": "  "},
        ]))

    def test_text_block_with_content_is_not_empty(self) -> None:
        self.assertFalse(is_tool_result_content_empty([
            {"type": "text", "text": "hello"},
        ]))

    def test_image_block_is_not_empty(self) -> None:
        self.assertFalse(is_tool_result_content_empty([
            {"type": "image", "source": {"data": "..."}},
        ]))

    def test_non_empty_string(self) -> None:
        self.assertFalse(is_tool_result_content_empty("output"))


class TestGeneratePreview(unittest.TestCase):
    def test_below_max_returns_unchanged(self) -> None:
        preview, has_more = generate_preview("hello", 100)
        self.assertEqual(preview, "hello")
        self.assertFalse(has_more)

    def test_above_max_truncates_with_has_more(self) -> None:
        content = "abcdefghij" * 100  # 1000 chars
        preview, has_more = generate_preview(content, 50)
        self.assertLessEqual(len(preview), 50)
        self.assertTrue(has_more)

    def test_truncates_at_newline_boundary_when_close(self) -> None:
        # Newline at byte 90 (within 50% .. max range)
        content = ("a" * 90) + "\n" + ("b" * 50)
        preview, has_more = generate_preview(content, 100)
        # Should cut at the newline, not at byte 100
        self.assertEqual(preview, "a" * 90)
        self.assertTrue(has_more)

    def test_falls_back_to_max_when_no_close_newline(self) -> None:
        content = ("a" * 200)
        preview, has_more = generate_preview(content, 100)
        self.assertEqual(len(preview), 100)
        self.assertTrue(has_more)


class TestPersistToolResult(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.results_dir = Path(self.tmpdir) / "tool-results"

    def test_persists_string_content(self) -> None:
        result = persist_tool_result(
            "hello world", "tool-use-1",
            tool_results_dir=self.results_dir,
        )
        self.assertIsInstance(result, PersistedToolResult)
        assert isinstance(result, PersistedToolResult)
        self.assertTrue(result.filepath.endswith("tool-use-1.txt"))
        self.assertEqual(Path(result.filepath).read_text(), "hello world")
        self.assertEqual(result.original_size, len("hello world"))
        self.assertFalse(result.is_json)
        self.assertFalse(result.has_more)
        self.assertEqual(result.preview, "hello world")

    def test_persists_text_block_list_as_json(self) -> None:
        content = [{"type": "text", "text": "block one"}]
        result = persist_tool_result(
            content, "tool-use-2",
            tool_results_dir=self.results_dir,
        )
        self.assertIsInstance(result, PersistedToolResult)
        assert isinstance(result, PersistedToolResult)
        self.assertTrue(result.filepath.endswith("tool-use-2.json"))
        self.assertTrue(result.is_json)
        loaded = json.loads(Path(result.filepath).read_text())
        self.assertEqual(loaded[0]["text"], "block one")

    def test_rejects_non_text_blocks(self) -> None:
        content = [{"type": "image", "source": {}}]
        result = persist_tool_result(
            content, "tool-use-3",
            tool_results_dir=self.results_dir,
        )
        self.assertIsInstance(result, PersistToolResultError)
        self.assertTrue(is_persist_error(result))

    def test_idempotent_on_existing_file(self) -> None:
        # First call writes the file.
        persist_tool_result(
            "first", "same-id",
            tool_results_dir=self.results_dir,
        )
        # Second call sees EEXIST and returns success without rewriting.
        result = persist_tool_result(
            "second", "same-id",
            tool_results_dir=self.results_dir,
        )
        self.assertIsInstance(result, PersistedToolResult)
        # File still has the original content.
        assert isinstance(result, PersistedToolResult)
        self.assertEqual(Path(result.filepath).read_text(), "first")


class TestBuildLargeToolResultMessage(unittest.TestCase):
    def test_message_has_persisted_output_wrapper(self) -> None:
        result = PersistedToolResult(
            filepath="/tmp/foo.txt",
            original_size=5000,
            is_json=False,
            preview="head of file",
            has_more=True,
        )
        msg = build_large_tool_result_message(result)
        self.assertTrue(msg.startswith(PERSISTED_OUTPUT_TAG))
        self.assertTrue(msg.endswith(PERSISTED_OUTPUT_CLOSING_TAG))
        self.assertIn("/tmp/foo.txt", msg)
        self.assertIn("head of file", msg)
        self.assertIn("...", msg)  # has_more indicator

    def test_message_omits_dots_when_no_more(self) -> None:
        result = PersistedToolResult(
            filepath="/tmp/foo.txt",
            original_size=5000,
            is_json=False,
            preview="head of file",
            has_more=False,
        )
        msg = build_large_tool_result_message(result)
        # has_more=False means no `...` separator before the closing tag
        # (the closing tag itself is the trailer).
        last_lines = msg.splitlines()[-3:]
        # Just check the trailer doesn't have a "..." before close
        self.assertNotIn("...", msg.split(PERSISTED_OUTPUT_CLOSING_TAG)[0].splitlines()[-1])


class TestMaybePersistLargeToolResult(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.results_dir = Path(self.tmpdir) / "tool-results"

    def _block(self, content: Any, tool_use_id: str = "abc") -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }

    def test_small_content_unchanged(self) -> None:
        block = self._block("hello")
        out = maybe_persist_large_tool_result(
            block, "MyTool",
            threshold=1000,
            tool_results_dir=self.results_dir,
        )
        self.assertEqual(out["content"], "hello")

    def test_empty_content_replaced_with_marker(self) -> None:
        block = self._block("")
        out = maybe_persist_large_tool_result(
            block, "MyTool",
            threshold=1000,
            tool_results_dir=self.results_dir,
        )
        self.assertEqual(out["content"], "(MyTool completed with no output)")

    def test_none_content_replaced_with_marker(self) -> None:
        block = self._block(None)
        out = maybe_persist_large_tool_result(
            block, "MyTool",
            threshold=1000,
            tool_results_dir=self.results_dir,
        )
        self.assertEqual(out["content"], "(MyTool completed with no output)")

    def test_image_block_unchanged(self) -> None:
        content = [{"type": "image", "source": {"data": "..."}}]
        block = self._block(content)
        out = maybe_persist_large_tool_result(
            block, "ScreenshotTool",
            threshold=10,  # absurdly small — would persist if not for image
            tool_results_dir=self.results_dir,
        )
        self.assertEqual(out["content"], content)

    def test_large_content_persisted_with_wrapper(self) -> None:
        big = "x" * 5000
        block = self._block(big, tool_use_id="big-id")
        out = maybe_persist_large_tool_result(
            block, "BashTool",
            threshold=1000,
            tool_results_dir=self.results_dir,
        )
        self.assertNotEqual(out["content"], big)
        msg: str = out["content"]
        self.assertIn(PERSISTED_OUTPUT_TAG, msg)
        self.assertIn(PERSISTED_OUTPUT_CLOSING_TAG, msg)
        self.assertIn("Output too large", msg)
        # File should exist on disk
        target = self.results_dir / "big-id.txt"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), big)

    def test_at_threshold_not_persisted(self) -> None:
        content = "y" * 100
        block = self._block(content)
        out = maybe_persist_large_tool_result(
            block, "MyTool",
            threshold=100,
            tool_results_dir=self.results_dir,
        )
        # `<=` threshold is unchanged
        self.assertEqual(out["content"], content)


class TestProcessToolResultBlock(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.results_dir = Path(self.tmpdir) / "tool-results"

    def test_routes_through_persistence(self) -> None:
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult

        big_output = "z" * 80_000

        def _call(_inp: dict[str, Any], _ctx: Any) -> ToolResult:
            return ToolResult(name="BigTool", output=big_output)

        tool = build_tool(
            name="BigTool",
            input_schema={"type": "object", "properties": {}},
            call=_call,
            max_result_size_chars=30_000,
        )
        out = process_tool_result_block(
            tool, big_output, "tu-1",
            tool_results_dir=self.results_dir,
        )
        self.assertIn(PERSISTED_OUTPUT_TAG, out["content"])
        # The threshold used should be min(30_000, 50_000) = 30_000
        self.assertTrue((self.results_dir / "tu-1.txt").exists())

    def test_below_threshold_unchanged(self) -> None:
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult

        small = "small output"

        def _call(_inp: dict[str, Any], _ctx: Any) -> ToolResult:
            return ToolResult(name="SmallTool", output=small)

        tool = build_tool(
            name="SmallTool",
            input_schema={"type": "object", "properties": {}},
            call=_call,
            max_result_size_chars=30_000,
        )
        out = process_tool_result_block(
            tool, small, "tu-2",
            tool_results_dir=self.results_dir,
        )
        self.assertEqual(out["content"], small)

    def test_empty_result_replaced_with_marker(self) -> None:
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult

        def _call(_inp: dict[str, Any], _ctx: Any) -> ToolResult:
            return ToolResult(name="QuietTool", output="")

        tool = build_tool(
            name="QuietTool",
            input_schema={"type": "object", "properties": {}},
            call=_call,
        )
        out = process_tool_result_block(
            tool, "", "tu-3",
            tool_results_dir=self.results_dir,
        )
        self.assertEqual(
            out["content"],
            "(QuietTool completed with no output)",
        )

    def test_infinity_opt_out_does_not_persist_large_output(self) -> None:
        """A tool that declares max_result_size_chars=float("inf") must
        never trigger persistence regardless of output size.

        Ports TS FileReadTool.ts ``maxResultSizeChars: Infinity`` rule:
        persisting Read output would create a circular Read loop
        because the model would Read the persisted file, hitting the
        same threshold. See ch06-tools.md "FileReadTool: The Versatile
        Reader".
        """
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult

        # 500K characters -- well above DEFAULT_MAX_RESULT_SIZE_CHARS (50K).
        # Without the Infinity opt-out, this would be persisted.
        huge_output = "X" * 500_000

        def _call(_inp: dict[str, Any], _ctx: Any) -> ToolResult:
            return ToolResult(name="ReadLike", output=huge_output)

        tool = build_tool(
            name="ReadLike",
            input_schema={"type": "object", "properties": {}},
            call=_call,
            max_result_size_chars=float("inf"),
        )
        out = process_tool_result_block(
            tool, huge_output, "tu-inf",
            tool_results_dir=self.results_dir,
        )
        # The persisted-output wrapper must NOT appear -- content passes through.
        self.assertNotIn(PERSISTED_OUTPUT_TAG, out["content"])
        self.assertEqual(out["content"], huge_output)
        # No file should have been written.
        self.assertFalse((self.results_dir / "tu-inf.txt").exists())

    def test_real_read_tool_has_infinity_max_chars(self) -> None:
        """Regression check: the production FileReadTool must declare
        ``max_result_size_chars == float("inf")`` so its output is never
        persisted (would cause circular Read loop)."""
        import math
        from src.tool_system.tools.read import ReadTool

        self.assertTrue(math.isinf(float(ReadTool.max_result_size_chars)))


if __name__ == "__main__":
    unittest.main()
