"""Runtime glue for downstream Cron tools and scheduler."""

from __future__ import annotations

from typing import Any

from .models import CronTask
from .scheduler import CronScheduler
from .tools import CronCreateTool, CronDeleteTool, CronListTool

_CRON_TOOL_NAMES = {"croncreate", "crondelete", "cronlist"}


def replace_cron_tools(registry: Any) -> None:
    registry._tools = [tool for tool in registry._tools if tool.name.lower() not in _CRON_TOOL_NAMES]
    for name in list(registry._by_name.keys()):
        tool = registry._by_name[name]
        if tool.name.lower() in _CRON_TOOL_NAMES:
            del registry._by_name[name]
    registry.register(CronCreateTool)
    registry.register(CronListTool)
    registry.register(CronDeleteTool)


def attach_cron_runtime(ctx: Any, *, autostart: bool = False) -> CronScheduler:
    outbox = getattr(ctx.tool_context, "outbox", None)
    if outbox is None:
        outbox = []
        setattr(ctx.tool_context, "outbox", outbox)

    def on_fire(prompt: str) -> None:
        outbox.append({"type": "cron_prompt", "prompt": prompt})

    def on_missed(tasks: list[CronTask], notification: str) -> None:
        outbox.append(
            {
                "type": "cron_missed",
                "tasks": [task.id for task in tasks],
                "notification": notification,
            }
        )

    scheduler = CronScheduler(ctx.workspace_root, on_fire=on_fire, on_missed=on_missed)
    setattr(ctx, "cron_scheduler", scheduler)
    if autostart:
        scheduler.start()
    return scheduler
