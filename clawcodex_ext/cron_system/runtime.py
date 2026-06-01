"""Runtime glue for downstream Cron tools and scheduler (F-22-G1 + G4)."""

from __future__ import annotations

import logging
from typing import Any

from .models import CronTask, is_cron_disabled, load_jitter_config
from .runs import CronRun
from .scheduler import CronScheduler
from .tools import CronCreateTool, CronDeleteTool, CronListTool

_log = logging.getLogger(__name__)

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


def attach_cron_runtime(
    ctx: Any,
    *,
    autostart: bool = False,
    is_killed: Any | None = None,
) -> CronScheduler:
    """Wire Cron tools + scheduler to a session context.

    ``is_killed`` is the F-22-G1 kill switch. When None, falls back to
    ``is_cron_disabled`` (reads ``CLAWCODEX_DISABLE_CRON``). When provided,
    it takes precedence — daemon callers can pass a GrowthBook-style flag.
    """
    if is_killed is None:
        is_killed = is_cron_disabled

    outbox = getattr(ctx.tool_context, "outbox", None)
    if outbox is None:
        outbox = []
        setattr(ctx.tool_context, "outbox", outbox)

    def on_fire(prompt: str) -> None:
        if is_cron_disabled():
            return
        outbox.append({"type": "cron_prompt", "prompt": prompt})

    def on_fire_task(task: CronTask, run: CronRun) -> None:
        if is_cron_disabled():
            return
        outbox.append(
            {
                "type": "cron_prompt",
                "prompt": task.prompt,
                "task_id": task.id,
                "run_id": run.id,
            }
        )

    def on_missed(tasks: list[CronTask], notification: str) -> None:
        if is_cron_disabled():
            return
        outbox.append(
            {
                "type": "cron_missed",
                "tasks": [task.id for task in tasks],
                "notification": notification,
            }
        )

    # F-22-G7: opt-in observability sink — by default just logs at debug.
    def _log_event(payload: dict) -> None:
        _log.debug("cron event: %s", payload)

    # F-22-G2: the scheduler hot-loads the jitter config on every
    # ``check_once`` tick. Threading the loader through ctx.cron_jitter_config
    # (if present) lets REPL callers inject a GrowthBook-style remote source.
    config_loader = getattr(ctx, "cron_jitter_config", None)

    scheduler = CronScheduler(
        ctx.workspace_root,
        on_fire=on_fire,
        on_fire_task=on_fire_task,
        on_missed=on_missed,
        is_killed=is_killed,
        load_jitter_config=config_loader,
        on_fire_event=_log_event,
        on_missed_event=_log_event,
        on_expired_event=_log_event,
    )
    setattr(ctx, "cron_scheduler", scheduler)
    setattr(ctx, "cron_jitter_config", lambda: load_jitter_config(ctx.workspace_root))
    if autostart:
        scheduler.start()
    return scheduler


def install_permanent_cron_tasks(
    workspace_root: Any,
    tasks: list[dict],
) -> list[tuple[Any, bool]]:
    """F-22-G4 installer entry point.

    ``tasks`` is a list of dicts with keys: ``cron``, ``prompt``,
    optional ``recurring`` (default True), ``jitter`` (CronJitterConfig),
    ``created_at`` (epoch ms), ``task_id`` (8-hex string).

    Returns a list of ``(task, created)`` tuples — same shape as
    :func:`clawcodex_ext.cron_system.tasks.write_permanent_task_if_missing`.
    Used by the assistant-mode installer to seed catch-up / morning-checkin
    / dream tasks.
    """
    # Local import to avoid circular import: tasks.py imports from .jitter
    # which imports from .models.
    from .tasks import write_permanent_task_if_missing

    results: list[tuple[Any, bool]] = []
    for spec in tasks:
        try:
            result = write_permanent_task_if_missing(
                workspace_root,
                cron=spec["cron"],
                prompt=spec["prompt"],
                recurring=spec.get("recurring", True),
                jitter=spec.get("jitter"),
                created_at=spec.get("created_at"),
                task_id=spec.get("task_id"),
            )
        except PermissionError as exc:
            _log.warning("skipping permanent install: %s", exc)
            continue
        results.append(result)
    return results
