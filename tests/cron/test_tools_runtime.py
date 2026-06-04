from __future__ import annotations

import pytest

from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.defaults import build_default_registry
from src.tool_system.tools.cron import CronCreateTool as FallbackCronCreateTool

from clawcodex_ext.cron_system.runtime import attach_cron_runtime, replace_cron_tools
from clawcodex_ext.cron_system.tools import CronCreateTool
from clawcodex_ext.cron_system.runs import read_cron_runs


class _Runtime:
    def __init__(self, tmp_path):
        self.workspace_root = tmp_path
        self.tool_context = ToolContext(workspace_root=tmp_path)


def test_replace_cron_tools_swaps_fallback_implementation() -> None:
    registry = build_default_registry(provider=None)
    assert registry.get("CronCreate") is FallbackCronCreateTool
    replace_cron_tools(registry)
    assert registry.get("CronCreate") is CronCreateTool


def test_extension_tools_store_session_tasks_by_default(tmp_path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    created = CronCreateTool.call({"cron": "*/5 * * * *", "prompt": "ping"}, ctx).output
    assert len(created["id"]) == 8
    assert created["durable"] is False
    listed = registry_tool("CronList").call({}, ctx).output
    assert [job["id"] for job in listed["jobs"]] == [created["id"]]
    assert not (tmp_path / ".claude" / "scheduled_tasks.json").exists()
    deleted = registry_tool("CronDelete").call({"id": created["id"]}, ctx).output
    assert deleted["success"] is True


def test_extension_tools_persist_durable_tasks(tmp_path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    created = CronCreateTool.call({"cron": "*/5 * * * *", "prompt": "ping", "durable": True}, ctx).output
    assert created["durable"] is True
    assert (tmp_path / ".claude" / "scheduled_tasks.json").exists()
    listed = registry_tool("CronList").call({}, ctx).output
    assert [job["id"] for job in listed["jobs"]] == [created["id"]]


def test_extension_delete_missing_task_errors(tmp_path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    with pytest.raises(ToolInputError, match="No scheduled job"):
        registry_tool("CronDelete").call({"id": "missing"}, ctx)


def test_mutating_cron_tools_are_not_read_only() -> None:
    assert CronCreateTool.is_read_only({}) is False
    assert registry_tool("CronDelete").is_read_only({}) is False
    assert registry_tool("CronList").is_read_only({}) is True



def registry_tool(name: str):
    registry = build_default_registry(provider=None)
    replace_cron_tools(registry)
    tool = registry.get(name)
    assert tool is not None
    return tool
