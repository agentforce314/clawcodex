"""End-to-end tests for Phase 3 dialog screens + widgets."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from src.tui.screens import (
    DiffDialogScreen,
    FileDiff,
    McpListScreen,
    McpServer,
    MessageSelectorScreen,
    TranscriptMessage,
)
from src.tui.widgets.structured_diff import (
    StructuredDiff,
    count_changes,
    parse_structured_patch,
    parse_unified_diff,
)
from src.tui.widgets.task_list import Task, TaskListWidget
from src.tui.widgets.tool_activity.edit import EditActivity, _format_edit_summary


class _Host(Screen):
    def compose(self) -> ComposeResult:
        yield Static("host")


class _DialogHost(App):
    def on_mount(self) -> None:
        self.push_screen(_Host())


def _push(app: App, screen) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _callback(result):
        if not future.done():
            future.set_result(result)

    app.push_screen(screen, callback=_callback)
    return future


# ------------------------------------------------------------------
# MessageSelector
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_selector_restores_selected_prompt():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = MessageSelectorScreen(
            messages=[
                TranscriptMessage(index=0, kind="user", text="first prompt"),
                TranscriptMessage(index=1, kind="user", text="second prompt"),
                TranscriptMessage(index=2, kind="user", text="third prompt"),
            ]
        )
        fut = _push(app, screen)
        await pilot.pause()
        # Cursor starts at the most recent user message.
        assert screen._select.current.value == 2
        await pilot.press("up")
        await pilot.press("enter")
        result = await fut
        assert result == (1, "restore")


@pytest.mark.asyncio
async def test_message_selector_escape_resolves_cancel():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = MessageSelectorScreen(
            messages=[TranscriptMessage(index=0, kind="user", text="hi")]
        )
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("escape")
        index, action = await fut
        assert action == "cancel"
        assert index == -1


@pytest.mark.asyncio
async def test_message_selector_summarize_action():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = MessageSelectorScreen(
            messages=[
                TranscriptMessage(index=0, kind="user", text="only prompt"),
            ]
        )
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("s")
        index, action = await fut
        assert index == 0
        assert action == "summarize"


# ------------------------------------------------------------------
# StructuredDiff parsing helpers
# ------------------------------------------------------------------


SAMPLE_PATCH = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 def foo():
-    return 1
+    return 2
+    # added comment
     pass
"""


def test_parse_unified_diff_counts():
    lines = parse_unified_diff(SAMPLE_PATCH)
    add, remove = count_changes(lines)
    assert add == 2
    assert remove == 1
    # Line numbers are threaded through the hunk.
    add_lines = [l for l in lines if l.kind == "add"]
    assert add_lines[0].new_lineno == 2
    assert add_lines[1].new_lineno == 3


def test_structured_diff_stats():
    widget = StructuredDiff(patch=SAMPLE_PATCH)
    add, remove = widget.stats
    assert add == 2
    assert remove == 1


@pytest.mark.asyncio
async def test_structured_diff_set_patch_updates_stats():
    widget = StructuredDiff(patch=SAMPLE_PATCH)

    class _App(App):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        widget.set_patch("@@ -1,1 +1,1 @@\n-a\n+b\n")
        await pilot.pause()
        add, remove = widget.stats
        assert add == 1
        assert remove == 1


# ------------------------------------------------------------------
# parse_structured_patch + EditActivity body parity
# ------------------------------------------------------------------


SAMPLE_STRUCTURED_PATCH = [
    {
        "oldStart": 10,
        "oldLines": 3,
        "newStart": 10,
        "newLines": 4,
        "lines": [
            " keep me",
            "-old line",
            "+new line",
            "+brand new",
            " trailing",
        ],
    },
    {
        "oldStart": 50,
        "oldLines": 2,
        "newStart": 51,
        "newLines": 1,
        "lines": [
            " ctx",
            "-removed",
        ],
    },
]


def test_parse_structured_patch_threads_line_numbers():
    lines = parse_structured_patch(SAMPLE_STRUCTURED_PATCH)
    add, remove = count_changes(lines)
    assert add == 2
    assert remove == 2

    add_lines = [l for l in lines if l.kind == "add"]
    assert add_lines[0].new_lineno == 11
    assert add_lines[1].new_lineno == 12

    remove_lines = [l for l in lines if l.kind == "remove"]
    # First removal lives in the first hunk (oldStart=10, after 1 context).
    assert remove_lines[0].old_lineno == 11
    # Second removal is in the second hunk (oldStart=50, after 1 context).
    assert remove_lines[1].old_lineno == 51


