from __future__ import annotations

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.tools.cron import CronCreateTool as FallbackCronCreateTool

from clawcodex_ext.cron_system.runtime import attach_cron_runtime, replace_cron_tools
from clawcodex_ext.cron_system.tools import CronCreateTool


class _Runtime:
    def __init__(self, tmp_path):
        self.workspace_root = tmp_path
        self.tool_context = ToolContext(workspace_root=tmp_path)


def test_replace_cron_tools_swaps_fallback_implementation() -> None:
    registry = build_default_registry(provider=None)
    assert registry.get("CronCreate") is FallbackCronCreateTool
    replace_cron_tools(registry)
    assert registry.get("CronCreate") is CronCreateTool


def test_extension_tools_persist_tasks(tmp_path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    created = CronCreateTool.call({"cron": "*/5 * * * *", "prompt": "ping"}, ctx).output
    assert len(created["id"]) == 8
    listed = registry_tool("CronList").call({}, ctx).output
    assert [job["id"] for job in listed["jobs"]] == [created["id"]]
    deleted = registry_tool("CronDelete").call({"id": created["id"]}, ctx).output
    assert deleted["success"] is True


def test_attach_cron_runtime_adds_scheduler_and_outbox(tmp_path) -> None:
    runtime = _Runtime(tmp_path)
    scheduler = attach_cron_runtime(runtime)
    assert runtime.cron_scheduler is scheduler
    scheduler.on_fire("ping")
    assert runtime.tool_context.outbox == [{"type": "cron_prompt", "prompt": "ping"}]


def registry_tool(name: str):
    registry = build_default_registry(provider=None)
    replace_cron_tools(registry)
    tool = registry.get(name)
    assert tool is not None
    return tool
