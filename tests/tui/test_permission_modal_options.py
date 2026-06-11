"""Pilot tests for the C1 multi-option permission modal."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from src.permissions.types import (
    PermissionAskReply,
    PermissionRuleValue,
    PermissionUpdateAddRules,
)
from src.tui.screens.permission_modal import PermissionModal
from src.tui.state import PendingPermission


class _Host(Screen):
    def compose(self) -> ComposeResult:
        yield Static("host")


class _DialogHost(App):
    def on_mount(self) -> None:
        self.push_screen(_Host())


_SUGGESTION = PermissionUpdateAddRules(
    destination="localSettings",
    behavior="allow",
    rules=(PermissionRuleValue(tool_name="Bash", rule_content="git diff:*"),),
)


def _pending(replies: list, suggestions=()) -> PendingPermission:
    return PendingPermission(
        request_id="perm-test",
        tool_name="Bash",
        message="Claude wants to run a command",
        suggestions=tuple(suggestions),
        tool_input={"command": "git diff --stat"},
        decide=replies.append,
    )


@pytest.mark.asyncio
async def test_allow_once_reply() -> None:
    replies: list[PermissionAskReply] = []
    app = _DialogHost()
    async with app.run_test() as pilot:
        app.push_screen(PermissionModal(_pending(replies, [_SUGGESTION])))
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert len(replies) == 1
    assert replies[0].behavior == "allow"
    assert replies[0].chosen_updates == ()


@pytest.mark.asyncio
async def test_allow_always_carries_suggestions() -> None:
    replies: list[PermissionAskReply] = []
    app = _DialogHost()
    async with app.run_test() as pilot:
        app.push_screen(PermissionModal(_pending(replies, [_SUGGESTION])))
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
    assert len(replies) == 1
    assert replies[0].behavior == "allow"
    assert replies[0].chosen_updates == (_SUGGESTION,)


@pytest.mark.asyncio
async def test_allow_always_unavailable_without_suggestions() -> None:
    replies: list[PermissionAskReply] = []
    app = _DialogHost()
    async with app.run_test() as pilot:
        app.push_screen(PermissionModal(_pending(replies, [])))
        await pilot.pause()
        await pilot.press("a")  # no suggestions → ignored
        await pilot.pause()
        assert replies == []
        await pilot.press("escape")
        await pilot.pause()
    assert len(replies) == 1
    assert replies[0].behavior == "deny"


@pytest.mark.asyncio
async def test_deny_with_feedback_flow() -> None:
    replies: list[PermissionAskReply] = []
    app = _DialogHost()
    async with app.run_test() as pilot:
        app.push_screen(PermissionModal(_pending(replies, [_SUGGESTION])))
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        for ch in "use rg":
            await pilot.press(ch if ch != " " else "space")
        await pilot.press("enter")
        await pilot.pause()
    assert len(replies) == 1
    assert replies[0].behavior == "deny"
    assert replies[0].message == "use rg"


@pytest.mark.asyncio
async def test_escape_denies() -> None:
    replies: list[PermissionAskReply] = []
    app = _DialogHost()
    async with app.run_test() as pilot:
        app.push_screen(PermissionModal(_pending(replies, [_SUGGESTION])))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert len(replies) == 1
    assert replies[0].behavior == "deny"
    assert replies[0].message is None
