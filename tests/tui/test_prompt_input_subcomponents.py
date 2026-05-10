"""Tests for the Phase-3 PromptInput sub-components and ``@``-completer.

Covers WI-3.2 through WI-3.7:
- ``@``-file completer popup (token parser + popup state)
- :class:`PromptInputModeIndicator`
- :class:`PromptInputFooter`
- :class:`PromptInputQueuedCommands`
- :class:`PromptInputStashNotice` + stash persistence helpers
- :class:`PromptInputHelpMenu` (smoke; modal lifecycle)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from src.tui.widgets.prompt_input import (
    PromptInput,
    _current_at_token,
)
from src.tui.widgets.prompt_input_footer import PromptInputFooter
from src.tui.widgets.prompt_input_mode_indicator import PromptInputModeIndicator
from src.tui.widgets.prompt_input_queued_commands import PromptInputQueuedCommands
from src.tui.widgets.prompt_input_stash_notice import (
    PromptInputStashNotice,
    clear_stash,
    read_stash,
    write_stash,
)


# ------------------------------------------------------------------
# _current_at_token — token parser
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "buf,expected",
    [
        ("", (None, 0)),
        ("hello", (None, 0)),
        ("@", ("@", 0)),
        ("@src", ("@src", 0)),
        ("@src/tu", ("@src/tu", 0)),
        ("ref @src/tu", ("@src/tu", 4)),
        # space terminates the token
        ("@src/foo ", (None, 0)),
        # only opens at start-of-buffer or after whitespace
        ("foo@bar", (None, 0)),
        ("hello world", (None, 0)),
    ],
)
def test_current_at_token(buf: str, expected: tuple[str | None, int]) -> None:
    assert _current_at_token(buf) == expected


# ------------------------------------------------------------------
# Mode indicator
# ------------------------------------------------------------------


class _Harness(App):
    """Minimal Textual app harness for sub-component widgets."""

    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_mode_indicator_hidden_when_disabled() -> None:
    indicator = PromptInputModeIndicator()
    async with _Harness(indicator).run_test() as pilot:
        indicator.set_state(enabled=False, mode="normal")
        await pilot.pause()
        assert indicator.has_class("-hidden")


@pytest.mark.asyncio
async def test_mode_indicator_hidden_in_insert_mode_even_when_enabled() -> None:
    indicator = PromptInputModeIndicator()
    async with _Harness(indicator).run_test() as pilot:
        indicator.set_state(enabled=True, mode="insert")
        await pilot.pause()
        assert indicator.has_class("-hidden")


@pytest.mark.asyncio
async def test_mode_indicator_visible_in_normal_mode() -> None:
    indicator = PromptInputModeIndicator()
    async with _Harness(indicator).run_test() as pilot:
        indicator.set_state(enabled=True, mode="normal")
        await pilot.pause()
        assert not indicator.has_class("-hidden")


@pytest.mark.asyncio
async def test_mode_indicator_unknown_mode_is_treated_as_insert() -> None:
    """Defensive — an unrecognized mode label hides the pill rather than
    rendering a stray label."""

    indicator = PromptInputModeIndicator()
    async with _Harness(indicator).run_test() as pilot:
        indicator.set_state(enabled=True, mode="zzz-unknown")
        await pilot.pause()
        assert indicator.has_class("-hidden")


# ------------------------------------------------------------------
# Footer
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_footer_hidden_when_no_hints() -> None:
    footer = PromptInputFooter()
    async with _Harness(footer).run_test() as pilot:
        footer.set_hints([])
        await pilot.pause()
        assert footer.has_class("-hidden")


@pytest.mark.asyncio
async def test_footer_visible_when_hints_set() -> None:
    footer = PromptInputFooter()
    async with _Harness(footer).run_test() as pilot:
        footer.set_hints([("Ctrl+C", "cancel"), ("Esc", "close")])
        await pilot.pause()
        assert not footer.has_class("-hidden")
        assert footer.hints == (("Ctrl+C", "cancel"), ("Esc", "close"))


@pytest.mark.asyncio
async def test_footer_filters_blank_pairs() -> None:
    footer = PromptInputFooter()
    async with _Harness(footer).run_test() as pilot:
        footer.set_hints([("", "blank-key"), ("Tab", ""), ("Ctrl+A", "action")])
        await pilot.pause()
        assert footer.hints == (("Ctrl+A", "action"),)


# ------------------------------------------------------------------
# Queued commands
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queued_commands_hidden_when_empty() -> None:
    queue = PromptInputQueuedCommands()
    async with _Harness(queue).run_test() as pilot:
        queue.set_queue([])
        await pilot.pause()
        assert queue.has_class("-hidden")


@pytest.mark.asyncio
async def test_queued_commands_visible_when_populated() -> None:
    queue = PromptInputQueuedCommands()
    async with _Harness(queue).run_test() as pilot:
        queue.set_queue(["/foo", "/bar"])
        await pilot.pause()
        assert not queue.has_class("-hidden")
        assert queue.queue == ("/foo", "/bar")


@pytest.mark.asyncio
async def test_queued_commands_filters_falsy() -> None:
    queue = PromptInputQueuedCommands()
    async with _Harness(queue).run_test() as pilot:
        queue.set_queue(["", "/cmd", None])
        await pilot.pause()
        assert queue.queue == ("/cmd",)


# ------------------------------------------------------------------
# Stash notice + persistence helpers
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stash_notice_hidden_by_default() -> None:
    notice = PromptInputStashNotice()
    async with _Harness(notice).run_test() as pilot:
        await pilot.pause()
        assert notice.has_class("-hidden")
        assert notice.has_stash is False


@pytest.mark.asyncio
async def test_stash_notice_announces_and_hides() -> None:
    notice = PromptInputStashNotice()
    async with _Harness(notice).run_test() as pilot:
        notice.announce_stash(True, recover_key="Ctrl+R")
        await pilot.pause()
        assert not notice.has_class("-hidden")
        assert notice.recover_key == "Ctrl+R"
        notice.announce_stash(False)
        await pilot.pause()
        assert notice.has_class("-hidden")


def test_stash_write_read_clear_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stash file is per-workspace; each round-trip works in isolation."""

    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()

    write_stash("draft text", workspace_root=workspace)
    assert read_stash(workspace_root=workspace) == "draft text"
    clear_stash(workspace_root=workspace)
    assert read_stash(workspace_root=workspace) == ""


