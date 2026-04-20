"""Unit tests for :mod:`src.tui.widgets.transcript_view`.

Locks in two regressions found when using the TUI against a live provider:

1. Tool events with the kinds emitted by ``run_agent_loop`` (``tool_use``,
   ``tool_result``, ``tool_error``) must actually render. An earlier
   version matched ``tool_start`` / ``tool_end`` instead and silently
   dropped every tool event in the TUI.

2. Streaming chunks must not be flushed one-per-row. Before the Phase 1
   fix, every chunk was emitted as a separate row, so a stream of 30
   one-token chunks would produce 30 rows before ``append_assistant``
   ever got called — which is what broke markdown rendering in the TUI.

These tests exercise :class:`TranscriptView` through its Phase 0
compatibility alias :class:`Transcript` so old callers keep working
alongside the new widget layout.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from src.tui.app import ClawCodexTUI
from src.tui.widgets import Transcript
from src.tui.widgets.messages import (
    AssistantTextMessage,
    AssistantToolUseMessage,
    SystemMessage,
    ToolResultRow,
)


class _FakeProvider:
    provider_name = "fake"
    model = "fake-model"


def _make_app(tmp_path) -> ClawCodexTUI:
    from src.tool_system.context import ToolContext
    from src.tool_system.registry import ToolRegistry

    return ClawCodexTUI(
        provider=_FakeProvider(),
        provider_name="fake",
        workspace_root=tmp_path,
        tool_registry=ToolRegistry(),
        tool_context=ToolContext(workspace_root=tmp_path),
        stream=True,
    )


def _rows(t: Transcript) -> list:
    return list(t.children)


# ------------------------------------------------------------------
# Tool events
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_use_event_mounts_tool_use_row(tmp_path):
    """``tool_use`` must route to :class:`AssistantToolUseMessage`.

    Regression guard: a previous version matched ``tool_start`` instead
    of ``tool_use`` and silently dropped every tool event.
    """

    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        before = len(_rows(t))
        t.append_tool_event(
            kind="tool_use",
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            tool_output=None,
            is_error=False,
            error=None,
            tool_use_id="tu_1",
        )
        await pilot.pause()
        rows = _rows(t)
        assert len(rows) == before + 1
        row = rows[-1]
        assert isinstance(row, AssistantToolUseMessage)
        assert row.tool_name == "Bash"
        assert row.tool_input.get("command") == "ls -la"
        # ``mark_running`` is called inline; status must have advanced
        # past the initial "requested" state.
        assert row.status == "running"


@pytest.mark.asyncio
async def test_tool_result_updates_existing_row_in_place(tmp_path):
    """A matching ``tool_result`` must mutate the existing row, not mount a new one."""

    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        t.append_tool_event(
            kind="tool_use",
            tool_name="Bash",
            tool_input={"command": "echo hi"},
            tool_output=None,
            is_error=False,
            error=None,
            tool_use_id="tu_2",
        )
        await pilot.pause()
        after_use = len(_rows(t))
        t.append_tool_event(
            kind="tool_result",
            tool_name="Bash",
            tool_input=None,
            tool_output={"stdout": "hi", "exit_code": 0},
            is_error=False,
            error=None,
            tool_use_id="tu_2",
        )
        await pilot.pause()
        rows = _rows(t)
        # In-place update: no additional row.
        assert len(rows) == after_use
        row = rows[-1]
        assert isinstance(row, AssistantToolUseMessage)
        assert row.status == "done"


@pytest.mark.asyncio
async def test_tool_result_error_marks_row_as_error(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        t.append_tool_event(
            kind="tool_use",
            tool_name="Read",
            tool_input={"path": "/does/not/exist"},
            tool_output=None,
            is_error=False,
            error=None,
            tool_use_id="tu_3",
        )
        t.append_tool_event(
            kind="tool_result",
            tool_name="Read",
            tool_input=None,
            tool_output={"error": "file not found"},
            is_error=True,
            error=None,
            tool_use_id="tu_3",
        )
        await pilot.pause()
        row = _rows(t)[-1]
        assert isinstance(row, AssistantToolUseMessage)
        assert row.status == "error"


@pytest.mark.asyncio
async def test_tool_result_without_matching_use_renders_standalone(tmp_path):
    """Orphan ``tool_result`` events must surface as :class:`ToolResultRow`."""

    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        t.append_tool_event(
            kind="tool_result",
            tool_name="Bash",
            tool_input=None,
            tool_output={"stdout": "hello\nworld"},
            is_error=False,
            error=None,
            tool_use_id="orphan_1",
        )
        await pilot.pause()
        row = _rows(t)[-1]
        assert isinstance(row, ToolResultRow)


@pytest.mark.asyncio
async def test_tool_error_event_surfaces_dispatch_failure(tmp_path):
    """``tool_error`` (not ``tool_end``) must produce a visible row."""

    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        before = len(_rows(t))
        t.append_tool_event(
            kind="tool_error",
            tool_name="DoesNotExist",
            tool_input=None,
            tool_output=None,
            is_error=True,
            error="Unknown tool",
            tool_use_id="orphan_2",
        )
        await pilot.pause()
        rows = _rows(t)
        assert len(rows) == before + 1
        row = rows[-1]
        # Either a standalone result row (no matching ``tool_use``) or a
        # mutated tool-use row marked as error — both are acceptable.
        assert isinstance(row, (ToolResultRow, AssistantToolUseMessage))


# ------------------------------------------------------------------
# Streaming markdown
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamed_chunks_do_not_mount_per_chunk_rows(tmp_path):
    """Streaming chunks must reuse a single :class:`AssistantTextMessage` row.

    Regression guard: before the Phase 1 fix, every chunk was flushed
    as a separate row, preventing Markdown rendering at end-of-turn.
    """

    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        before = len(_rows(t))
        for piece in ["**", "Claw ", "Codex", "**", " is", " a", " CLI."]:
            t.append_assistant_chunk(piece)
        await pilot.pause()
        rows = _rows(t)
        # Exactly one extra row (the streaming assistant) — not one per chunk.
        assert len(rows) == before + 1
        active = rows[-1]
        assert isinstance(active, AssistantTextMessage)
        # All chunks were accumulated into the single streaming row.
        assert active.streaming_text == "**Claw Codex** is a CLI."


@pytest.mark.asyncio
async def test_append_assistant_finalises_streaming_row(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        for piece in ["**", "Claw Codex", "**", " is a CLI."]:
            t.append_assistant_chunk(piece)
        t.append_assistant("**Claw Codex** is a CLI.")
        await pilot.pause()
        rows = _rows(t)
        finalised = rows[-1]
        assert isinstance(finalised, AssistantTextMessage)
        # The final text was recorded and the row is no longer the
        # transcript's active streaming row (so future chunks would start
        # a new message).
        assert finalised._final_text == "**Claw Codex** is a CLI."  # noqa: SLF001
        assert t._active_assistant is None  # noqa: SLF001


# ------------------------------------------------------------------
# System rows
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_system_mounts_system_row(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        t = app.screen.query_one(Transcript)
        t.append_system("hello world", style="muted")
        await pilot.pause()
        row = _rows(t)[-1]
        assert isinstance(row, SystemMessage)
