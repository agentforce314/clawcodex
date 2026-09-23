"""Bounded cleanup of the background work owned by one session."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from src.tasks.stop_task import stop_task
from src.tasks_core import is_terminal_task_status
from src.utils.message_queue_manager import drain_pending_notifications

logger = logging.getLogger(__name__)


async def shutdown_background_tasks(context: Any, *, timeout: float = 5.0) -> None:
    """Close admission, interrupt workers, and join them before transports close."""
    context.agent_supervisor.set_paused(True)
    for agent in context.agent_supervisor.snapshot()["active"]:
        context.agent_supervisor.interrupt(agent["subagent_id"])
    runtime = context.team_runtime
    if runtime is not None:
        runtime.stop.set()
    await asyncio.gather(
        *(
            stop_task(state.id, context, reason="session closed")
            for state in context.runtime_tasks.all()
            if not is_terminal_task_status(state.status)
        ),
        return_exceptions=True,
    )
    tasks = context.task_manager.list()
    for task in tasks:
        task.stop_event.set()

    def join() -> None:
        deadline = time.monotonic() + timeout
        for task in tasks:
            if task.thread is not threading.current_thread():
                task.thread.join(timeout=max(0.0, deadline - time.monotonic()))

    await asyncio.to_thread(join)
    if runtime is not None:
        try:
            runtime.delete()
        except Exception:
            logger.warning(
                "Session closed with a teammate still stopping", exc_info=True
            )
    drain_pending_notifications(scope=context.runtime_tasks)
