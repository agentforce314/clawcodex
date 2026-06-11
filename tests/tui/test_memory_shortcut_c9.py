"""C9 tests: `#` memory-append shortcut (service, screen, routing)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.memory_append import (
    SAVING_MESSAGES,
    append_memory_note,
    format_note_line,
    pick_saving_message,
)


class TestAppendService:
    def test_creates_file_and_bullets_note(self, tmp_path) -> None:
        target = tmp_path / "CLAUDE.md"
        assert append_memory_note(str(target), "prefer tabs")
        assert target.read_text() == "- prefer tabs\n"

    def test_appends_with_newline_discipline(self, tmp_path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing\n\n- old note")  # no trailing newline
        assert append_memory_note(str(target), "new note")
        assert target.read_text() == "# Existing\n\n- old note\n- new note\n"

    def test_existing_punctuation_not_double_bulleted(self, tmp_path) -> None:
        assert format_note_line("- already a bullet") == "- already a bullet"
        assert format_note_line("# heading note") == "# heading note"
        assert format_note_line("plain") == "- plain"
        target = tmp_path / "m.md"
        assert append_memory_note(str(target), "  - spaced bullet  ")
        assert target.read_text() == "- spaced bullet\n"

    def test_empty_note_rejected(self, tmp_path) -> None:
        target = tmp_path / "m.md"
        assert not append_memory_note(str(target), "   ")
        assert not target.exists()

    def test_write_failure_returns_false(self, tmp_path) -> None:
        # A directory at the target path makes open() fail with OSError.
        target = tmp_path / "CLAUDE.md"
        target.mkdir()
        assert not append_memory_note(str(target), "note")

    def test_creates_missing_parent_dirs(self, tmp_path) -> None:
        target = tmp_path / "deep" / "nested" / "MEMORY.md"
        assert append_memory_note(str(target), "note")
        assert target.read_text() == "- note\n"

    def test_saving_message_pool(self) -> None:
        assert pick_saving_message() in SAVING_MESSAGES


@pytest.mark.asyncio
async def test_memory_save_screen_outcomes() -> None:
    pytest.importorskip("textual")
    import asyncio

    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static

    from src.command_system.types import UIOption
    from src.tui.screens.memory_save import MemorySaveScreen

    class _Host(Screen):
        def compose(self) -> ComposeResult:
            yield Static("host")

    class _App(App):
        def on_mount(self) -> None:
            self.push_screen(_Host())

    options = [
        UIOption(value="/u/CLAUDE.md", label="User memory", description="~"),
        UIOption(value="/p/CLAUDE.md", label="Project memory", description="./"),
    ]
    for presses, expected in (
        (("enter",), "/u/CLAUDE.md"),
        (("down", "enter"), "/p/CLAUDE.md"),
        (("escape",), None),
    ):
        app = _App()
        async with app.run_test() as pilot:
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            app.push_screen(
                MemorySaveScreen("remember this", options),
                callback=lambda r: future.set_result(r),
            )
            await pilot.pause()
            for key in presses:
                await pilot.press(key)
            result = await asyncio.wait_for(future, timeout=5)
        assert result == expected, (presses, result)


class TestReplRouting:
    def _fake_screen(self):
        from src.tui.screens.repl import REPLScreen

        calls: dict[str, list] = {
            "bash": [],
            "memory": [],
            "agent": [],
            "user_rows": [],
        }
        fake = SimpleNamespace(
            app=SimpleNamespace(
                handle_local_slash_command=lambda text, t: False,
                run_bash_mode=lambda cmd, t: calls["bash"].append(cmd),
                run_memory_shortcut=lambda note, t: calls["memory"].append(note),
                submit_to_agent=lambda text: calls["agent"].append(text),
            ),
            transcript=SimpleNamespace(
                append_user=lambda text: calls["user_rows"].append(text)
            ),
            status_bar=SimpleNamespace(
                set_busy=lambda: None, bump_turn=lambda: None
            ),
        )
        on_submit = REPLScreen.on_prompt_submitted
        return fake, calls, on_submit

    def _msg(self, text: str):
        return SimpleNamespace(text=text)

    def test_hash_prefix_routes_to_memory_shortcut(self) -> None:
        fake, calls, on_submit = self._fake_screen()
        on_submit(fake, self._msg("# remember to run lint"))
        assert calls["memory"] == [" remember to run lint"]
        assert calls["agent"] == []

    def test_bare_hash_falls_through_to_agent(self) -> None:
        fake, calls, on_submit = self._fake_screen()
        on_submit(fake, self._msg("#"))
        assert calls["memory"] == []
        assert calls["agent"] == ["#"]

    def test_bang_still_routes_to_bash(self) -> None:
        fake, calls, on_submit = self._fake_screen()
        on_submit(fake, self._msg("!ls"))
        assert calls["bash"] == ["ls"]


class TestAppMemoryFlow:
    def _fake(self):
        from src.tui.app import ClawCodexTUI

        rows: list[str] = []
        inserted: list[str] = []
        fake = SimpleNamespace(
            _insert_into_prompt=lambda text: inserted.append(text),
        )
        transcript = SimpleNamespace(
            append_system=lambda text, style="muted": rows.append(text)
        )
        on_target = (
            lambda note, path: ClawCodexTUI._on_memory_target(
                fake, note, path, transcript
            )
        )
        return on_target, rows, inserted

    def test_cancel_row_and_note_restored(self) -> None:
        on_target, rows, inserted = self._fake()
        on_target(" my note", None)
        assert rows == ["Cancelled memory editing"]
        assert inserted == ["# my note"]

    def test_success_rows(self, tmp_path) -> None:
        on_target, rows, inserted = self._fake()
        target = tmp_path / "CLAUDE.md"
        on_target(" my note", str(target))
        assert target.read_text() == "- my note\n"
        assert rows[0] == "# my note"
        assert rows[1] in SAVING_MESSAGES
        assert inserted == []

    def test_write_failure_row_and_restore(self, tmp_path) -> None:
        on_target, rows, inserted = self._fake()
        target = tmp_path / "CLAUDE.md"
        target.mkdir()  # forces the append to fail
        on_target("note", str(target))
        assert any("memory not saved" in r for r in rows)
        assert inserted == ["#note"]

    def test_enumeration_failure_row_and_restore(self, monkeypatch) -> None:
        import asyncio

        import src.command_system.memory_command as memory_command_mod
        from src.tui.app import ClawCodexTUI

        async def boom(cwd):
            raise RuntimeError("no memory files")

        monkeypatch.setattr(memory_command_mod, "build_memory_options", boom)
        rows: list[str] = []
        inserted: list[str] = []
        fake = SimpleNamespace(
            workspace_root="/tmp",
            _insert_into_prompt=lambda text: inserted.append(text),
        )
        transcript = SimpleNamespace(
            append_system=lambda text, style="muted": rows.append(text)
        )
        asyncio.run(ClawCodexTUI._memory_shortcut_flow(fake, "note", transcript))
        assert any("Could not enumerate" in r for r in rows)
        assert inserted == ["#note"]

    def test_note_recorded_in_history(self) -> None:
        from src.tui.app import ClawCodexTUI

        history: list[str] = []
        workers: list = []
        fake = SimpleNamespace(
            history_store=SimpleNamespace(
                append=lambda text: history.append(text)
            ),
            run_worker=lambda coro, **kw: workers.append(coro),
            _memory_shortcut_flow=lambda note, transcript: _coro(),
        )
        ClawCodexTUI.run_memory_shortcut(fake, " my note", None)
        assert history == ["# my note"]
        for w in workers:
            w.close()


async def _coro():
    return None
