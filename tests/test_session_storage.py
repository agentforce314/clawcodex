"""Tests for R2-WS-7: Session storage."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.services.session_storage import (
    SessionMetadata,
    SessionStorage,
    LARGE_CONTENT_THRESHOLD,
)
from src.types.messages import (
    create_user_message,
    create_assistant_message,
    create_system_message,
)


class TestSessionMetadata:
    def test_to_dict_roundtrip(self):
        meta = SessionMetadata(
            session_id="test-123",
            model="claude-sonnet-4",
            cwd="/tmp",
            title="Test session",
        )
        d = meta.to_dict()
        restored = SessionMetadata.from_dict(d)
        assert restored.session_id == "test-123"
        assert restored.model == "claude-sonnet-4"
        assert restored.title == "Test session"

    def test_defaults(self):
        meta = SessionMetadata()
        assert meta.session_id != ""
        assert meta.start_time > 0
        assert meta.message_count == 0


class TestSessionStorage:
    def test_init_metadata(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        meta = storage.init_metadata(model="claude-sonnet-4", cwd="/test")
        assert meta.model == "claude-sonnet-4"
        assert meta.cwd == "/test"
        assert (storage.session_dir / "metadata.json").exists()

    def test_write_and_read_message(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()
        msg = create_user_message("hello world")
        storage.write_message(msg)
        storage.flush()

        entries = storage.read_transcript()
        assert len(entries) == 1
        assert entries[0]["content"] == "hello world"

    def test_write_multiple_messages(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()
        storage.write_message(create_user_message("q1"))
        storage.write_message(create_assistant_message("a1"))
        storage.write_message(create_user_message("q2"))
        storage.flush()

        entries = storage.read_transcript()
        assert len(entries) == 3

    def test_read_messages_as_typed(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()
        storage.write_message(create_user_message("hello"))
        storage.write_message(create_assistant_message("world"))
        storage.flush()

        messages = storage.read_messages()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_flush_updates_metadata_count(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()
        storage.write_message(create_user_message("a"))
        storage.write_message(create_user_message("b"))
        storage.flush()

        meta = storage.get_metadata()
        assert meta is not None
        assert meta.message_count == 2

    def test_update_metadata(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata(title="Old title")
        storage.update_metadata(title="New title", total_cost=0.50)

        meta = storage.get_metadata()
        assert meta is not None
        assert meta.title == "New title"
        assert meta.total_cost == 0.50

    def test_large_content_replacement(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()

        large_content = "x" * (LARGE_CONTENT_THRESHOLD + 100)
        msg_dict = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": large_content}
            ],
            "type": "user",
        }
        replaced = storage._replace_large_content(msg_dict)
        block = replaced["content"][0]
        assert "content stored:" in block["content"]
        assert "_content_ref" in block

        # Can load stored content
        ref_id = block["_content_ref"]
        loaded = storage.load_content(ref_id)
        assert loaded == large_content

    def test_small_content_not_replaced(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        msg_dict = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "short"}
            ],
        }
        replaced = storage._replace_large_content(msg_dict)
        assert replaced["content"][0]["content"] == "short"

    def test_write_raw(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()
        storage.write_raw({"custom": "data"})
        storage.flush()

        entries = storage.read_transcript()
        assert len(entries) == 1
        assert entries[0]["custom"] == "data"

    def test_delete_session(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        storage.init_metadata()
        storage.write_message(create_user_message("test"))
        storage.flush()
        assert storage.session_dir.exists()

        storage.delete()
        assert not storage.session_dir.exists()

    def test_read_empty_transcript(self, tmp_path):
        storage = SessionStorage(sessions_dir=tmp_path)
        entries = storage.read_transcript()
        assert entries == []


class TestSessionListing:
    def test_list_sessions(self, tmp_path):
        for i in range(3):
            s = SessionStorage(session_id=f"session-{i}", sessions_dir=tmp_path)
            s.init_metadata(title=f"Session {i}")

        sessions = SessionStorage.list_sessions(sessions_dir=tmp_path)
        assert len(sessions) == 3

    def test_list_sessions_sorted(self, tmp_path):
        for i in range(3):
            s = SessionStorage(session_id=f"session-{i}", sessions_dir=tmp_path)
            meta = s.init_metadata(title=f"Session {i}")
            # Make each newer than the last
            time.sleep(0.01)

        sessions = SessionStorage.list_sessions(sessions_dir=tmp_path)
        assert sessions[0].title == "Session 2"  # Most recent first

    def test_list_sessions_limit(self, tmp_path):
        for i in range(5):
            s = SessionStorage(session_id=f"session-{i}", sessions_dir=tmp_path)
            s.init_metadata(title=f"Session {i}")

        sessions = SessionStorage.list_sessions(sessions_dir=tmp_path, limit=2)
        assert len(sessions) == 2

    def test_list_sessions_empty(self, tmp_path):
        sessions = SessionStorage.list_sessions(sessions_dir=tmp_path)
        assert sessions == []


class TestSessionCleanup:
    def test_cleanup_old_sessions(self, tmp_path):
        # Create an "old" session
        s = SessionStorage(session_id="old-session", sessions_dir=tmp_path)
        meta = s.init_metadata(title="Old")
        # Hack metadata to be old
        meta_path = s.session_dir / "metadata.json"
        data = json.loads(meta_path.read_text())
        data["last_updated"] = time.time() - (40 * 86400)  # 40 days ago
        meta_path.write_text(json.dumps(data))

        # Create a "new" session
        s2 = SessionStorage(session_id="new-session", sessions_dir=tmp_path)
        s2.init_metadata(title="New")

        deleted = SessionStorage.cleanup_sessions(sessions_dir=tmp_path, retention_days=30)
        assert deleted == 1

        remaining = SessionStorage.list_sessions(sessions_dir=tmp_path)
        assert len(remaining) == 1
        assert remaining[0].title == "New"

    def test_cleanup_nothing_to_delete(self, tmp_path):
        s = SessionStorage(sessions_dir=tmp_path)
        s.init_metadata()
        deleted = SessionStorage.cleanup_sessions(sessions_dir=tmp_path, retention_days=30)
        assert deleted == 0
