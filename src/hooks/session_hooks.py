from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from .hook_types import HookConfig, HookEvent
from .registry import AsyncHookRegistry, get_global_hook_registry

logger = logging.getLogger(__name__)

SESSION_START_EVENT: HookEvent = "Notification"
SESSION_END_EVENT: HookEvent = "Notification"
COMPACT_EVENT: HookEvent = "Notification"


async def run_session_start_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    session_id: str | None = None,
    cwd: str | None = None,
) -> list[dict[str, Any]]:
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event("Notification")

    results: list[dict[str, Any]] = []
    for hook in hooks:
        if hook.config.matcher and hook.config.matcher != "onSessionStart":
            continue

        stdin_data = {
            "hook_event": "Notification",
            "notification_type": "onSessionStart",
            "session_id": session_id,
            "cwd": cwd,
        }

        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook
        from .exec_prompt_hook import execute_prompt_hook

        if hook.config.type == "command":
            result = await _execute_command_hook(hook.config, stdin_data)
        elif hook.config.type == "http":
            result = await execute_http_hook(hook.config, stdin_data)
        elif hook.config.type == "prompt":
            result = await execute_prompt_hook(hook.config, stdin_data)
        else:
            continue

        results.append({
            "hook": hook.config,
            "result": result,
        })

    return results


async def run_session_end_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    session_id: str | None = None,
    total_cost: float | None = None,
    total_turns: int | None = None,
) -> list[dict[str, Any]]:
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event("Notification")

    results: list[dict[str, Any]] = []
    for hook in hooks:
        if hook.config.matcher and hook.config.matcher != "onSessionEnd":
            continue

        stdin_data = {
            "hook_event": "Notification",
            "notification_type": "onSessionEnd",
            "session_id": session_id,
            "total_cost": total_cost,
            "total_turns": total_turns,
        }

        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook

        if hook.config.type == "command":
            result = await _execute_command_hook(hook.config, stdin_data)
        elif hook.config.type == "http":
            result = await execute_http_hook(hook.config, stdin_data)
        else:
            continue

        results.append({
            "hook": hook.config,
            "result": result,
        })

    return results


async def run_compact_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    session_id: str | None = None,
    tokens_before: int | None = None,
    tokens_after: int | None = None,
    trigger: str = "manual",
) -> list[dict[str, Any]]:
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event("Notification")

    results: list[dict[str, Any]] = []
    for hook in hooks:
        if hook.config.matcher and hook.config.matcher != "onCompact":
            continue

        stdin_data = {
            "hook_event": "Notification",
            "notification_type": "onCompact",
            "session_id": session_id,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "trigger": trigger,
        }

        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook

        if hook.config.type == "command":
            result = await _execute_command_hook(hook.config, stdin_data)
        elif hook.config.type == "http":
            result = await execute_http_hook(hook.config, stdin_data)
        else:
            continue

        results.append({
            "hook": hook.config,
            "result": result,
        })

    return results
