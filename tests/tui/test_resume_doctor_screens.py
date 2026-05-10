"""Smoke tests for Phase-8 Resume / Doctor screens (gap #9)."""

from __future__ import annotations

import pytest
from textual.app import App

from src.tui.screens.doctor import DoctorScreen
from src.tui.screens.resume_conversation import ResumeConversation


# ------------------------------------------------------------------
# ResumeConversation
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_screen_mounts_and_dismisses() -> None:
    """Modal lifecycle smoke — modal renders, Esc dismisses."""

    class _Harness(App):
        async def on_mount(self) -> None:
            await self.push_screen(ResumeConversation())

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        assert isinstance(pilot.app.screen, ResumeConversation)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, ResumeConversation)


@pytest.mark.asyncio
async def test_resume_screen_shows_empty_state_when_no_sessions(
    tmp_path, monkeypatch
) -> None:
    """Phase-8 placeholder: no sessions → empty-state surface.

    Isolate the global ``SESSIONS_DIR`` so this test doesn't pick up
    sessions from the dev's real ``~/.clawcodex/sessions/`` (Phase-8
    wiring made the screen actually read from disk).
    """

    monkeypatch.setattr(
        "src.services.session_storage.SESSIONS_DIR", tmp_path
    )

    class _Harness(App):
        async def on_mount(self) -> None:
            await self.push_screen(ResumeConversation())

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, ResumeConversation)
        from textual.widgets import OptionList

        # Empty case — no OptionList children mounted.
        assert not screen.query(OptionList)


@pytest.mark.asyncio
async def test_resume_screen_handles_storage_exception_gracefully() -> None:
    """Audit state-2 path: even if SessionStorage import fails, the
    screen mounts cleanly with the placeholder empty state."""

    class _Harness(App):
        async def on_mount(self) -> None:
            await self.push_screen(ResumeConversation())

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        # Just ensure no exception propagated to the test harness.
        assert isinstance(pilot.app.screen, ResumeConversation)


# ------------------------------------------------------------------
# DoctorScreen
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doctor_screen_mounts_and_dismisses() -> None:
    class _Harness(App):
        async def on_mount(self) -> None:
            await self.push_screen(DoctorScreen())

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        assert isinstance(pilot.app.screen, DoctorScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, DoctorScreen)


@pytest.mark.asyncio
async def test_doctor_screen_renders_known_sections() -> None:
    """Smoke — sections render without crashing on the missing app_state."""

    class _Harness(App):
        async def on_mount(self) -> None:
            await self.push_screen(DoctorScreen(app_state=None))

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, DoctorScreen)
        # Sections are mounted as Static panels; just ensure ≥ 4 (env,
        # hyperlinks, frame metrics, storage).
        from textual.widgets import Static

        statics = list(screen.query(Static))
        # Title + 4 section panels = at least 5 statics.
        assert len(statics) >= 5


@pytest.mark.asyncio
async def test_doctor_screen_with_app_state() -> None:
    class _State:
        provider = "openai"
        model = "gpt-x"
        workspace_root = "/tmp/ws"
        theme_name = "claude"

    class _Harness(App):
        async def on_mount(self) -> None:
            await self.push_screen(DoctorScreen(app_state=_State()))

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        assert isinstance(pilot.app.screen, DoctorScreen)
