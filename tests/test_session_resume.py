"""Tests for R2-WS-7: Session resume."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.session_resume import (
    ResumeResult,
    _fix_orphaned_tool_uses,
    _handle_snip_boundaries,
    resume_session,
)
from src.services.session_storage import SessionStorage
from src.types.content_blocks import ToolUseBlock, ToolResultBlock, TextBlock
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    create_assistant_message,
    create_user_message,
)


class TestResumeSession:
    def test_resume_basic(self, tmp_path):
        storage = SessionStorage(session_id="test-session", sessions_dir=tmp_path)
        storage.init_metadata(model="claude-sonnet-4", cwd="/test")
        storage.write_message(create_user_message("hello"))
        storage.write_message(create_assistant_message("world"))
        storage.flush()

        result = resume_session("test-session", sessions_dir=tmp_path)
        assert result.success is True
        assert result.message_count == 2
        assert result.metadata is not None
        assert result.metadata.model == "claude-sonnet-4"

    def test_resume_missing_session(self, tmp_path):
        result = resume_session("nonexistent", sessions_dir=tmp_path)
        assert result.success is False
        assert result.message_count == 0
        assert result.has_warnings is True

    def test_resume_with_malformed_lines(self, tmp_path):
        storage = SessionStorage(session_id="bad-session", sessions_dir=tmp_path)
        storage.init_metadata()

        # Write some valid and invalid lines
        transcript_path = storage.session_dir / "transcript.jsonl"
        storage.session_dir.mkdir(parents=True, exist_ok=True)
        with open(transcript_path, "w") as f:
            f.write(json.dumps({"role": "user", "content": "valid", "type": "user"}) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps({"role": "assistant", "content": [{"type": "text", "text": "ok"}], "type": "assistant"}) + "\n")

        result = resume_session("bad-session", sessions_dir=tmp_path)
        assert result.success is True
        # The malformed line is skipped at read_transcript level (warning logged)
        # and the valid entries are parsed


class TestFixOrphanedToolUses:
    def test_no_orphans(self):
        messages = [
            AssistantMessage(content=[
                ToolUseBlock(type="tool_use", id="t1", name="Read", input={}),
            ]),
            UserMessage(content=[
                ToolResultBlock(type="tool_result", tool_use_id="t1", content="ok"),
            ]),
        ]
        fixed, warnings = _fix_orphaned_tool_uses(messages)
        assert len(warnings) == 0
        assert len(fixed) == 2

    def test_orphaned_tool_use_gets_synthetic_result(self):
        messages = [
            AssistantMessage(content=[
                ToolUseBlock(type="tool_use", id="t1", name="Read", input={}),
            ]),
            # No tool_result for t1
        ]
        fixed, warnings = _fix_orphaned_tool_uses(messages)
        assert len(warnings) > 0
        assert "orphaned" in warnings[0].lower()
        # Should have added a synthetic result
        assert len(fixed) == 2
        synthetic = fixed[1]
        assert synthetic.role == "user"

    def test_multiple_orphans(self):
        messages = [
            AssistantMessage(content=[
                ToolUseBlock(type="tool_use", id="t1", name="Read", input={}),
                ToolUseBlock(type="tool_use", id="t2", name="Write", input={}),
            ]),
            UserMessage(content=[
                ToolResultBlock(type="tool_result", tool_use_id="t1", content="ok"),
            ]),
        ]
        fixed, warnings = _fix_orphaned_tool_uses(messages)
        assert len(warnings) > 0
        # t2 should get a synthetic result


class TestSnipBoundaries:
    def test_no_boundaries(self):
        messages = [
            create_user_message("a"),
            create_user_message("b"),
        ]
        result = _handle_snip_boundaries(messages)
        assert len(result) == 2

    def test_keeps_after_boundary(self):
        messages = [
            create_user_message("old1"),
            create_user_message("old2"),
            create_user_message("boundary", isCompactSummary=True),
            create_user_message("new1"),
            create_user_message("new2"),
        ]
        result = _handle_snip_boundaries(messages)
        assert len(result) == 3  # boundary + 2 new messages

    def test_last_boundary_wins(self):
        messages = [
            create_user_message("b1", isCompactSummary=True),
            create_user_message("mid"),
            create_user_message("b2", isCompactSummary=True),
            create_user_message("final"),
        ]
        result = _handle_snip_boundaries(messages)
        assert len(result) == 2  # b2 + final


class TestResumeResult:
    def test_properties(self):
        result = ResumeResult(
            messages=[create_user_message("a")],
            metadata=None,
            warnings=["warn"],
            success=True,
        )
        assert result.message_count == 1
        assert result.has_warnings is True

    def test_no_warnings(self):
        result = ResumeResult(
            messages=[],
            metadata=None,
            warnings=[],
            success=True,
        )
        assert result.has_warnings is False
