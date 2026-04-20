"""
Tests for post-compact file and plan attachments.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from src.services.compact.post_compact_attachments import (
    create_post_compact_file_attachments,
    create_plan_attachment_if_needed,
    create_skill_attachment_if_needed,
    SkillInfo,
    POST_COMPACT_MAX_FILES_TO_RESTORE,
    POST_COMPACT_TOKEN_BUDGET,
    POST_COMPACT_MAX_TOKENS_PER_FILE,
    _should_exclude_from_post_compact_restore,
    _collect_read_tool_file_paths,
    _truncate_to_tokens,
)
from src.types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock
from src.types.messages import UserMessage, AssistantMessage


class TestShouldExcludeFromPostCompactRestore(unittest.TestCase):
    def test_normal_file_not_excluded(self):
        self.assertFalse(_should_exclude_from_post_compact_restore("src/main.py"))

    def test_plan_file_excluded(self):
        self.assertTrue(
            _should_exclude_from_post_compact_restore(
                "/tmp/plan.md", plan_file_path="/tmp/plan.md"
            )
        )

    def test_claude_md_excluded(self):
        self.assertTrue(
            _should_exclude_from_post_compact_restore("CLAUDE.md")
        )
        self.assertTrue(
            _should_exclude_from_post_compact_restore(".claude.md")
        )

    def test_memory_path_excluded(self):
        paths = {os.path.abspath("/home/user/.claude/memory.md")}
        self.assertTrue(
            _should_exclude_from_post_compact_restore(
                "/home/user/.claude/memory.md", memory_paths=paths
            )
        )


class TestCollectReadToolFilePaths(unittest.TestCase):
    def test_collects_read_paths(self):
        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/tmp/foo.py"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id="t1", content="file contents"),
            ]),
        ]
        paths = _collect_read_tool_file_paths(msgs)
        self.assertIn(os.path.abspath("/tmp/foo.py"), paths)

    def test_skips_stub_results(self):
        msgs = [
            AssistantMessage(content=[
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/tmp/bar.py"}),
            ]),
            UserMessage(content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content="[File unchanged since last read]",
                ),
            ]),
        ]
        paths = _collect_read_tool_file_paths(msgs)
        self.assertEqual(len(paths), 0)

    def test_empty_messages(self):
        self.assertEqual(_collect_read_tool_file_paths([]), set())


class TestTruncateToTokens(unittest.TestCase):
    def test_short_content_unchanged(self):
        self.assertEqual(_truncate_to_tokens("hello", 100), "hello")

    def test_long_content_truncated(self):
        long_text = "x" * 10_000
        result = _truncate_to_tokens(long_text, 100)
        self.assertIn("truncated for compaction", result)
        self.assertLess(len(result), len(long_text))


class TestCreatePostCompactFileAttachments(unittest.TestCase):
    def test_empty_state_returns_empty(self):
        result = create_post_compact_file_attachments({})
        self.assertEqual(result, [])

    def test_creates_attachments_for_existing_files(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('hello')")
            f.flush()
            name = f.name

        try:
            state = {name: {"content": "print('hello')", "timestamp": time.time()}}
            result = create_post_compact_file_attachments(state)
            self.assertEqual(len(result), 1)
            self.assertIn(name, result[0].content)
        finally:
            os.unlink(name)

    def test_skips_nonexistent_files(self):
        state = {"/nonexistent/file.py": {"content": "x", "timestamp": time.time()}}
        result = create_post_compact_file_attachments(state)
        self.assertEqual(len(result), 0)

    def test_respects_max_files(self):
        files = []
        try:
            for i in range(10):
                f = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False
                )
                f.write(f"file {i}")
                f.close()
                files.append(f.name)

            state = {
                name: {"content": f"file {i}", "timestamp": time.time() + i}
                for i, name in enumerate(files)
            }
            result = create_post_compact_file_attachments(state, max_files=3)
            self.assertLessEqual(len(result), 3)
        finally:
            for name in files:
                os.unlink(name)

    def test_skips_preserved_read_paths(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("content")
            f.flush()
            name = f.name

        try:
            state = {name: {"content": "content", "timestamp": time.time()}}
            preserved = [
                AssistantMessage(content=[
                    ToolUseBlock(id="t1", name="Read", input={"file_path": name}),
                ]),
                UserMessage(content=[
                    ToolResultBlock(tool_use_id="t1", content="content"),
                ]),
            ]
            result = create_post_compact_file_attachments(
                state, preserved_messages=preserved
            )
            self.assertEqual(len(result), 0)
        finally:
            os.unlink(name)

    def test_orders_by_recency(self):
        files = []
        try:
            for i in range(3):
                f = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False
                )
                f.write(f"file_{i}_content")
                f.close()
                files.append(f.name)

            now = time.time()
            state = {
                files[0]: {"content": "old", "timestamp": now - 100},
                files[1]: {"content": "newest", "timestamp": now},
                files[2]: {"content": "mid", "timestamp": now - 50},
            }
            result = create_post_compact_file_attachments(state, max_files=2)
            self.assertEqual(len(result), 2)
            self.assertIn(files[1], result[0].content)
        finally:
            for name in files:
                os.unlink(name)


class TestCreatePlanAttachmentIfNeeded(unittest.TestCase):
    def test_no_path_returns_none(self):
        self.assertIsNone(create_plan_attachment_if_needed(None))

    def test_nonexistent_file_returns_none(self):
        self.assertIsNone(
            create_plan_attachment_if_needed("/nonexistent/plan.md")
        )

    def test_creates_attachment_for_existing_plan(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("# My Plan\n\n1. Step one\n2. Step two")
            f.flush()
            name = f.name

        try:
            result = create_plan_attachment_if_needed(name)
            self.assertIsNotNone(result)
            self.assertIn("My Plan", result.content)
        finally:
            os.unlink(name)

    def test_empty_plan_returns_none(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("   ")
            f.flush()
            name = f.name

        try:
            self.assertIsNone(create_plan_attachment_if_needed(name))
        finally:
            os.unlink(name)


class TestCreateSkillAttachmentIfNeeded(unittest.TestCase):
    def test_no_skills_returns_none(self):
        self.assertIsNone(create_skill_attachment_if_needed(None))
        self.assertIsNone(create_skill_attachment_if_needed([]))

    def test_creates_attachment_for_skills(self):
        skills = [
            SkillInfo(
                name="test_skill",
                path="/skills/test.py",
                content="# Skill content here",
                invoked_at=time.time(),
            )
        ]
        result = create_skill_attachment_if_needed(skills)
        self.assertIsNotNone(result)
        self.assertIn("test_skill", result.content)
        self.assertIn("Skill content here", result.content)

    def test_respects_token_budget(self):
        skills = [
            SkillInfo(
                name=f"skill_{i}",
                path=f"/skills/s{i}.py",
                content="x" * 50_000,
                invoked_at=time.time() - i,
            )
            for i in range(10)
        ]
        result = create_skill_attachment_if_needed(skills)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
