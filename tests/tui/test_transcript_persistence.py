"""Tests for Phase-8 transcript persistence wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.session_storage import SessionMetadata, SessionStorage
from src.types.messages import Message as TypedMessage


def test_session_storage_writes_user_and_assistant_round_trip(
    tmp_path: Path,
) -> None:
    """The bridge's persistence helpers do exactly this — write user +
    assistant messages and flush; ``read_messages`` recovers them."""

    storage = SessionStorage(session_id="test", sessions_dir=tmp_path)
    storage.init_metadata(model="x", cwd=str(tmp_path))
    storage.write_message(TypedMessage(role="user", content="hello"))
    storage.write_message(TypedMessage(role="assistant", content="hi back"))
    storage.flush()

    messages = storage.read_messages()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "hi back"


def test_session_storage_list_sessions_finds_persisted(tmp_path: Path) -> None:
    """After persistence, ``list_sessions`` enumerates the session."""

    storage = SessionStorage(session_id="t1", sessions_dir=tmp_path)
    storage.init_metadata(model="m", cwd=str(tmp_path), title="first")
    storage.write_message(TypedMessage(role="user", content="prompt"))
    storage.flush()

    sessions = SessionStorage.list_sessions(sessions_dir=tmp_path)
    assert len(sessions) == 1
    assert sessions[0].session_id == "t1"
    assert sessions[0].title == "first"


def test_resume_screen_reads_real_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring smoke: ``ResumeConversation._load_sessions`` returns
    populated entries when the storage has real files."""

    # Redirect SessionStorage's default sessions_dir to tmp_path.
    monkeypatch.setattr(
        "src.services.session_storage.SESSIONS_DIR", tmp_path
    )

    storage = SessionStorage(session_id="abc", sessions_dir=tmp_path)
    storage.init_metadata(model="m", cwd="/", title="hello session")
    storage.write_message(TypedMessage(role="user", content="hi"))
    storage.flush()

    from src.tui.screens.resume_conversation import ResumeConversation

    screen = ResumeConversation()
    sessions = screen._load_sessions()
    assert len(sessions) == 1
    session_id, label = sessions[0]
    assert session_id == "abc"
    # Label contains the title or session id.
    assert "hello" in label or "abc" == label


def test_resume_screen_empty_when_no_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "src.services.session_storage.SESSIONS_DIR", tmp_path
    )
    from src.tui.screens.resume_conversation import ResumeConversation

    assert ResumeConversation()._load_sessions() == []


def test_agent_bridge_persistence_helpers_handle_no_storage() -> None:
    """A bridge with ``self._storage=None`` (init failure) must still run."""

    from src.tui.agent_bridge import AgentBridge

    # Stub out the constructor's dependencies; we just need an instance
    # whose ``_persist_*`` paths run cleanly with ``_storage=None``.
    class _DummySession:
        session_id = "dummy"

        class _Conv:
            def add_user_message(self, _: str) -> None: pass

            def get_messages(self) -> list[Any]: return []
        conversation = _Conv()

    class _DummyState:
        def append_streaming_text(self, _: str) -> None: pass
        def set_thinking(self, *_a, **_k) -> None: pass
        def clear_streaming_text(self) -> None: pass
        def mark_tool_started(self, _: str) -> None: pass
        def mark_tool_finished(self, _: str) -> None: pass
        usage: dict[str, int] = {}

    class _Provider:
        model = "p"

    bridge = AgentBridge(
        post_message=lambda *_a, **_k: None,
        session=_DummySession(),
        provider=_Provider(),
        tool_registry=type("R", (), {"list_tools": lambda self: []})(),
        tool_context=type("C", (), {"workspace_root": Path("/tmp")})(),
        app_state=_DummyState(),
        run_worker=lambda *_a, **_k: None,
    )
    # Force the no-storage path.
    bridge._storage = None
    # These must not raise.
    bridge._persist_user_message("hello")
    bridge._persist_assistant_message("world")


def test_agent_bridge_persistence_skips_blank_messages(
    tmp_path: Path,
) -> None:
    """Empty / whitespace-only messages must NOT be persisted (avoids
    bloating the JSONL with blanks during silent turns)."""

    from src.tui.agent_bridge import AgentBridge

    class _DummySession:
        session_id = "blank-test"

        class _Conv:
            def add_user_message(self, _: str) -> None: pass
        conversation = _Conv()

    class _DummyState:
        usage: dict[str, int] = {}

    class _Provider:
        model = "p"

    bridge = AgentBridge.__new__(AgentBridge)
    bridge._storage = SessionStorage(
        session_id="blank-test", sessions_dir=tmp_path
    )
    bridge._storage.init_metadata(model="p", cwd=str(tmp_path))
    bridge._persist_user_message("")
    bridge._persist_user_message("   \n\n  ")
    bridge._persist_assistant_message("")
    msgs = bridge._storage.read_messages()
    assert msgs == []