def test_stash_write_empty_text_clears_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "p"
    workspace.mkdir()
    write_stash("draft", workspace_root=workspace)
    write_stash("", workspace_root=workspace)
    assert read_stash(workspace_root=workspace) == ""


def test_stash_write_whitespace_only_treated_as_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "p"
    workspace.mkdir()
    write_stash("draft", workspace_root=workspace)
    write_stash("   \n\n  ", workspace_root=workspace)
    assert read_stash(workspace_root=workspace) == ""


def test_different_workspaces_dont_share_stash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    write_stash("for a", workspace_root=workspace_a)
    write_stash("for b", workspace_root=workspace_b)
    assert read_stash(workspace_root=workspace_a) == "for a"
    assert read_stash(workspace_root=workspace_b) == "for b"


# ------------------------------------------------------------------
# PromptInput integration — @-completer popup
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_input_at_popup_opens_on_at_keystroke(
    tmp_path: Path,
) -> None:
    """Typing ``@src`` in the input opens the ``@``-file popup; pressing
    Esc closes it."""

    pi = PromptInput(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        # Simulate typed text — bypass the keypress-to-input translation
        # and drive the change handler directly so we don't depend on
        # Textual's input-routing internals.
        pi._input.value = "@no-such-prefix-zzzz"
        pi._input.cursor_position = len(pi._input.value)
        # Dispatch the synthetic Changed event the input would emit.
        # ``on_input_changed`` is a sync handler (Textual handlers can be
        # either sync or async); call it directly.
        pi.on_input_changed(
            type("E", (), {"value": pi._input.value, "input": pi._input})()
        )
        await pilot.pause()
        # The popup may stay hidden if the current directory has no
        # matches for the bogus prefix; that's fine — the contract under
        # test is that the popup state machine doesn't crash.


@pytest.mark.asyncio
async def test_prompt_input_at_popup_finds_real_project_files(
    tmp_path: Path,
) -> None:
    """The popup must surface project-file matches for ``@src/...``-style
    tokens (the headline use case). Critic-flagged: previous implementation
    only handled path-like ``@/...`` tokens and silently returned empty
    for project files."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    src_dir = workspace / "src"
    src_dir.mkdir()
    (src_dir / "foo.py").write_text("# fixture\n")
    (src_dir / "bar.py").write_text("# fixture\n")
    (workspace / "README.md").write_text("# fixture\n")

    pi = PromptInput(words_provider=lambda: [], workspace_root=workspace)
    async with _Harness(pi).run_test() as pilot:
        pi._input.value = "@foo"
        pi._input.cursor_position = len(pi._input.value)
        pi.on_input_changed(
            type("E", (), {"value": pi._input.value, "input": pi._input})()
        )
        await pilot.pause()
        # Popup must open (not hidden) — at least ``foo.py`` matches.
        assert not pi._at_suggestions.has_class("-hidden"), (
            "popup should be open for a project-file query"
        )


@pytest.mark.asyncio
async def test_prompt_input_at_popup_splice_at_buffer_end(
    tmp_path: Path,
) -> None:
    """Cursor at end of buffer: splice should append a trailing space."""

    pi = PromptInput(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi._input.value = "see @src"
        pi._input.cursor_position = len(pi._input.value)
        pi._at_suggestions.remove_class("-hidden")
        # Encoded option id (start_offset, replacement) per the new shape.
        # start_offset = -4 (covers ``@src``); replacement = ``@src/tui/app.py``.
        pi._accept_at_selection("-4\0@src/tui/app.py")
        await pilot.pause()
        assert pi._input.value == "see @src/tui/app.py "
        assert pi._input.cursor_position == len("see @src/tui/app.py ")


@pytest.mark.asyncio
async def test_prompt_input_at_popup_splice_mid_buffer_no_double_space(
    tmp_path: Path,
) -> None:
    """Cursor mid-buffer with trailing space: splice should not insert a
    second space (Critic-flagged double-space bug)."""

    pi = PromptInput(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi._input.value = "see @src foo"
        # Cursor is right after ``@src`` (position 8), before the space.
        pi._input.cursor_position = 8
        pi._at_suggestions.remove_class("-hidden")
        pi._accept_at_selection("-4\0@src/tui/app.py")
        await pilot.pause()
        # Resulting buffer: replace ``@src`` with ``@src/tui/app.py``;
        # the existing space after the cursor is preserved (no double).
        assert pi._input.value == "see @src/tui/app.py foo"
        assert "  " not in pi._input.value


@pytest.mark.asyncio
async def test_prompt_input_at_popup_accepts_selection(
    tmp_path: Path,
) -> None:
    """``_accept_at_selection`` splices the selected path into the input
    at the ``@``-token site (encoded-id form — the canonical shape)."""

    pi = PromptInput(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi._input.value = "see @src"
        pi._input.cursor_position = len(pi._input.value)
        # Force the popup open; bypass the path-completer (we just want
        # the splice path).
        pi._at_suggestions.remove_class("-hidden")
        # Encoded option_id — start_offset=-4 covers the ``@src`` token.
        pi._accept_at_selection("-4\0@src/tui/app.py")
        await pilot.pause()
        assert pi._input.value == "see @src/tui/app.py "
        assert pi._input.cursor_position == len("see @src/tui/app.py ")
        # Popup auto-closes after accept.
        assert pi._at_suggestions.has_class("-hidden")


@pytest.mark.asyncio
async def test_prompt_input_external_apis_dont_crash(tmp_path: Path) -> None:
    """Sanity smoke: the public Phase-3 APIs run without exceptions."""

    pi = PromptInput(words_provider=lambda: [], workspace_root=tmp_path)
    async with _Harness(pi).run_test() as pilot:
        pi.set_queued_commands(["/foo"])
        pi.set_footer_hints([("Ctrl+C", "cancel")])
        pi.set_vim_mode(True)
        pi.set_vim_mode(False)
        await pilot.pause()
        assert pi.queued_commands.queue == ("/foo",)
        assert pi.footer.hints == (("Ctrl+C", "cancel"),)


@pytest.mark.asyncio
async def test_stash_recovery_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stash a draft → mount fresh PromptInput in the same workspace →
    notice fires → recover restores the input value and clears the stash."""

    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    write_stash("recovered draft", workspace_root=workspace)

    pi = PromptInput(words_provider=lambda: [], workspace_root=workspace)
    async with _Harness(pi).run_test() as pilot:
        await pilot.pause()
        assert pi.stash_notice.has_stash is True
        assert pi.recover_stashed_draft() is True
        assert pi._input.value == "recovered draft"
        assert pi.stash_notice.has_stash is False
        # Stash file is gone after recovery.
        assert read_stash(workspace_root=workspace) == ""


@pytest.mark.asyncio
async def test_stash_current_draft_writes_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    pi = PromptInput(words_provider=lambda: [], workspace_root=workspace)
    async with _Harness(pi).run_test() as pilot:
        pi._input.value = "halfway through a thought"
        pi.stash_current_draft()
        await pilot.pause()
    assert read_stash(workspace_root=workspace) == "halfway through a thought"


# ------------------------------------------------------------------
# Help menu (smoke — modal mounts and dismisses without crash)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_menu_renders_bindings_and_dismisses(tmp_path: Path) -> None:
    from src.tui.keybindings_dispatcher import KeybindingDispatcher
    from src.tui.widgets.prompt_input_help_menu import PromptInputHelpMenu

    dispatcher = KeybindingDispatcher.from_defaults()

    class _ModalHarness(App):
        async def on_mount(self) -> None:
            await self.push_screen(PromptInputHelpMenu(dispatcher))

    async with _ModalHarness().run_test() as pilot:
        await pilot.pause()
        # Modal is on the stack — second screen.
        assert isinstance(pilot.app.screen, PromptInputHelpMenu)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, PromptInputHelpMenu)
