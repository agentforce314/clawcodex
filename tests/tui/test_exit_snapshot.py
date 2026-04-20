"""Tests for the post-exit transcript snapshot.

The Textual TUI runs in the alt-screen by default, which would
otherwise wipe the conversation the user saw as soon as it exits. To
match the TS ink reference's non-fullscreen behaviour, the app
captures the transcript into :attr:`ClawCodexTUI.exit_snapshot` on
the way out so entry points can replay it into the host's scrollback
buffer.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.widgets.messages import (
    AssistantTextMessage,
    AssistantToolUseMessage,
    SystemMessage,
    ToolResultRow,
    UserTextMessage,
)
from src.tui.widgets.transcript_view import Transcript


def _flatten(pieces) -> str:
    """Concatenate a snapshot into a plain string for assertion ease."""

    from rich.console import Console
    from io import StringIO

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    for piece in pieces:
        console.print(piece)
    return buf.getvalue()


def test_user_row_snapshot_includes_prompt_marker():
    row = UserTextMessage("hello world")
    text = str(row.snapshot())
    assert "❯" in text
    assert "hello world" in text


def test_assistant_streaming_snapshot_falls_back_to_plain():
    row = AssistantTextMessage()
    row.append_chunk("partial ")
    row.append_chunk("reply")
    snap = row.snapshot()
    # Streaming (non-finalised) snapshots return the raw text so we
    # don't try to parse a half-formed Markdown stream.
    rendered = _flatten([snap] if not isinstance(snap, tuple) else list(snap))
    assert "assistant" in rendered
    assert "partial reply" in rendered


def test_assistant_finalised_snapshot_uses_markdown():
    row = AssistantTextMessage()
    row.finalise("**done**")
    snap = row.snapshot()
    rendered = _flatten([snap] if not isinstance(snap, tuple) else list(snap))
    # Markdown rendering bolds the text but the literal word should
    # still appear — we just don't expect the ** markers.
    assert "done" in rendered
    assert "assistant" in rendered


def test_tool_use_snapshot_reports_status():
    row = AssistantToolUseMessage(
        tool_use_id="t1",
        tool_name="Bash",
        tool_input={"command": "ls"},
    )
    before = str(row.snapshot())
    assert "Bash" in before
    row.status = "done"
    after = str(row.snapshot())
    assert "Bash" in after


def test_tool_result_snapshot_includes_body():
    row = ToolResultRow(
        tool_name="Bash",
        summary="ls",
        body="file1\nfile2",
    )
    snap = row.snapshot()
    rendered = str(snap)
    assert "ls" in rendered
    assert "file1" in rendered
    assert "file2" in rendered


def test_system_snapshot_emits_plain_text():
    row = SystemMessage("boot completed", style="muted")
    snap = row.snapshot()
    assert "boot completed" in str(snap)


@pytest.mark.asyncio
async def test_transcript_snapshot_preserves_insertion_order():
    transcript = Transcript()

    class _App(App):
        def compose(self) -> ComposeResult:
            yield transcript

    async with _App().run_test() as pilot:
        await pilot.pause()
        transcript.append_user("first prompt")
        transcript.append_assistant("response one")
        transcript.append_tool_event(
            kind="tool_use",
            tool_name="Bash",
            tool_input={"command": "ls"},
            tool_output=None,
            is_error=False,
            error=None,
            tool_use_id="t1",
        )
        transcript.append_tool_event(
            kind="tool_result",
            tool_name="Bash",
            tool_input=None,
            tool_output="hi",
            is_error=False,
            error=None,
            tool_use_id="t1",
        )
        transcript.append_user("second prompt")
        transcript.append_system("bye", style="muted")
        await pilot.pause()

        pieces = transcript.snapshot()
        rendered = _flatten(pieces)
        # Order is preserved across the snapshot so scrollback reads
        # like the live view.
        assert rendered.find("first prompt") < rendered.find("response one")
        assert rendered.find("response one") < rendered.find("Bash")
        assert rendered.find("Bash") < rendered.find("second prompt")
        assert rendered.find("second prompt") < rendered.find("bye")


@pytest.mark.asyncio
async def test_app_exit_captures_transcript_snapshot():
    """``ClawCodexTUI.exit`` should populate ``exit_snapshot`` so the
    host entry-point can reprint the conversation after the alt-screen
    unwinds.
    """

    from src.tui.app import ClawCodexTUI

    class _StubProvider:
        model = "stub-model"
        completions = []  # noqa: RUF012

        def generate(self, *args, **kwargs):  # pragma: no cover - unused
            raise RuntimeError("provider should not be called in UI test")

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        app = ClawCodexTUI(
            provider=_StubProvider(),
            provider_name="stub",
            workspace_root=Path(tmp),
            max_turns=1,
            stream=False,
        )
        await _drive_exit_snapshot(app)

    rendered = _flatten(app.exit_snapshot)
    assert "user-prompt-one" in rendered
    assert "final answer" in rendered


async def _drive_exit_snapshot(app) -> None:
    """Populate the transcript and trigger an app exit."""

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._repl_screen is not None  # type: ignore[attr-defined]
        transcript = app._repl_screen.transcript  # type: ignore[attr-defined]
        transcript.append_user("user-prompt-one")
        transcript.append_assistant("final answer")
        await pilot.pause()
        app.exit()
        await pilot.pause()


@pytest.mark.asyncio
async def test_transcript_snapshot_ignores_rows_without_snapshot():
    transcript = Transcript()

    class _Weird:
        """A stand-in row without the snapshot protocol."""

    class _App(App):
        def compose(self) -> ComposeResult:
            yield transcript

    async with _App().run_test() as pilot:
        await pilot.pause()
        transcript.append_user("kept")
        # Mutate internal state to slip a non-snapshot-aware row past
        # the public API; :meth:`snapshot` must silently skip it.
        transcript._mounted_rows.append(_Weird())  # type: ignore[arg-type]
        await pilot.pause()
        rendered = _flatten(transcript.snapshot())
        assert "kept" in rendered
