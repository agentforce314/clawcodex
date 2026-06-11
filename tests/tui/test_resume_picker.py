"""C2 resume-picker tests: entry filtering, screen selection, bridge swap."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from src.services.session_persistence import SessionPersister
from src.services.session_storage import SessionMetadata, SessionStorage
from src.services.session_listing import (
    ResumeEntry,
    build_resume_entries,
    filter_entries,
)
from src.tui.screens.resume_conversation import ResumeConversation


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    base = tmp_path / "sessions"
    import src.services.session_storage as storage_mod

    monkeypatch.setattr(storage_mod, "SESSIONS_DIR", base)
    return base


def _meta(session_id: str, count: int, title: str = "") -> SessionMetadata:
    return SessionMetadata(
        session_id=session_id, message_count=count, title=title
    )


class TestBuildResumeEntries:
    def test_filters_zero_count_and_counts_hidden(self) -> None:
        entries, hidden = build_resume_entries(
            [_meta("a", 3, "alpha"), _meta("b", 0, "ghost"), _meta("c", 1)]
        )
        assert [e.session_id for e in entries] == ["a", "c"]
        assert hidden == 1

    def test_excludes_current_session_silently(self) -> None:
        entries, hidden = build_resume_entries(
            [_meta("current", 5), _meta("other", 2)],
            exclude_session_id="current",
        )
        assert [e.session_id for e in entries] == ["other"]
        assert hidden == 0

    def test_label_prefers_title_then_id(self) -> None:
        titled = ResumeEntry(session_id="x", title="my work", message_count=2)
        untitled = ResumeEntry(session_id="y", title="", message_count=2)
        assert titled.label().startswith("my work")
        assert untitled.label().startswith("y")

    def test_duplicate_session_ids_deduped(self) -> None:
        # A duplicated id would crash Textual's OptionList (DuplicateID).
        entries, hidden = build_resume_entries(
            [_meta("dup", 3, "first"), _meta("dup", 5, "second"), _meta("z", 1)]
        )
        assert [e.session_id for e in entries] == ["dup", "z"]
        assert entries[0].title == "first"
        assert hidden == 0

    def test_filter_entries_matches_id_and_title(self) -> None:
        entries = [
            ResumeEntry(session_id="abc-123", title="fix parser", message_count=1),
            ResumeEntry(session_id="def-456", title="docs pass", message_count=1),
        ]
        assert [e.session_id for e in filter_entries(entries, "parser")] == [
            "abc-123"
        ]
        assert [e.session_id for e in filter_entries(entries, "def")] == ["def-456"]
        assert filter_entries(entries, "") == entries


class _Host(Screen):
    def compose(self) -> ComposeResult:
        yield Static("host")


class _DialogHost(App):
    def on_mount(self) -> None:
        self.push_screen(_Host())


@pytest.mark.asyncio
async def test_screen_selection_returns_session_id() -> None:
    import asyncio

    app = _DialogHost()
    async with app.run_test() as pilot:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        def _callback(result):
            if not future.done():
                future.set_result(result)

        app.push_screen(
            ResumeConversation(
                entries=[
                    ResumeEntry(session_id="s1", title="one", message_count=2),
                    ResumeEntry(session_id="s2", title="two", message_count=4),
                ],
                hidden_count=1,
            ),
            callback=_callback,
        )
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert await future == "s2"


@pytest.mark.asyncio
async def test_screen_escape_returns_none_and_footer_renders() -> None:
    import asyncio

    app = _DialogHost()
    async with app.run_test() as pilot:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        screen = ResumeConversation(
            entries=[ResumeEntry(session_id="s1", title="one", message_count=2)],
            hidden_count=3,
        )
        app.push_screen(screen, callback=lambda r: future.set_result(r))
        await pilot.pause()
        footers = screen.query(".-footer")
        assert len(footers) == 1
        await pilot.press("escape")
        await pilot.pause()
        assert await future is None


def _make_bridge(tmp_path):
    from src.agent.session import Session
    from src.tool_system.context import ToolContext
    from src.tool_system.registry import ToolRegistry
    from src.tui.agent_bridge import AgentBridge
    from src.tui.state import AppState

    session = Session.create("test", "test-model")
    bridge = AgentBridge(
        post_message=lambda _msg: None,
        session=session,
        provider=MagicMock(model="test-model"),
        tool_registry=ToolRegistry(),
        tool_context=ToolContext(workspace_root=tmp_path),
        app_state=AppState(),
        run_worker=lambda *a, **k: None,
    )
    return bridge, session


class TestBridgeResumeSession:
    def test_round_trip_swaps_conversation_and_persister(
        self, sessions_dir, tmp_path
    ) -> None:
        # Produce a stored session the way production does — including a
        # block-list assistant message (the shape the bridge persists).
        producer = SessionPersister("old-session", sessions_dir=sessions_dir)
        producer.start(model="m", cwd=str(tmp_path))
        producer.record_user("hello from the past")
        producer.record(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "past reply"}],
            }
        )
        producer.flush()
        # Give it a title the way /rename would.
        SessionStorage(
            session_id="old-session", sessions_dir=sessions_dir
        ).update_metadata(title="titled work")

        bridge, session = _make_bridge(tmp_path)
        session.conversation.add_user_message("current work")

        loaded = bridge.resume_session("old-session")
        assert loaded is not None
        assert len(session.conversation.messages) == len(loaded) == 2
        roles = [m.role for m in session.conversation.messages]
        assert roles == ["user", "assistant"]

        # Bootstrap id switched (TS switchSession path).
        from src.bootstrap.state import get_session_id

        assert str(get_session_id()) == "old-session"

        # Advisor scan cursor points at the END of the repopulated list —
        # index 0 would re-emit historical advisor events on the first
        # post-resume scan.
        assert bridge._last_scanned_msg_index == 2
        assert bridge._emitted_advisor_ids == set()

        # Persister re-targeted: the next user turn appends to the SAME
        # store, the title survives, and message_count increments from the
        # loaded value (no double counting).
        bridge._persister.record_user("new turn after resume")
        bridge._persister.flush()
        storage = SessionStorage(
            session_id="old-session", sessions_dir=sessions_dir
        )
        entries = storage.read_transcript()
        assert entries[-1]["role"] == "user"
        assert "new turn after resume" in str(entries[-1]["content"])
        meta = storage.get_metadata()
        assert meta is not None
        assert meta.title == "titled work"
        assert meta.message_count == 3

    def test_refuses_while_busy(self, sessions_dir, tmp_path) -> None:
        producer = SessionPersister("busy-target", sessions_dir=sessions_dir)
        producer.start(model="m", cwd=str(tmp_path))
        producer.record_user("x")
        producer.flush()

        bridge, _session = _make_bridge(tmp_path)
        bridge._busy = True
        assert bridge.resume_session("busy-target") is None

    def test_missing_session_returns_none(self, sessions_dir, tmp_path) -> None:
        bridge, session = _make_bridge(tmp_path)
        before = list(session.conversation.messages)
        assert bridge.resume_session("does-not-exist") is None
        assert session.conversation.messages == before


class TestRenderResumedMessages:
    """The replay renderer must handle DATACLASS content blocks — the
    resume reader yields TextBlock et al., not dicts (C2 review B1)."""

    def _render(self, messages):
        from src.tui.app import ClawCodexTUI

        rows: list[tuple[str, str]] = []

        class _FakeTranscript:
            def clear_transcript(self):
                rows.append(("clear", ""))

            def append_user(self, text):
                rows.append(("user", text))

            def append_assistant(self, text):
                rows.append(("assistant", text))

            def append_system(self, text, style="muted"):
                rows.append(("system", text))

        ClawCodexTUI._render_resumed_messages(
            MagicMock(), _FakeTranscript(), "sid", messages
        )
        return rows

    def test_dataclass_text_blocks_render(self, sessions_dir, tmp_path) -> None:
        # Produce + read through the PRODUCTION pipeline so blocks arrive
        # in whatever shape the reader actually yields.
        from src.services.session_resume import resume_session as read_back

        producer = SessionPersister("render-me", sessions_dir=sessions_dir)
        producer.start(model="m", cwd=str(tmp_path))
        producer.record_user("the question")
        producer.record(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "the answer"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            }
        )
        producer.flush()
        result = read_back("render-me")
        assert result.success

        rows = self._render(result.messages)
        kinds = [k for k, _ in rows]
        assert kinds[0] == "clear"
        assert ("user", "the question") in rows
        assert ("assistant", "the answer") in rows, (
            "dataclass TextBlock content must render"
        )
        note = rows[-1][1]
        assert "Resumed session sid" in note
        # tool_use (and its synthetic orphan-repair tool_result) counted,
        # never silently dropped.
        assert "not re-rendered" in note


class TestResumeRegistryCommand:
    @pytest.mark.asyncio
    async def test_lists_sessions_headless(self, sessions_dir, tmp_path) -> None:
        producer = SessionPersister("listed", sessions_dir=sessions_dir)
        producer.start(model="m", cwd=str(tmp_path))
        producer.record_user("x")
        producer.flush()
        # update message_count metadata
        ghost = SessionStorage(session_id="ghost", sessions_dir=sessions_dir)
        ghost.init_metadata(title="ghost-entry")

        from src.command_system.resume_command import RESUME_COMMAND

        outcome = await RESUME_COMMAND.run("", MagicMock())
        assert "listed" in outcome.message
        assert "metadata-only" in outcome.message
        assert "TUI" in outcome.message

    @pytest.mark.asyncio
    async def test_empty_store_is_honest(self, sessions_dir) -> None:
        from src.command_system.resume_command import RESUME_COMMAND

        outcome = await RESUME_COMMAND.run("", MagicMock())
        assert "No resumable conversations" in outcome.message
