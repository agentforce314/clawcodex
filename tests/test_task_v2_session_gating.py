from __future__ import annotations

from src.bootstrap.state import get_is_interactive, set_is_interactive
from src.permissions.types import ToolPermissionContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.registry import get_tools


def _enabled_tool_names() -> set[str]:
    registry = build_default_registry()
    permission_context = ToolPermissionContext(mode="bypassPermissions")
    return {tool.name for tool in get_tools(registry, permission_context)}


def test_interactive_session_exposes_task_v2_and_hides_todo_write(monkeypatch) -> None:
    previous = get_is_interactive()
    monkeypatch.delenv("CLAUDE_CODE_ENABLE_TASKS", raising=False)
    try:
        set_is_interactive(True)
        names = _enabled_tool_names()
        assert {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"} <= names
        assert "TodoWrite" not in names
    finally:
        set_is_interactive(previous)


def test_print_session_keeps_todo_write_and_hides_task_v2(monkeypatch) -> None:
    previous = get_is_interactive()
    monkeypatch.delenv("CLAUDE_CODE_ENABLE_TASKS", raising=False)
    try:
        set_is_interactive(False)
        names = _enabled_tool_names()
        assert "TodoWrite" in names
        assert not {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"} & names
    finally:
        set_is_interactive(previous)
