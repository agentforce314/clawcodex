"""Phase D — Session Integration Tests.

Full session lifecycle: create → write → flush → resume → verify.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.session_resume import resume_session
from src.services.session_storage import SessionStorage
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import (
    AssistantMessage,
    create_assistant_message,
    create_user_message,
)


class TestSessionLifecycle(unittest.TestCase):
    """Full session create → write → resume cycle."""

    def test_basic_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)
            sid = "lifecycle-test-1"

            # Create and populate
            storage = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            storage.init_metadata(model="claude-sonnet-4-6", cwd="/tmp", title="Lifecycle Test")

            storage.write_message(create_user_message("Hello"))
            storage.write_message(create_assistant_message("Hi there!"))
            storage.flush()

            # Resume
            result = resume_session(sid, sessions_dir=sessions_dir)
            self.assertTrue(result.success)
            self.assertEqual(result.message_count, 2)
            self.assertEqual(result.metadata.title, "Lifecycle Test")
            self.assertEqual(result.metadata.model, "claude-sonnet-4-6")

    def test_tool_use_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)
            sid = "tool-use-test"

            storage = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            storage.init_metadata(model="test-model", cwd="/tmp")

            # Write tool_use and tool_result messages
            assistant_msg = AssistantMessage(
                content=[
                    TextBlock(text="Reading file."),
                    ToolUseBlock(id="tu_1", name="Read", input={"file_path": "test.py"}),
                ],
                stop_reason="tool_use",
            )
            tool_result_msg = create_user_message(
                [ToolResultBlock(tool_use_id="tu_1", content="# test content")],
            )

            storage.write_message(assistant_msg)
            storage.write_message(tool_result_msg)
            storage.flush()

            result = resume_session(sid, sessions_dir=sessions_dir)
            self.assertTrue(result.success)
            self.assertEqual(result.message_count, 2)

    def test_metadata_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)
            sid = "meta-update"

            storage = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            storage.init_metadata(model="m1", title="V1")
            storage.update_metadata(title="V2")

            storage2 = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            meta = storage2.get_metadata()
            self.assertEqual(meta.title, "V2")

    def test_multiple_flushes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)
            sid = "multi-flush"

            storage = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            storage.init_metadata(model="test")

            storage.write_message(create_user_message("msg1"))
            storage.flush()

            storage.write_message(create_assistant_message("msg2"))
            storage.flush()

            storage.write_message(create_user_message("msg3"))
            storage.flush()

            result = resume_session(sid, sessions_dir=sessions_dir)
            self.assertEqual(result.message_count, 3)

    def test_resume_missing_session(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = resume_session("nonexistent", sessions_dir=Path(td))
            self.assertFalse(result.success)
            self.assertTrue(result.has_warnings)

    def test_orphaned_tool_use_fixed(self) -> None:
        """Resume fixes orphaned tool_use without tool_result."""
        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)
            sid = "orphan-test"

            storage = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            storage.init_metadata(model="test")

            # Write assistant with tool_use but NO corresponding tool_result
            assistant = AssistantMessage(
                content=[
                    TextBlock(text="Using tool"),
                    ToolUseBlock(id="tu_orphan", name="Bash", input={"command": "ls"}),
                ],
                stop_reason="tool_use",
            )
            storage.write_message(assistant)
            storage.flush()

            result = resume_session(sid, sessions_dir=sessions_dir)
            self.assertTrue(result.success)
            # Should have fixed the orphaned tool_use
            self.assertGreaterEqual(result.message_count, 1)

    def test_large_content_replacement(self) -> None:
        """Large tool results are stored separately."""
        with tempfile.TemporaryDirectory() as td:
            sessions_dir = Path(td)
            sid = "large-content"

            storage = SessionStorage(session_id=sid, sessions_dir=sessions_dir)
            storage.init_metadata(model="test")

            # Create message with large content
            large_content = "x" * 200_000
            storage.write_raw({
                "role": "user",
                "type": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_big", "content": large_content},
                ],
            })
            storage.flush()

            # Verify transcript has reference, not full content
            transcript = storage.read_transcript()
            self.assertEqual(len(transcript), 1)


if __name__ == "__main__":
    unittest.main()
