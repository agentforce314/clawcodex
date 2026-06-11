"""C3b tests: compact boundary, ctrl+o expand, read-group collapse, /thinking.

Tool RESULT events deliberately use the PRODUCTION shape — ``tool_name=""``
(agent_loop_compat.py builds result ToolEvents without a name) — the C3b
review's B1: name-carrying result events masked an inert collapse feature.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.widgets.transcript_view import (
    _READ_GROUP_MIN,
    TranscriptView,
)


class _Host(App):
    def compose(self) -> ComposeResult:
        yield TranscriptView()


def _rendered(pieces) -> str:
    """Plain-text rendering of snapshot pieces (Panels etc.)."""

    import io

    from rich.console import Console

    console = Console(record=True, width=120, file=io.StringIO())
    for piece in pieces:
        try:
            console.print(piece)
        except Exception:
            console.print(str(piece))
    return console.export_text()


def _result_event(view: TranscriptView, key: str, output: str = "ok"):
    # PRODUCTION SHAPE: tool_name is EMPTY on result events.
    view.append_tool_event(
        kind="tool_result",
        tool_name="",
        tool_input=None,
        tool_output=output,
        is_error=False,
        error=None,
        tool_use_id=key,
    )


def _use_event(view: TranscriptView, tool: str, key: str, tool_input=None):
    view.append_tool_event(
        kind="tool_use",
        tool_name=tool,
        tool_input=tool_input or {},
        tool_output=None,
        is_error=False,
        error=None,
        tool_use_id=key,
    )


@pytest.mark.asyncio
async def test_compact_boundary_row_and_snapshot() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        view.append_compact_boundary("Conversation compacted (saved 1234 tokens)")
        await pilot.pause()
        assert len(view.query(".compact-boundary")) == 1
        # Post-exit dump must include it (review M3).
        pieces = view.snapshot()
        assert "compacted" in _rendered(pieces)


@pytest.mark.asyncio
async def test_expand_last_empty_is_honest() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        view.expand_last()
        await pilot.pause()
        assert view.message_count == 1  # the "Nothing to expand." row


@pytest.mark.asyncio
async def test_truncated_result_is_expandable_with_real_name() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        long_output = "\n".join(f"line {i}" for i in range(60))
        _use_event(view, "Bash", "t1", {"command": "x"})
        _result_event(view, "t1", long_output)
        await pilot.pause()
        assert len(view._expandables) == 1
        label, full = view._expandables[-1]
        # Label derives from the ROW's name, not the empty event name (B1).
        assert label == "Bash result"
        assert "line 59" in full
        before = view.message_count
        view.expand_last()
        await pilot.pause()
        assert view.message_count == before + 1
        # Not popped: repeat re-prints (legacy REPL parity), and the
        # expanded panel participates in the snapshot (M3).
        view.expand_last()
        await pilot.pause()
        assert view.message_count == before + 2
        assert "line 59" in _rendered(view.snapshot())


@pytest.mark.asyncio
async def test_short_result_not_expandable() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        _use_event(view, "Bash", "t1", {"command": "x"})
        _result_event(view, "t1", "short")
        await pilot.pause()
        assert len(view._expandables) == 0


@pytest.mark.asyncio
async def test_read_group_collapses_with_production_events() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        for i in range(_READ_GROUP_MIN):
            key = f"r{i}"
            _use_event(view, "Read", key, {"file_path": f"/f{i}.py"})
            _result_event(view, key)
        await pilot.pause()
        groups = view.query(".read-group")
        assert len(groups) == 1
        from src.tui.widgets.messages import AssistantToolUseMessage

        assert len(view.query(AssistantToolUseMessage)) == 0
        label, full = view._expandables[-1]
        assert label == "collapsed reads/searches"
        assert "Read(/f0.py)" in full
        # The group row participates in the post-exit snapshot (M3).
        assert "reads/searches" in _rendered(view.snapshot())


@pytest.mark.asyncio
async def test_multi_fold_updates_single_stash_entry() -> None:
    """Folding N reads must not flood the ctrl+o deque (review M2) — with
    PRODUCTION-SIZED outputs, whose per-read content stashes interleave
    between folds (the residual the [-1]-only guard missed)."""

    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        n = _READ_GROUP_MIN + 5
        big = "x" * 2000  # > _BODY_MAX_CHARS → every read stashes content
        for i in range(n):
            key = f"r{i}"
            _use_event(view, "Read", key, {"file_path": f"/f{i}.py"})
            _result_event(view, key, big)
        await pilot.pause()
        entries = [e for e in view._expandables if e[0] == "collapsed reads/searches"]
        assert len(entries) == 1
        assert f"Read(/f{n-1}.py)" in entries[0][1]
        assert len(entries[0][1].splitlines()) == n
        # The per-read CONTENT entries survive alongside the single
        # collapsed entry (they are the removed rows' only remnant).
        content_entries = [e for e in view._expandables if e[0] == "Read result"]
        assert len(content_entries) == n


@pytest.mark.asyncio
async def test_read_group_breaks_on_thinking_and_nonread() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        view = app.query_one(TranscriptView)
        for i in range(2):
            key = f"r{i}"
            _use_event(view, "Read", key, {"file_path": f"/f{i}.py"})
            _result_event(view, key)
        view.append_thinking("pondering…")
        for i in range(2, 4):
            key = f"r{i}"
            _use_event(view, "Read", key, {"file_path": f"/f{i}.py"})
            _result_event(view, key)
        _use_event(view, "Bash", "b1", {"command": "x"})
        _result_event(view, "b1")
        _use_event(view, "Read", "r9", {"file_path": "/f9.py"})
        _result_event(view, "r9")
        await pilot.pause()
        # Three separate short runs — none reached the threshold.
        assert len(view.query(".read-group")) == 0


class TestThinkingToggle:
    def _fake_app(self, provider, current=None):
        from src.tui.state import AppState

        rows: list[tuple[str, str]] = []
        bridge = SimpleNamespace(extended_thinking=current, _provider=provider)
        fake = SimpleNamespace(
            _agent_bridge=bridge,
            app_state=AppState(),
        )
        transcript = SimpleNamespace(
            append_system=lambda text, style="muted": rows.append((style, text)),
            append_compact_boundary=lambda text: rows.append(("boundary", text)),
            append_user=lambda text: rows.append(("user", text)),
            clear_transcript=lambda: rows.append(("clear", "")),
        )
        return fake, bridge, transcript, rows

    def _apply(self, fake, transcript, **kwargs):
        from src.tui.app import ClawCodexTUI
        from src.tui.commands import CommandDispatchResult

        ClawCodexTUI._apply_command_result(
            fake, CommandDispatchResult(handled=True, **kwargs), transcript
        )

    def test_first_use_disables(self) -> None:
        fake, bridge, transcript, rows = self._fake_app(MagicMock())
        self._apply(fake, transcript, system_text="__thinking__")
        assert bridge.extended_thinking is False
        assert "disabled" in rows[-1][1]

    def test_enable_refused_on_unsupported_provider(self) -> None:
        fake, bridge, transcript, rows = self._fake_app(
            MagicMock(), current=False
        )
        self._apply(fake, transcript, system_text="__thinking__")
        assert bridge.extended_thinking is False  # unchanged
        assert "not supported" in rows[-1][1]

    def test_enable_allowed_on_supporting_anthropic_model(self) -> None:
        from src.providers.anthropic_provider import AnthropicProvider

        provider = MagicMock(spec=AnthropicProvider)
        provider.model = "claude-opus-4-7"
        fake, bridge, transcript, rows = self._fake_app(provider, current=False)
        self._apply(fake, transcript, system_text="__thinking__")
        assert bridge.extended_thinking is True
        assert "enabled" in rows[-1][1]

    def test_compact_result_renders_boundary(self) -> None:
        fake, bridge, transcript, rows = self._fake_app(MagicMock())
        self._apply(
            fake, transcript, system_text="compacted!", compact=True
        )
        assert rows[-1] == ("boundary", "compacted!")


class TestCompactResultPlumb:
    def test_engine_preserves_compact_type(self) -> None:
        import asyncio
        from pathlib import Path

        from src.command_system.engine import CommandEngine
        from src.command_system.types import (
            LocalCommand,
            LocalCommandResult,
        )

        class _FakeCompact(LocalCommand):
            async def call(self, args, context):
                return LocalCommandResult(type="compact", value="compacted!")

        cmd = _FakeCompact(name="compact", description="d")
        engine = CommandEngine(
            registry=MagicMock(),
            workspace_root=Path("."),
            context=MagicMock(),
        )
        result = asyncio.run(engine._execute_local(cmd, ""))
        assert result.result_type == "compact"
        assert result.text == "compacted!"
        assert result.success

    @pytest.mark.asyncio
    async def test_dispatch_maps_compact_flag(self, monkeypatch) -> None:
        from src.tui import commands as commands_mod

        async def _fake_execute(name, args, context):
            from src.command_system.engine import CommandResult

            return CommandResult(
                success=True,
                command_name="compact",
                result_type="compact",
                text="compacted!",
            )

        import src.command_system.builtins as builtins_mod

        monkeypatch.setattr(
            builtins_mod, "execute_command_async", _fake_execute
        )
        result = await commands_mod.dispatch_registry_command(
            "/compact", command_context=MagicMock()
        )
        assert result.handled and result.compact
        assert result.system_text == "compacted!"
