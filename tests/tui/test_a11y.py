"""Tests for the accessibility helpers in :mod:`src.tui.a11y`."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult

from src.tui.a11y import (
    Announcer,
    LiveRegion,
    aria_label,
    describe_option,
    describe_status,
)
from src.tui.widgets.select_list import SelectList, SelectOption


# ---------- pure-function helpers ----------


def test_describe_option_plain():
    assert describe_option("Run tests") == "Run tests"


def test_describe_option_disabled_prefix():
    assert describe_option("Run tests", disabled=True).startswith("[disabled]")


def test_describe_option_selected_and_description():
    rendered = describe_option(
        "Dark",
        selected=True,
        description="current",
    )
    assert "[selected]" in rendered
    assert "Dark" in rendered
    assert "— current" in rendered


def test_describe_option_empty_label_falls_back():
    assert "(empty)" in describe_option("")


def test_describe_status_maps_kinds():
    assert describe_status("idle").lower().startswith("agent idle")
    assert describe_status("busy") == "Agent busy"
    assert describe_status("busy", verb="Thinking") == "Agent busy: Thinking"
    assert "waiting" in describe_status("waiting").lower()


# ---------- LiveRegion / Announcer ----------


@pytest.mark.asyncio
async def test_live_region_updates_on_announce():
    region = LiveRegion(aria_label="Status")

    class _App(App):
        def compose(self) -> ComposeResult:
            yield region

    async with _App().run_test() as pilot:
        await pilot.pause()
        region.announce("Ready")
        await pilot.pause()
        rendered = str(region.content)
        assert "Ready" in rendered
        assert "Status" in rendered
        assert not region.has_class("-assertive")


@pytest.mark.asyncio
async def test_live_region_assertive_toggles_class():
    region = LiveRegion()

    class _App(App):
        def compose(self) -> ComposeResult:
            yield region

    async with _App().run_test() as pilot:
        await pilot.pause()
        region.announce("Attention", level="assertive")
        await pilot.pause()
        assert region.has_class("-assertive")
        region.announce("calm", level="polite")
        await pilot.pause()
        assert not region.has_class("-assertive")


@pytest.mark.asyncio
async def test_announcer_records_history_and_updates_region():
    region = LiveRegion()
    notifications: list[tuple[str, str]] = []

    class _App(App):
        def compose(self) -> ComposeResult:
            yield region

        def notify(self, message, *, severity="information", timeout=3.0, **_):  # type: ignore[override]
            notifications.append((message, severity))

    app = _App()
    async with app.run_test() as pilot:
        announcer = Announcer(app)
        announcer.bind_region(region)
        await pilot.pause()
        announcer.announce("Hello")
        announcer.announce("Warn", level="assertive")
        announcer.announce("quiet", notify=False)
        await pilot.pause()

        history = announcer.history
        assert [a.message for a in history] == ["Hello", "Warn", "quiet"]
        assert history[1].level == "assertive"
        # Toast is suppressed when notify=False.
        assert [m for m, _ in notifications] == ["Hello", "Warn"]
        assert ("Warn", "warning") in notifications


@pytest.mark.asyncio
async def test_announcer_respects_disable_flag():
    region = LiveRegion()

    class _App(App):
        def compose(self) -> ComposeResult:
            yield region

    app = _App()
    async with app.run_test() as pilot:
        announcer = Announcer(app)
        announcer.bind_region(region)
        announcer.set_enabled(False)
        await pilot.pause()
        announcer.announce("hidden")
        await pilot.pause()
        assert announcer.history == []
        # Region stays empty.
        assert str(region.content).strip() == ""


@pytest.mark.asyncio
async def test_announcer_history_bounded():
    class _App(App):
        def compose(self) -> ComposeResult:
            yield LiveRegion()

    app = _App()
    async with app.run_test() as pilot:
        announcer = Announcer(app)
        await pilot.pause()
        for i in range(announcer.HISTORY_LIMIT + 20):
            announcer.announce(f"msg-{i}", notify=False)
        hist = announcer.history
        assert len(hist) == announcer.HISTORY_LIMIT
        # Oldest ones are evicted; the most recent one is retained.
        assert hist[-1].message == f"msg-{announcer.HISTORY_LIMIT + 19}"


# ---------- Widget aria_label shim ----------


@pytest.mark.asyncio
async def test_aria_label_sets_tooltip():
    region = LiveRegion()

    class _App(App):
        def compose(self) -> ComposeResult:
            yield region

    async with _App().run_test() as pilot:
        await pilot.pause()
        aria_label(region, "Announcement area")
        assert region.tooltip == "Announcement area"


# ---------- SelectList a11y integration ----------


@pytest.mark.asyncio
async def test_select_list_describe_reflects_state():
    select = SelectList(
        [
            SelectOption(label="One"),
            SelectOption(label="Two (off)", disabled=True),
            SelectOption(label="Three", description="current"),
        ],
        initial_index=2,
    )

    class _App(App):
        def compose(self) -> ComposeResult:
            yield select

    async with _App().run_test() as pilot:
        await pilot.pause()
        lines = select.describe()
        assert lines[0] == "One"
        assert lines[1].startswith("[disabled]")
        assert "[selected]" in lines[2]
        assert "— current" in lines[2]


@pytest.mark.asyncio
async def test_select_list_renders_disabled_prefix_visually():
    """Disabled rows should carry a visible ``[disabled]`` marker so
    terminal screen readers pick it up (the ``dim strike`` CSS style
    alone is invisible to AT).
    """

    select = SelectList(
        [
            SelectOption(label="Run", disabled=False),
            SelectOption(label="Stop", disabled=True),
        ]
    )

    class _App(App):
        def compose(self) -> ComposeResult:
            yield select

    async with _App().run_test() as pilot:
        await pilot.pause()
        rendered = str(select.content)
        assert "[disabled]" in rendered
        assert "Run" in rendered
