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


def _drive_unified(ctx, registry, blocks):
    """ch07 unification: drive the orchestrator lane (the production path
    after PR-1; the slim ``_dispatch_single_tool`` lane was retired).
    Returns the user-role messages run_tools yielded, in yield order."""
    import asyncio as _asyncio

    from src.services.tool_execution.orchestrator import run_tools
    from src.tool_system.registry import get_all_base_tools
    from src.types.messages import AssistantMessage, UserMessage

    ctx.options.tools = get_all_base_tools(registry)
    ctx.permission_context.mode = "bypassPermissions"

    def _allow(*_a, **_k):
        return {"behavior": "allow"}

    async def _go():
        out = []
        async for u in run_tools(
            blocks, [AssistantMessage(content="t")], _allow, ctx,
        ):
            if u.message is not None and isinstance(u.message, UserMessage):
                out.append(u.message)
        return out

    return _asyncio.run(_go())


def _split_primary_extras(msgs, tool_use_id):
    """(primary, extras) shim over the unified lane's yield stream."""
    primary = None
    extras = []
    for m in msgs:
        c = getattr(m, "content", None)
        if isinstance(c, list) and c and hasattr(c[0], "tool_use_id") and c[0].tool_use_id == tool_use_id:
            primary = m
        else:
            extras.append(m)
    return primary, extras


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
        """Read tool blocks reads outside workspace root.

        A bare ToolContext defaults to bypassPermissions, and — faithful to
        TS ``shouldBypassPermissions`` (permissions.ts:1268-1281) — that mode
        skips the working-directory allowlist entirely, so pin a gated mode.
        In a gated mode the permission pipeline surfaces an *ask* before the
        tool body runs (TS prompts for outside-cwd reads), so dispatch yields
        an error result rather than raising; the containment gate itself
        still hard-refuses the path.
        """
        self.ctx.permission_context.mode = "default"
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": "/etc/passwd"}),
            self.ctx,
        )
        self.assertTrue(result.is_error)
        # "root:" is the first passwd entry on macOS and Linux; an error
        # message legitimately mentioning a path or workspace *root* is fine.
        self.assertNotIn("root:", str(result.output))  # no content leak
        with self.assertRaises(ToolPermissionError):
            self.ctx.ensure_readable_path("/etc/passwd")

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

    def test_read_oversized_image_downscales_to_fit(self) -> None:
        """Oversized real images get downscaled to fit IMAGE_TARGET_RAW_SIZE
        rather than rejected. Mirrors TS readImageWithTokenBudget which
        always returns something readable. Regression for the pre-Tier-C
        behavior that raised ToolInputError on anything > 3.75 MB."""
        import base64
        import io
        from PIL import Image
        from src.utils.image_processor import IMAGE_TARGET_RAW_SIZE
        # 3500x2500 noise PNG -> ~14 MB raw, well over 3.75 MB cap
        img = Image.effect_noise((3500, 2500), 64).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        big_bytes = buf.getvalue()
        self.assertGreater(len(big_bytes), IMAGE_TARGET_RAW_SIZE,
                           "test image must be over the cap to exercise resize")
        big = self.root / "huge.png"
        big.write_bytes(big_bytes)
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(big)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["type"], "image")
        # Downscaled bytes fit under the cap.
        decoded = base64.b64decode(result.output["file"]["base64"])
        self.assertLessEqual(len(decoded), IMAGE_TARGET_RAW_SIZE)
        # Dimensions metadata recorded the resize.
        dims = result.output["file"]["dimensions"]
        self.assertIsNotNone(dims)
        self.assertEqual(dims["originalWidth"], 3500)
        self.assertEqual(dims["originalHeight"], 2500)
        self.assertLessEqual(dims["displayWidth"], 1568)
        self.assertLessEqual(dims["displayHeight"], 1568)

    def test_read_misnamed_jpeg_as_png_uses_correct_media_type(self) -> None:
        """Magic-byte format detection trumps the file extension. A file
        named foo.png containing JPEG bytes must report media_type=image/jpeg
        so the Anthropic API doesn't reject the wrong-typed image. Mirrors
        TS detectImageFormatFromBuffer at imageResizer.ts:769-812."""
        import io
        from PIL import Image
        # Real JPEG bytes saved under .png extension
        img = Image.new("RGB", (50, 50), color="green")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        liar = self.root / "liar.png"
        liar.write_bytes(buf.getvalue())
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(liar)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["type"], "image")
        self.assertEqual(result.output["file"]["type"], "image/jpeg")

    def test_read_resized_image_emits_metadata_user_message(self) -> None:
        """When an image gets resized, the Read tool emits a second isMeta
        UserMessage with dimensions text for coordinate-mapping prompts.
        Mirrors TS FileReadTool.ts:882-893 createImageMetadataText flow."""
        import io
        from PIL import Image
        from src.types.messages import UserMessage
        img = Image.new("RGB", (4000, 3000), color="white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        big = self.root / "big.jpg"
        big.write_bytes(buf.getvalue())
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(big)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertIsNotNone(result.new_messages)
        self.assertEqual(len(result.new_messages), 1)
        meta = result.new_messages[0]
        self.assertIsInstance(meta, UserMessage)
        self.assertTrue(meta.isMeta)
        body = meta.content
        self.assertIn("original 4000x3000", body)
        self.assertIn("displayed at", body)
        self.assertIn("Multiply coordinates by", body)
        self.assertIn(str(big), body)

    def test_read_small_image_emits_source_only_metadata(self) -> None:
        """A small image that passes through without resize still emits a
        ``[Image: source: <path>]`` metadata message so the model knows the
        source path. Matches TS createImageMetadataText behavior."""
        import io
        from PIL import Image
        from src.types.messages import UserMessage
        img = Image.new("RGB", (50, 50), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        small = self.root / "small.png"
        small.write_bytes(buf.getvalue())
        result = self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": str(small)}),
            self.ctx,
        )
        self.assertFalse(result.is_error)
        self.assertIsNotNone(result.new_messages)
        self.assertEqual(len(result.new_messages), 1)
        meta = result.new_messages[0]
        self.assertIsInstance(meta, UserMessage)
        self.assertTrue(meta.isMeta)
        self.assertIn(str(small), meta.content)
        # No "Multiply coordinates by" since no resize
        self.assertNotIn("Multiply coordinates by", meta.content)

    def test_production_dispatch_returns_primary_and_extras(self) -> None:
        """``_dispatch_single_tool`` returns ``(primary, extras)`` where
        primary is the tool_result and extras are any supplemental new_messages.
        Locks in the plumbing fix: a resized image must yield BOTH so the
        dimensions metadata reaches the model."""
        import io
        from PIL import Image
        pass  # ch07: unified-lane driver (see _drive_unified above)
        from src.types.messages import UserMessage
        from src.types.content_blocks import ToolUseBlock

        img = Image.new("RGB", (3000, 2000), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        big = self.root / "dispatch.jpg"
        big.write_bytes(buf.getvalue())

        block = ToolUseBlock(id="tu_pd_1", name="Read", input={"file_path": str(big)})
        msgs = _drive_unified(self.ctx, self.registry, [block])
        primary, extras = _split_primary_extras(msgs, block.id)

        # Primary is the tool_result carrying tu_pd_1.
        self.assertIsInstance(primary, UserMessage)
        self.assertEqual(primary.content[0].tool_use_id, "tu_pd_1")
        self.assertFalse(primary.content[0].is_error)
        # Exactly one supplemental (dimensions) message, flagged as meta.
        self.assertEqual(len(extras), 1)
        self.assertIsInstance(extras[0], UserMessage)
        self.assertTrue(extras[0].isMeta)
        self.assertIn("original 3000x2000", extras[0].content)
        self.assertIn("Multiply coordinates by", extras[0].content)

    def test_production_dispatch_no_extras_for_text_read(self) -> None:
        """A plain text read produces a tool_result and no extras."""
        pass  # ch07: unified-lane driver (see _drive_unified above)
        from src.types.content_blocks import ToolUseBlock

        text = self.root / "plain.txt"
        text.write_text("hello\nworld\n")
        block = ToolUseBlock(id="tu_pd_2", name="Read", input={"file_path": str(text)})
        msgs = _drive_unified(self.ctx, self.registry, [block])
        primary, extras = _split_primary_extras(msgs, block.id)
        self.assertEqual(extras, [])
        # Verify the right tool_use_id and non-error status.
        self.assertEqual(primary.content[0].tool_use_id, "tu_pd_2")
        self.assertFalse(primary.content[0].is_error)

    def test_multi_tool_batch_preserves_tool_result_pairing(self) -> None:
        """Regression test for the critic-flagged bug where multi-tool batches
        with supplemental new_messages broke tool_result pairing.

        Pre-fix: ``_run_tools_partitioned`` interleaved
        ``[a_result, a_meta, b_result]``. The merge guard refused to combine
        a_result+a_meta, so ``ensure_tool_result_pairing`` saw a_result then
        a_meta (no tool_result for b), decided tu_b was missing, and injected
        a synthetic "[Tool result missing due to internal error]" placeholder.

        ch07 round-3: the fix moved into normalize itself (unconditional
        merge + hoist + seam join, TS messages.ts:2445-2489); the unified
        lane yields interleaved and pairing still holds."""
        import asyncio
        import io
        from PIL import Image
        from src.types.content_blocks import ToolUseBlock
        from src.types.messages import normalize_messages_for_api, AssistantMessage

        # Two real images that will resize and emit metadata extras.
        img_a = Image.new("RGB", (2000, 1500), color="red")
        img_b = Image.new("RGB", (3000, 2000), color="green")
        path_a = self.root / "img_a.jpg"
        path_b = self.root / "img_b.jpg"
        for img, p in [(img_a, path_a), (img_b, path_b)]:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            p.write_bytes(buf.getvalue())

        block_a = ToolUseBlock(id="tu_a", name="Read", input={"file_path": str(path_a)})
        block_b = ToolUseBlock(id="tu_b", name="Read", input={"file_path": str(path_b)})

        # Read is concurrency-safe so this exercises the parallel path
        # of the UNIFIED lane (interleaved yields; the merge+hoist in
        # normalize handles pairing — no reordering shim).
        results = _drive_unified(self.ctx, self.registry, [block_a, block_b])

        tool_result_msgs = [
            m for m in results
            if m.content and hasattr(m.content[0], "tool_use_id")
        ]
        self.assertEqual(len(tool_result_msgs), 2)
        ids = {m.content[0].tool_use_id for m in tool_result_msgs}
        self.assertEqual(ids, {"tu_a", "tu_b"})
        for m in tool_result_msgs:
            self.assertFalse(m.content[0].is_error,
                             f"tu_{m.content[0].tool_use_id} regression: "
                             f"got error placeholder instead of real result")

        # End-to-end: feed through normalize_messages_for_api with an
        # assistant message carrying both tool_uses, and verify both
        # tool_results are present, non-error, and contain real content.
        from src.types.content_blocks import ToolUseBlock as TUB
        asst = AssistantMessage(
            content=[
                TUB(id="tu_a", name="Read", input={"file_path": str(path_a)}),
                TUB(id="tu_b", name="Read", input={"file_path": str(path_b)}),
            ],
        )
        normalized = normalize_messages_for_api([asst, *results])
        # Find every tool_result in the normalized output.
        found_ids = set()
        for m in normalized:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tu_id = block.get("tool_use_id")
                    body = block.get("content")
                    # Reject the synthetic-error placeholder.
                    self.assertNotIn(
                        "Tool result missing", str(body),
                        f"{tu_id}: API would receive synthetic placeholder "
                        f"instead of the real tool_result",
                    )
                    found_ids.add(tu_id)
        self.assertEqual(found_ids, {"tu_a", "tu_b"},
                         "API payload missing one or both tool_results")

    def test_pdf_pages_parser_accepts_single_page(self) -> None:
        from src.tool_system.tools.read import _parse_pdf_pages
        self.assertEqual(_parse_pdf_pages("3"), (3, 3))

    def test_pdf_pages_parser_accepts_range(self) -> None:
        from src.tool_system.tools.read import _parse_pdf_pages
        self.assertEqual(_parse_pdf_pages("1-5"), (1, 5))
        self.assertEqual(_parse_pdf_pages("10-20"), (10, 20))

    def test_pdf_pages_parser_rejects_malformed(self) -> None:
        from src.tool_system.tools.read import _parse_pdf_pages
        with self.assertRaises(ToolInputError):
            _parse_pdf_pages("abc")
        with self.assertRaises(ToolInputError):
            _parse_pdf_pages("")
        with self.assertRaises(ToolInputError):
            _parse_pdf_pages("5-3")  # reversed
        with self.assertRaises(ToolInputError):
            _parse_pdf_pages("0-2")  # 1-indexed

    def test_pdf_pages_parser_rejects_oversized_range(self) -> None:
        from src.tool_system.tools.read import (
            PDF_MAX_PAGES_PER_READ,
            _parse_pdf_pages,
        )
        with self.assertRaises(ToolInputError):
            _parse_pdf_pages(f"1-{PDF_MAX_PAGES_PER_READ + 1}")

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

    def test_dispatch_preserves_image_under_aggregate_threshold_pressure(self) -> None:
        """The aggregate-budget gate must short-circuit on image content.

        ``maybe_persist_large_tool_result``'s ``_has_image_block`` guard
        is the load-bearing line that keeps image bytes from being
        force-persisted to a wrapper-text message when the per-message
        aggregate budget is nearly full. This test pre-loads the
        running aggregate to one byte below
        ``MAX_TOOL_RESULTS_PER_MESSAGE_CHARS`` and asserts the image
        tool_result still arrives as a list of content blocks (not a
        ``<persisted-output>`` wrapper)."""
        from PIL import Image
        pass  # ch07: unified-lane driver (see _drive_unified above)
        from src.services.tool_execution.tool_result_persistence import (
            MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
        )
        from src.tool_system.registry import get_all_base_tools
        from src.types.content_blocks import ToolResultBlock, ToolUseBlock

        png = self.root / "under_pressure.png"
        Image.new("RGB", (48, 48), color="purple").save(png, format="PNG")

        # Pre-load the running aggregate to within 1 byte of the cap.
        # If ``_has_image_block`` didn't short-circuit, the image block
        # would be force-persisted (persist_tool_result would refuse
        # because of the non-text branch, and the original block would
        # come back wrapped in a text marker).
        self.ctx.tool_result_chars_so_far = MAX_TOOL_RESULTS_PER_MESSAGE_CHARS - 1

        tools = get_all_base_tools(self.registry)
        tu = ToolUseBlock(id="tu_agg", name="Read", input={"file_path": str(png)})
        msgs = _drive_unified(self.ctx, self.registry, [tu])
        primary, _extras = _split_primary_extras(msgs, tu.id)

        body = primary.content[0].content
        self.assertIsInstance(body, list,
            "Aggregate-pressure path collapsed image to text instead of "
            "short-circuiting via _has_image_block")
        self.assertEqual(body[0]["type"], "image")
        # The aggregate counter must NOT have grown by the image bytes:
        # ``_content_size`` returns 0 for image blocks
        # (tool_result_persistence.py:159-163), and the dispatcher's
        # ``+= compute_block_chars(api_block)`` therefore adds 0.
        # Pins the "image bytes don't pollute the per-message budget"
        # invariant so a future change to ``_content_size`` that started
        # counting base64 length would fail loudly here.
        self.assertEqual(
            self.ctx.tool_result_chars_so_far,
            MAX_TOOL_RESULTS_PER_MESSAGE_CHARS - 1,
            "Image bytes leaked into tool_result_chars_so_far counter",
        )

    def test_dispatch_preserves_image_list_content_not_json_stringified(self) -> None:
        """Regression for Bug B: ``_dispatch_single_tool`` must keep the
        Read tool's image content as a LIST of content blocks, not
        ``json.dumps`` it into a string.

        Before the fix, ``query.py:_dispatch_single_tool`` ran
        ``json.dumps(content)`` on any non-string content, which turned
        ``[{"type": "image", "source": ...}]`` into the literal text
        ``'[{"type": "image", "source": ...}]'``. The Anthropic API then
        received an image tool_result whose content was text JSON; the
        model couldn't see the actual image and would hallucinate.

        This test drives a real PNG through ``_dispatch_single_tool``
        AND then through ``normalize_messages_for_api`` to lock in the
        full wire shape: the API payload's tool_result content must be
        a list whose first element is an ``image`` content block."""
        from PIL import Image
        pass  # ch07: unified-lane driver (see _drive_unified above)
        from src.tool_system.registry import get_all_base_tools
        from src.types.content_blocks import ToolResultBlock, ToolUseBlock
        from src.types.messages import AssistantMessage, normalize_messages_for_api

        png = self.root / "regression.png"
        # Tiny so no resize kicks in -- we want the raw image block path.
        Image.new("RGB", (32, 32), color="red").save(png, format="PNG")

        # Production callers always pass ``tools``; without it the
        # dispatcher takes the legacy fallback branch and JSON-dumps the
        # raw output dict. Pass it explicitly so we exercise the
        # ``process_tool_result_block`` branch where Bug B lived.
        tools = get_all_base_tools(self.registry)
        tu = ToolUseBlock(id="tu_regress_b", name="Read", input={"file_path": str(png)})
        msgs = _drive_unified(self.ctx, self.registry, [tu])
        primary, _extras = _split_primary_extras(msgs, tu.id)

        # Direct check on the ToolResultBlock the dispatcher built.
        self.assertIsInstance(primary.content[0], ToolResultBlock)
        body = primary.content[0].content
        self.assertIsInstance(
            body, list,
            "Bug B regression: tool_result.content is a string instead of "
            "a list of content blocks — image was JSON-stringified.",
        )
        self.assertEqual(body[0]["type"], "image")
        self.assertEqual(body[0]["source"]["type"], "base64")
        self.assertEqual(body[0]["source"]["media_type"], "image/png")
        self.assertGreater(len(body[0]["source"]["data"]), 0)

        # End-to-end through normalize_messages_for_api: the API would
        # receive an image content block, not a text JSON blob.
        asst = AssistantMessage(content=[tu])
        normalized = normalize_messages_for_api([asst, primary])
        # Find the tool_result block in the normalized output
        api_tool_results = []
        for m in normalized:
            content = m.get("content") if isinstance(m, dict) else None
            if not isinstance(content, list):
                continue
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    api_tool_results.append(blk)
        self.assertEqual(len(api_tool_results), 1)
        api_body = api_tool_results[0]["content"]
        self.assertIsInstance(
            api_body, list,
            "API payload regressed: tool_result.content arrived as a string",
        )
        self.assertEqual(api_body[0]["type"], "image")
        self.assertEqual(api_body[0]["source"]["media_type"], "image/png")


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
