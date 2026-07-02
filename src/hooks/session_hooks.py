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


async def _resolve_event_configs(
    event: HookEvent,
    registry: AsyncHookRegistry | None,
    tool_use_context: Any,
) -> list[HookConfig]:
    """ch12 round-4 (critic B1+M1) — resolve the HookConfigs to fire for a
    lifecycle event, TRUST-GATED and per-session-safe.

    Prefer the per-context SNAPSHOT (per-session, carries all scopes:
    user/project/local/policy) when a ``tool_use_context`` is available —
    NOT the process-global registry, which is cwd-independent and would
    cross-contaminate concurrent sessions (M1). Apply the workspace-trust
    filter so an UNtrusted workspace runs only ``is_policy`` hooks — without
    this, a malicious repo's ``.clawcodex/settings.json`` SessionStart
    command hook executed even when the user DECLINED the trust dialog
    (B1: arbitrary code execution). Mirrors the tool lane
    (hook_executor.py) and PostSampling (post_sampling_hooks.py).

    Falls back to the global registry (USER-scope only, cwd-independent)
    when no context is supplied — the test/back-compat path.
    """
    if tool_use_context is not None:
        from .hook_executor import _get_hooks_from_snapshot
        from .trust_gate import should_skip_hook_due_to_trust

        snapshot = _get_hooks_from_snapshot(tool_use_context)
        configs = list(snapshot.get(event, []))
        if should_skip_hook_due_to_trust(tool_use_context):
            configs = [c for c in configs if c.source.is_policy]
        return configs

    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event(event)
    return [h.config for h in hooks]


async def run_session_start_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    session_id: str | None = None,
    cwd: str | None = None,
    tool_use_context: Any = None,
) -> list[dict[str, Any]]:
    configs = await _resolve_event_configs(
        SESSION_START_EVENT, registry, tool_use_context,
    )
    results: list[dict[str, Any]] = []
    for config in configs:
        stdin_data = {
            "hook_event": SESSION_START_EVENT,
            "session_id": session_id,
            "cwd": cwd,
        }

        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook
        from .exec_prompt_hook import execute_prompt_hook

        if config.type == "command":
            result = await _execute_command_hook(config, stdin_data)
        elif config.type == "http":
            result = await execute_http_hook(config, stdin_data)
        elif config.type == "prompt":
            result = await execute_prompt_hook(config, stdin_data)
        else:
            continue

        results.append({"hook": config, "result": result})

    return results


async def run_session_end_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    session_id: str | None = None,
    total_cost: float | None = None,
    total_turns: int | None = None,
    tool_use_context: Any = None,
) -> list[dict[str, Any]]:
    configs = await _resolve_event_configs(
        SESSION_END_EVENT, registry, tool_use_context,
    )
    results: list[dict[str, Any]] = []
    for config in configs:
        stdin_data = {
            "hook_event": SESSION_END_EVENT,
            "session_id": session_id,
            "total_cost": total_cost,
            "total_turns": total_turns,
        }

        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook

        if config.type == "command":
            result = await _execute_command_hook(config, stdin_data)
        elif config.type == "http":
            result = await execute_http_hook(config, stdin_data)
        else:
            continue

        results.append({"hook": config, "result": result})

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
    configs = await _resolve_event_configs(
        COMPACT_EVENT, registry, tool_use_context,
    )
    results: list[dict[str, Any]] = []
    for config in configs:
        stdin_data = {
            "hook_event": COMPACT_EVENT,
            "session_id": session_id,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "trigger": trigger,
        }

        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook

        if config.type == "command":
            result = await _execute_command_hook(config, stdin_data)
        elif config.type == "http":
            result = await execute_http_hook(config, stdin_data)
        else:
            continue

        results.append({"hook": config, "result": result})

    return results
