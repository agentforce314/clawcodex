"""C5 tests: workspace-search service, dialogs, dispatch, insertion."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.services.workspace_search import (
    ContentMatch,
    file_insertion,
    filter_files,
    list_workspace_files,
    search_content,
)


@pytest.fixture
def workspace(tmp_path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text(
        "def needle_function():\n    return 1\n"
    )
    (tmp_path / "src" / "beta.py").write_text("x = 'no match here'\n")
    (tmp_path / "README.md").write_text("needle_function docs\n")
    return tmp_path


class TestService:
    def test_search_content_parses_and_relativizes(self, workspace) -> None:
        matches, truncated = search_content("needle_function", str(workspace))
        assert not truncated
        files = {m.file for m in matches}
        assert "src/alpha.py" in files and "README.md" in files
        alpha = next(m for m in matches if m.file == "src/alpha.py")
        assert alpha.line == 1
        assert "needle_function" in alpha.text

    def test_search_is_case_insensitive(self, workspace) -> None:
        # TS uses rg -i (GlobalSearchDialog.tsx:268) — an uppercase query
        # must still match.
        matches, _ = search_content("NEEDLE_FUNCTION", str(workspace))
        assert matches

    def test_truncation_flag(self, workspace) -> None:
        big = workspace / "many.txt"
        big.write_text("hit\n" * 40)
        matches, truncated = search_content(
            "hit", str(workspace), max_results=3
        )
        assert len(matches) == 3
        assert truncated

    def test_insertion_formats_are_ts_verbatim(self) -> None:
        match = ContentMatch(file="src/a.py", line=12, text="x")
        assert match.insertion() == "@src/a.py#L12 "
        assert file_insertion("src/a.py") == "@src/a.py "

    def test_list_and_filter_files(self, workspace) -> None:
        files, truncated = list_workspace_files(str(workspace))
        assert not truncated
        assert "src/alpha.py" in files
        ranked = filter_files(files, "alpha")
        assert ranked[0] == "src/alpha.py"
        # Substring beats subsequence.
        ranked2 = filter_files(["zzz_ap.py", "alpha.py"], "alp")
        assert ranked2[0] == "alpha.py"

    def test_empty_query_returns_nothing(self, workspace) -> None:
        assert search_content("   ", str(workspace)) == ([], False)

    def test_mention_fragment_attaches_file(self, workspace) -> None:
        # The @file#Lline insertion must be FUNCTIONAL downstream
        # (review M4): expand_at_mentions strips the fragment and
        # attaches the file.
        from src.command_system.input_processing import expand_at_mentions

        _text, attachments = expand_at_mentions(
            "look at @src/alpha.py#L1 please", cwd=str(workspace)
        )
        assert any(
            "alpha.py" in str(a.get("path", "")) for a in attachments
        ), attachments

    def test_large_output_does_not_stall(self, tmp_path) -> None:
        """Regression for the pipe-buffer deadlock: >64KB of rg output
        previously blocked until the 20s timeout."""

        from src.tool_system.utils.ripgrep import ripgrep

        big = tmp_path / "big.txt"
        big.write_text("needle padding padding padding\n" * 5000)  # ~150KB
        start = time.monotonic()
        lines = ripgrep(["-n", "--fixed-strings", "needle"], str(big))
        elapsed = time.monotonic() - start
        assert len(lines) == 5000
        assert elapsed < 5, f"rg stalled: {elapsed:.1f}s"


class TestDispatch:
    def _dispatch(self, text: str):
        from src.tui.commands import dispatch_local_command

        return dispatch_local_command(
            text, session=None, workspace_root=Path("."), tool_registry=None
        )

    def test_search_plain_and_seeded(self) -> None:
        assert self._dispatch("/search").open_dialog == "search"
        assert self._dispatch("/search foo bar").open_dialog == "search:foo bar"

    def test_open(self) -> None:
        assert self._dispatch("/open").open_dialog == "quickopen"


@pytest.mark.asyncio
async def test_global_search_screen_selection(workspace) -> None:
    pytest.importorskip("textual")
    import asyncio

    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static

    from src.tui.screens.workspace_search import GlobalSearchScreen

    class _Host(Screen):
        def compose(self) -> ComposeResult:
            yield Static("host")

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(_Host())

    app = _App()
    async with app.run_test() as pilot:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        app.push_screen(
            GlobalSearchScreen(
                cwd=str(workspace), initial_query="needle_function"
            ),
            callback=lambda r: future.set_result(r),
        )
        # Let the worker run + results land.
        for _ in range(20):
            await pilot.pause(0.05)
            screen = app.screen
            if getattr(screen, "_matches", None):
                break
        assert getattr(app.screen, "_matches", [])
        # Input kept focus (no theft); Enter on the unchanged query
        # selects the highlighted (top) match.
        await pilot.press("enter")
        result = await asyncio.wait_for(future, timeout=5)
    assert result is not None
    assert result.startswith("@") and "#L" in result and result.endswith(" ")


@pytest.mark.asyncio
async def test_global_search_stale_enter_does_not_select_old_query(
    workspace,
) -> None:
    """Review B1: Enter while a NEW query's search is in flight must not
    insert the OLD query's top match."""

    pytest.importorskip("textual")
    import asyncio

    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static

    from src.tui.screens.workspace_search import GlobalSearchScreen

    class _Host(Screen):
        def compose(self) -> ComposeResult:
            yield Static("host")

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(_Host())

    app = _App()
    async with app.run_test() as pilot:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        app.push_screen(
            GlobalSearchScreen(
                cwd=str(workspace), initial_query="needle_function"
            ),
            callback=lambda r: future.set_result(r),
        )
        for _ in range(20):
            await pilot.pause(0.05)
            if getattr(app.screen, "_matches", None):
                break
        screen = app.screen
        # Simulate the user editing the query and re-running the search:
        # _run_search must clear matches IMMEDIATELY.
        screen._run_search("something else entirely")
        assert screen._matches == []
        # Enter now (unchanged-new-query + no matches) selects nothing.
        await pilot.press("enter")
        await pilot.pause()
        assert not future.done()
        await pilot.press("escape")
        result = await asyncio.wait_for(future, timeout=5)
    assert result is None


@pytest.mark.asyncio
async def test_quick_open_screen_enter_picks_top(workspace) -> None:
    pytest.importorskip("textual")
    import asyncio

    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static

    from src.tui.screens.workspace_search import QuickOpenScreen

    class _Host(Screen):
        def compose(self) -> ComposeResult:
            yield Static("host")

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(_Host())

    app = _App()
    async with app.run_test() as pilot:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        app.push_screen(
            QuickOpenScreen(cwd=str(workspace)),
            callback=lambda r: future.set_result(r),
        )
        for _ in range(20):
            await pilot.pause(0.05)
            if getattr(app.screen, "_all_files", None):
                break
        for ch in "alpha":
            await pilot.press(ch)
        await pilot.pause()
        await pilot.press("enter")
        result = await asyncio.wait_for(future, timeout=5)
    assert result == "@src/alpha.py "


@pytest.mark.asyncio
async def test_prompt_append_value() -> None:
    pytest.importorskip("textual")
    from textual.app import App, ComposeResult

    from src.tui.widgets.prompt_input import PromptInput

    class _Host(App):
        def compose(self) -> ComposeResult:
            yield PromptInput(words_provider=lambda: [])

    app = _Host()
    async with app.run_test() as pilot:
        prompt = app.query_one(PromptInput)
        prompt.set_value("look at ")
        prompt.append_value("@src/a.py#L12 ")
        await pilot.pause()
        assert prompt._input.value == "look at @src/a.py#L12 "
