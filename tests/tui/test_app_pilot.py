"""End-to-end pilot tests for the Claw Codex Textual TUI.

Uses Textual's :meth:`App.run_test` harness to drive the real UI under an
in-memory terminal emulator. We mock the provider so no network traffic is
required — the tests are fast (<2s) and hermetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("textual")

from src.tui.app import ClawCodexTUI
from src.tui.widgets import PromptInput, StartupHeader, StatusBar, Transcript
from src.tool_system.registry import ToolRegistry
from src.tool_system.context import ToolContext


class _FakeProvider:
    """Minimal provider stand-in for the agent loop.

    We bypass ``run_agent_loop`` entirely by monkeypatching
    :meth:`ClawCodexTUI._run_agent_in_thread` in individual tests; this class
    just needs to satisfy attribute lookups done at construction time.
    """

    provider_name = "fake"
    model = "fake-model"


def _make_app(tmp_path: Path) -> ClawCodexTUI:
    registry = ToolRegistry()
    tool_context = ToolContext(workspace_root=tmp_path)
    return ClawCodexTUI(
        provider=_FakeProvider(),
        provider_name="fake",
        workspace_root=tmp_path,
        tool_registry=registry,
        tool_context=tool_context,
        stream=False,
    )


@pytest.mark.asyncio
async def test_app_boots_with_all_core_widgets(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Every core widget the Phase 11 layout promises must be mounted on
        # the active screen (REPLScreen, pushed in ``on_mount``).
        screen = app.screen
        assert screen.query_one(StartupHeader) is not None
        assert screen.query_one(Transcript) is not None
        assert screen.query_one(StatusBar) is not None
        assert screen.query_one(PromptInput) is not None


@pytest.mark.asyncio
async def test_local_slash_help_is_handled_without_agent(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        ClawCodexTUI, "submit_to_agent", lambda self, text: calls.append(text)
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.screen.query_one(PromptInput)
        prompt._input.value = "/help"
        await prompt._input.action_submit()
        await pilot.pause()

    # /help is handled locally — the agent must NOT be invoked.
    assert calls == []


@pytest.mark.asyncio
async def test_prompt_submission_dispatches_to_agent(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        ClawCodexTUI, "submit_to_agent", lambda self, text: calls.append(text)
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.screen.query_one(PromptInput)
        prompt._input.value = "hello agent"
        await prompt._input.action_submit()
        await pilot.pause()

    assert calls == ["hello agent"]


@pytest.mark.asyncio
async def test_assistant_message_renders_into_transcript(tmp_path):
    from src.tui.messages import AssistantMessage

    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.screen.query_one(Transcript)
        before = len(transcript.lines)
        # Messages are routed to the *screen* (where the handler lives), not
        # the app. Posting to the app would leave ``on_assistant_message``
        # unreached because bubbling goes up the DOM, not down.
        app.screen.post_message(AssistantMessage(text="pong"))
        await pilot.pause()
        after = len(transcript.lines)
        assert after > before


@pytest.mark.asyncio
async def test_local_slash_exit_quits_app(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    monkeypatch.setattr(
        ClawCodexTUI, "submit_to_agent", lambda self, text: None
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.screen.query_one(PromptInput)
        prompt._input.value = "/exit"
        await prompt._input.action_submit()
        await pilot.pause()


@pytest.mark.asyncio
async def test_local_slash_repl_is_alias_for_exit(tmp_path, monkeypatch):
    """``/repl`` advertises intent ('return to the Rich REPL') but the
    mechanism is the same ``app.exit()`` as ``/exit`` / ``/quit``. When
    dispatched from a handoff (``/tui`` from the Rich REPL) this returns
    control to the outer loop; when booted via ``--tui`` it ends the
    process. This test locks in the exit semantics."""
    app = _make_app(tmp_path)
    monkeypatch.setattr(
        ClawCodexTUI, "submit_to_agent", lambda self, text: None
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.screen.query_one(PromptInput)
        prompt._input.value = "/repl"
        await prompt._input.action_submit()
        await pilot.pause()
    # If ``/repl`` hadn't called exit(), ``async with`` would block
    # forever and pytest would time out.

    # App.run_test exits cleanly when the app calls ``exit()``; if it hadn't,
    # ``async with`` would block forever and this test would time out.