def test_edit_activity_result_body_renders_summary_and_diff():
    activity = EditActivity(
        tool_name="Edit",
        tool_input={"file_path": "/tmp/foo.py"},
    )
    body = activity.result_body(
        {
            "type": "update",
            "filePath": "/tmp/foo.py",
            "structuredPatch": SAMPLE_STRUCTURED_PATCH,
        },
        is_error=False,
    )
    assert body is not None
    rendered = "".join(part.plain for part in body.renderables)
    assert "Added 2 lines, removed 2 lines" in rendered
    assert "+ new line" in rendered
    assert "- old line" in rendered


def test_edit_activity_result_body_create_fallback():
    activity = EditActivity(tool_name="Edit", tool_input={"file_path": "/tmp/new.py"})
    body = activity.result_body(
        {"type": "create", "filePath": "/tmp/new.py", "structuredPatch": []},
        is_error=False,
    )
    assert body is not None
    assert body.plain == "/tmp/new.py"


def test_edit_activity_result_body_error_returns_none():
    activity = EditActivity(tool_name="Edit", tool_input={"file_path": "/tmp/foo.py"})
    assert activity.result_body({"error": "boom"}, is_error=True) is None


@pytest.mark.parametrize(
    "adds,removes,expected",
    [
        (1, 0, "Added 1 line"),
        (3, 0, "Added 3 lines"),
        (0, 2, "Removed 2 lines"),
        (0, 1, "Removed 1 line"),
        (1, 18, "Added 1 line, removed 18 lines"),
        (4, 4, "Added 4 lines, removed 4 lines"),
        (0, 0, ""),
    ],
)
def test_format_edit_summary_pluralization(adds, removes, expected):
    assert _format_edit_summary(adds, removes) == expected


# ------------------------------------------------------------------
# DiffDialog
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_dialog_resolves_with_picked_file():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = DiffDialogScreen(
            files=[
                FileDiff(path="foo.py", patch=SAMPLE_PATCH),
                FileDiff(path="bar.py", patch="@@ -1,1 +1,1 @@\n-x\n+y\n"),
            ]
        )
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        result = await fut
        assert result == "bar.py"


@pytest.mark.asyncio
async def test_diff_dialog_escape_resolves_none():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = DiffDialogScreen(
            files=[FileDiff(path="foo.py", patch=SAMPLE_PATCH)]
        )
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("escape")
        assert await fut is None


@pytest.mark.asyncio
async def test_diff_dialog_handles_empty_file_list():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = DiffDialogScreen(files=[])
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("escape")
        result = await fut
        assert result is None


# ------------------------------------------------------------------
# TaskList widget
# ------------------------------------------------------------------


def test_task_list_progress_counts_leaves_only():
    root = Task(
        id="root",
        title="parent",
        status="in_progress",
        children=[
            Task(id="a", title="A", status="completed"),
            Task(id="b", title="B", status="in_progress"),
            Task(id="c", title="C", status="pending"),
        ],
    )
    widget = TaskListWidget(tasks=[root])
    done, total = widget.progress()
    assert done == 1
    assert total == 3


@pytest.mark.asyncio
async def test_task_list_set_tasks_updates_rendering():
    widget = TaskListWidget(tasks=[])

    class _App(App):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        widget.set_tasks(
            [
                Task(id="a", title="A", status="completed"),
                Task(id="b", title="B", status="completed"),
            ]
        )
        await pilot.pause()
        done, total = widget.progress()
        assert done == 2
        assert total == 2


# ------------------------------------------------------------------
# MCP dialogs
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_list_dialog_dismisses_with_server_id():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = McpListScreen(
            servers=[
                McpServer(id="alpha", name="alpha", status="connected", tools=["a", "b"]),
                McpServer(id="beta", name="beta", status="disconnected"),
            ]
        )
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        assert await fut == "beta"


@pytest.mark.asyncio
async def test_mcp_list_dialog_empty_server_list():
    app = _DialogHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = McpListScreen(servers=[])
        fut = _push(app, screen)
        await pilot.pause()
        await pilot.press("escape")
        assert await fut is None
