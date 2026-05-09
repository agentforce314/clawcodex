"""Lifecycle-event routers (Phase-1 / WI-1.1 wiring).

Pre-Phase-1 these helpers filtered on ``Notification + matcher: "onXxx"``.
Phase 1 promotes ``SessionStart``, ``SessionEnd``, ``PreCompact`` to first-class
``HookEvent`` values; this module now routes via those names.

Per assumption A9, this file will be renamed to ``lifecycle_routers.py`` in
Phase 2 (when a new ``session_hooks.py`` introduces the registration API in
Phase 3 / WI-3.1). Public function names are preserved across that rename so
callers don't churn — only the import path moves.

Legacy settings.json with ``Notification + matcher: "onSessionStart"`` is
translated to first-class events at config load time
(``config_manager.load_hooks_from_settings``), so by the time these routers
read from the registry/snapshot, the events have been canonicalized.

Phase-7 follow-up D5 update: dispatch by hook.config.type now goes through
``_dispatch_hook_by_type`` (in ``hook_executor``) so provider/model from the
active ToolContext flow into prompt/agent hooks. The optional
``tool_use_context`` kwarg lets callers thread context through; when None,
LLM-driven hook types fall back to the no-provider blocking_error.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from .hook_types import HookConfig, HookEvent
from .registry import AsyncHookRegistry, get_global_hook_registry

logger = logging.getLogger(__name__)

# Phase-1 / WI-1.1 — first-class lifecycle event constants. The legacy values
# (all three pointing at "Notification") have been removed; the back-compat
# reader at config-load time translates legacy settings.json into these
# first-class names.
SESSION_START_EVENT: HookEvent = "SessionStart"
SESSION_END_EVENT: HookEvent = "SessionEnd"
COMPACT_EVENT: HookEvent = "PreCompact"


async def run_session_start_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    session_id: str | None = None,
    cwd: str | None = None,
    tool_use_context: Any = None,
) -> list[dict[str, Any]]:
    """Run all SessionStart hooks. Hook results returned in firing order.

    ``tool_use_context`` (Phase-7 D5) carries the active session's
    provider/model for prompt/agent hook dispatch. Optional for back-compat
    with callers that don't have a ToolContext at session-start time.
    """
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event(SESSION_START_EVENT)

    results: list[dict[str, Any]] = []
    for hook in hooks:
        stdin_data = {
            "hook_event": SESSION_START_EVENT,
            "session_id": session_id,
            "cwd": cwd,
        }

        from .hook_executor import _dispatch_hook_by_type
        result = await _dispatch_hook_by_type(
            hook.config, stdin_data,
            tool_use_context=tool_use_context,
        )

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
    tool_use_context: Any = None,
) -> list[dict[str, Any]]:
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event(SESSION_END_EVENT)

    results: list[dict[str, Any]] = []
    for hook in hooks:
        stdin_data = {
            "hook_event": SESSION_END_EVENT,
            "session_id": session_id,
            "total_cost": total_cost,
            "total_turns": total_turns,
        }

        from .hook_executor import _dispatch_hook_by_type
        result = await _dispatch_hook_by_type(
            hook.config, stdin_data,
            tool_use_context=tool_use_context,
        )

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
    tool_use_context: Any = None,
) -> list[dict[str, Any]]:
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event(COMPACT_EVENT)

    results: list[dict[str, Any]] = []
    for hook in hooks:
        stdin_data = {
            "hook_event": COMPACT_EVENT,
            "session_id": session_id,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "trigger": trigger,
        }

        from .hook_executor import _dispatch_hook_by_type
        result = await _dispatch_hook_by_type(
            hook.config, stdin_data,
            tool_use_context=tool_use_context,
        )

        results.append({
            "hook": hook.config,
            "result": result,
        })

    return results
