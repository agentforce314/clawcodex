"""Tests for the TUI command queue.

Parity target: ``components/PromptInput/PromptInputQueuedCommands.tsx`` +
``hooks/useCommandQueue`` — a prompt typed while a run is in flight is
*queued* for the next turn (shown in a dim preview above the input, not
the transcript) and drained one-per-turn (FIFO) when the run ends; ESC
discards the queue.

Three layers:
* ``format_queued_preview`` — the pure display formatter.
* ``AgentBridge`` — enqueue-under-lock FIFO + ``_finish`` posting
  ``QueuedPromptReady`` only when the queue is non-empty.
* ``REPLScreen`` via ``App.run_test`` — the end-to-end drain, the
  queued-not-transcript invariant, ESC clearing, and that slash / bash /
  memory inputs never enter the queue.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------
# 1. Pure formatter
# --------------------------------------------------------------------------
class TestFormatQueuedPreview:
    def _lines(self, prompts: list[str], width: int = 80) -> list[str]:
        from src.tui.widgets.queued_commands import format_queued_preview

        return format_queued_preview(prompts, width).plain.splitlines()

    def test_empty_queue_renders_nothing(self) -> None:
        from src.tui.widgets.queued_commands import format_queued_preview

        assert format_queued_preview([], 80).plain == ""

    def test_singular_header(self) -> None:
        lines = self._lines(["hello"])
        assert lines[0] == "1 message queued for next turn"
        assert lines[1] == "hello"

    def test_plural_header_preserves_order(self) -> None:
        lines = self._lines(["a", "b", "c"])
        assert lines[0] == "3 messages queued for next turn"
        assert lines[1:] == ["a", "b", "c"]

    def test_only_first_line_of_multiline_prompt(self) -> None:
        # A multi-line paste must not blow the footer up to many rows.
        lines = self._lines(["line1\nline2\nline3"])
        assert lines[1] == "line1"

    def test_internal_whitespace_collapsed(self) -> None:
        lines = self._lines(["  too    many   spaces  "])
        assert lines[1] == "too many spaces"

    def test_truncated_to_width_with_ellipsis(self) -> None:
        lines = self._lines(["x" * 100], width=20)
        assert lines[1] == "x" * 19 + "…"
        assert len(lines[1]) == 20

    def test_header_also_bounded_by_width(self) -> None:
        lines = self._lines(["hi"], width=10)
        assert len(lines[0]) == 10
        assert lines[0].endswith("…")

    def test_renderable_is_dim(self) -> None:
        from src.tui.widgets.queued_commands import format_queued_preview

        assert str(format_queued_preview(["hi"], 80).style) == "dim"


# --------------------------------------------------------------------------
# 2. Bridge enqueue + drain trigger
# --------------------------------------------------------------------------
class TestBridgeQueue:
    def _bridge(self, tmp_path: Path, monkeypatch):
        import src.services.session_storage as storage_mod
        from src.agent.session import Session
        from src.tool_system.context import ToolContext
        from src.tool_system.registry import ToolRegistry
        from src.tui.agent_bridge import AgentBridge
        from src.tui.state import AppState

        # Keep the persister off the developer's ~/.clawcodex/sessions.
        monkeypatch.setattr(storage_mod, "SESSIONS_DIR", tmp_path / "sessions")

        posted: list[object] = []
        session = Session.create("test", "test-model")
        bridge = AgentBridge(
            post_message=posted.append,
            session=session,
            provider=MagicMock(model="m"),
            tool_registry=ToolRegistry(),
            tool_context=ToolContext(workspace_root=tmp_path),
            app_state=AppState(),
            # Worker never actually runs → the run stays "in flight"
            # until the test calls _finish(), giving deterministic
            # control over the busy window.
            run_worker=lambda *a, **k: None,
        )
        return bridge, posted

    def test_idle_submit_starts_run(self, tmp_path, monkeypatch) -> None:
        bridge, _ = self._bridge(tmp_path, monkeypatch)
        assert bridge.submit("first") is True
        assert bridge.busy is True
        assert list(bridge._state.queued_prompts) == []

    def test_submit_while_busy_enqueues_fifo(self, tmp_path, monkeypatch) -> None:
        bridge, _ = self._bridge(tmp_path, monkeypatch)
        bridge.submit("first")  # starts a run → busy
        assert bridge.submit("second") is False
        assert bridge.submit("third") is False
        assert list(bridge._state.queued_prompts) == ["second", "third"]

    def test_finish_posts_ready_when_queue_nonempty(self, tmp_path, monkeypatch) -> None:
        from src.tui.messages import QueuedPromptReady

        bridge, posted = self._bridge(tmp_path, monkeypatch)
        bridge.submit("first")
        bridge.submit("second")  # queued
        posted.clear()
        bridge._finish()
        assert bridge.busy is False
        assert any(isinstance(m, QueuedPromptReady) for m in posted)

    def test_finish_silent_when_queue_empty(self, tmp_path, monkeypatch) -> None:
        from src.tui.messages import QueuedPromptReady

        bridge, posted = self._bridge(tmp_path, monkeypatch)
        bridge.submit("first")  # busy, nothing queued
        posted.clear()
        bridge._finish()
        assert not any(isinstance(m, QueuedPromptReady) for m in posted)


# --------------------------------------------------------------------------
# 3. End-to-end via the real REPL screen
# --------------------------------------------------------------------------
pytest.importorskip("textual")


class _FakeProvider:
    provider_name = "fake"
    model = "fake-model"


def _make_app(tmp_path: Path):
    from src.tui.app import ClawCodexTUI
    from src.tool_system.context import ToolContext
    from src.tool_system.registry import ToolRegistry

    return ClawCodexTUI(
        provider=_FakeProvider(),
        provider_name="fake",
        workspace_root=tmp_path,
        tool_registry=ToolRegistry(),
        tool_context=ToolContext(workspace_root=tmp_path),
        stream=False,
    )


def _spy_user_rows(screen, monkeypatch) -> list[str]:
    """Record every text appended to the transcript as a user row."""

    rows: list[str] = []
    orig = screen.transcript.append_user

    def _spy(text: str) -> None:
        rows.append(text)
        orig(text)

    monkeypatch.setattr(screen.transcript, "append_user", _spy)
    return rows


def _stick_busy(app) -> None:
    """Make submitted runs stay in flight until the test calls _finish()."""

    app._agent_bridge._run_worker = lambda *a, **k: None


async def _boot(tmp_path, monkeypatch):
    import src.services.session_storage as storage_mod
    from src.tui.history_store import HistoryStore

    monkeypatch.setattr(storage_mod, "SESSIONS_DIR", tmp_path / "sessions")
    app = _make_app(tmp_path)
    # Isolate prompt history off the real ~/.clawcodex/history.jsonl so
    # test prompts ("first", "second", …) never pollute the user's file.
    app.history_store = HistoryStore(tmp_path / "history.jsonl")
    return app


@pytest.mark.asyncio
async def test_prompt_while_busy_queues_without_transcript(tmp_path, monkeypatch):
    from src.tui.widgets.prompt_input import PromptSubmitted
    from src.tui.widgets.queued_commands import QueuedCommands

    app = await _boot(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _stick_busy(app)
        rows = _spy_user_rows(screen, monkeypatch)

        # Idle prompt → run starts, shown in the transcript.
        screen.post_message(PromptSubmitted(text="first"))
        await pilot.pause()
        assert app._agent_bridge.busy is True
        assert rows == ["first"]
        assert app.app_state.queued_prompts == []

        # Busy → the next prompt is queued, NOT shown in the transcript.
        screen.post_message(PromptSubmitted(text="second"))
        await pilot.pause()
        assert app.app_state.queued_prompts == ["second"]
        assert "second" not in rows
        assert screen.query_one(QueuedCommands).has_class("-has-queue")


@pytest.mark.asyncio
async def test_finish_drains_queue_fifo(tmp_path, monkeypatch):
    from src.tui.messages import AgentRunFinished
    from src.tui.widgets.prompt_input import PromptSubmitted
    from src.tui.widgets.queued_commands import QueuedCommands

    app = await _boot(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _stick_busy(app)
        rows = _spy_user_rows(screen, monkeypatch)

        for text in ("first", "second", "third"):
            screen.post_message(PromptSubmitted(text=text))
            await pilot.pause()
        # "first" ran; "second"/"third" queued in order.
        assert rows == ["first"]
        assert app.app_state.queued_prompts == ["second", "third"]

        async def _end_run() -> None:
            # Mirror the real worker order: AgentRunFinished is posted
            # before _finish() posts QueuedPromptReady — proving the
            # two-message ordering doesn't interfere with the drain.
            screen.post_message(
                AgentRunFinished(response_text="", num_turns=1, usage=None)
            )
            app._agent_bridge._finish()
            await pilot.pause()
            await pilot.pause()

        # End the first run → drain exactly one (the oldest).
        await _end_run()
        assert rows == ["first", "second"]
        assert app.app_state.queued_prompts == ["third"]

        # End the second run → drain the last; queue empties, widget hides.
        await _end_run()
        assert rows == ["first", "second", "third"]
        assert app.app_state.queued_prompts == []
        assert not screen.query_one(QueuedCommands).has_class("-has-queue")


@pytest.mark.asyncio
async def test_queued_prompt_recorded_to_history_once(tmp_path, monkeypatch):
    """A queued prompt is in input history once (typed), not twice (drain)."""
    from src.tui.widgets.prompt_input import PromptSubmitted

    app = await _boot(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _stick_busy(app)
        _spy_user_rows(screen, monkeypatch)

        screen.post_message(PromptSubmitted(text="first"))
        await pilot.pause()
        screen.post_message(PromptSubmitted(text="queued one"))
        await pilot.pause()
        app._agent_bridge._finish()
        await pilot.pause()
        await pilot.pause()

        recorded = [r.prompt for r in app.history_store.load() if r.prompt == "queued one"]
        assert recorded == ["queued one"]


@pytest.mark.asyncio
async def test_escape_while_busy_preserves_queue(tmp_path, monkeypatch):
    """TS handleCancel Priority 1: ESC cancels the run, queue is untouched."""
    from src.tui.messages import CancelRequested
    from src.tui.widgets.prompt_input import PromptSubmitted
    from src.tui.widgets.queued_commands import QueuedCommands

    app = await _boot(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _stick_busy(app)
        _spy_user_rows(screen, monkeypatch)

        screen.post_message(PromptSubmitted(text="first"))
        await pilot.pause()
        screen.post_message(PromptSubmitted(text="kept"))
        await pilot.pause()
        assert app.app_state.queued_prompts == ["kept"]
        assert app._agent_bridge.busy is True

        # ESC with a run in flight → cancel the run, leave the queue.
        app.post_message(CancelRequested())
        await pilot.pause()
        assert app.app_state.queued_prompts == ["kept"]
        assert screen.query_one(QueuedCommands).has_class("-has-queue")


@pytest.mark.asyncio
async def test_escape_while_idle_pops_queue_into_input(tmp_path, monkeypatch):
    """TS handleCancel Priority 2: ESC idle pops queued prompts into the input."""
    from src.tui.messages import CancelRequested
    from src.tui.widgets.prompt_input import PromptInput, PromptSubmitted
    from src.tui.widgets.queued_commands import QueuedCommands

    app = await _boot(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _stick_busy(app)
        _spy_user_rows(screen, monkeypatch)

        screen.post_message(PromptSubmitted(text="first"))
        await pilot.pause()
        screen.post_message(PromptSubmitted(text="queued one"))
        await pilot.pause()
        assert app.app_state.queued_prompts == ["queued one"]

        # Simulate the run ending without auto-draining, and a draft typed.
        app._agent_bridge._busy = False
        screen.query_one(PromptInput).set_value("draft")

        app.post_message(CancelRequested())
        await pilot.pause()
        # Queue drained back into the input (queued text then the draft),
        # not discarded; preview hidden.
        assert app.app_state.queued_prompts == []
        assert screen.query_one(PromptInput).current_text() == "queued one\ndraft"
        assert not screen.query_one(QueuedCommands).has_class("-has-queue")


@pytest.mark.asyncio
async def test_slash_bash_memory_never_enqueue(tmp_path, monkeypatch):
    from src.tui.widgets.prompt_input import PromptSubmitted

    app = await _boot(tmp_path, monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        _stick_busy(app)
        _spy_user_rows(screen, monkeypatch)

        # Isolate the routing: stub the prefix handlers so they don't run
        # bash / touch memory files / open dialogs.
        monkeypatch.setattr(app, "run_bash_mode", lambda *a, **k: None)
        monkeypatch.setattr(app, "run_memory_shortcut", lambda *a, **k: None)
        monkeypatch.setattr(app, "handle_local_slash_command", lambda *a, **k: True)

        # Start a run so the bridge is busy (a plain prompt WOULD queue).
        screen.post_message(PromptSubmitted(text="first"))
        await pilot.pause()
        assert app._agent_bridge.busy is True

        for prefixed in ("/help", "!ls", "#a note"):
            screen.post_message(PromptSubmitted(text=prefixed))
            await pilot.pause()
        assert app.app_state.queued_prompts == []

        # Control: a plain prompt still queues.
        screen.post_message(PromptSubmitted(text="plain"))
        await pilot.pause()
        assert app.app_state.queued_prompts == ["plain"]
