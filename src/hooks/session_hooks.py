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


USER_PROMPT_SUBMIT_EVENT: HookEvent = "UserPromptSubmit"


MAX_HOOK_OUTPUT_LENGTH = 10000  # TS processUserInput.ts:272-277 per-context cap


class UserPromptSubmitOutcome:
    """ch14 round-4 — the collected result of the UserPromptSubmit hooks.

    Unlike the lifecycle routers (fire-and-forget), UserPromptSubmit's whole
    value is the OUTCOME. Mirrors ``processUserInput.ts:194-262``, which
    distinguishes two stop modes:

    - ``blocked`` (a ``blockingError``) → ERASE the prompt + emit a system
      warning; the model never sees the prompt.
    - ``prevented`` (``preventContinuation``) → KEEP the prompt in context +
      push an "Operation stopped by hook" note; no query runs this turn.

    plus ``additional_contexts`` → extra model-visible context (each capped
    at MAX_HOOK_OUTPUT_LENGTH).
    """

    __slots__ = ("blocked", "block_message", "prevented",
                 "prevent_reason", "additional_contexts")

    def __init__(self) -> None:
        self.blocked: bool = False
        self.block_message: str | None = None
        self.prevented: bool = False
        self.prevent_reason: str | None = None
        self.additional_contexts: list[str] = []

    @property
    def stop(self) -> bool:
        """True when either stop mode fired → skip the query this turn."""
        return self.blocked or self.prevented


async def run_user_prompt_submit_hooks(
    prompt: str,
    *,
    registry: AsyncHookRegistry | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    tool_use_context: Any = None,
) -> UserPromptSubmitOutcome:
    """ch14 round-4 — fire UserPromptSubmit hooks on a raw user prompt.

    Trust-gated (via _resolve_event_configs → the per-context snapshot +
    should_skip_hook_due_to_trust) exactly like the other snapshot-lane
    hooks (ch12): an untrusted workspace runs only ``is_policy`` hooks.
    Returns the collected block/inject outcome. Never raises.
    """
    outcome = UserPromptSubmitOutcome()
    try:
        configs = await _resolve_event_configs(
            USER_PROMPT_SUBMIT_EVENT, registry, tool_use_context,
        )
    except Exception:  # noqa: BLE001 — hook resolution must not block a turn
        return outcome

    for config in configs:
        stdin_data = {
            "hook_event": USER_PROMPT_SUBMIT_EVENT,
            "prompt": prompt,  # Claude Code contract field name (NOT user_message)
            "session_id": session_id,
            "cwd": cwd,
        }
        from .hook_executor import _execute_command_hook
        from .exec_http_hook import execute_http_hook
        from .exec_prompt_hook import execute_prompt_hook

        try:
            if config.type == "command":
                result = await _execute_command_hook(config, stdin_data)
            elif config.type == "http":
                result = await execute_http_hook(config, stdin_data)
            elif config.type == "prompt":
                result = await execute_prompt_hook(config, stdin_data)
            else:
                continue
        except Exception:  # noqa: BLE001 — one bad hook must not block the turn
            logger.debug("UserPromptSubmit hook failed", exc_info=True)
            continue

        # blockingError (exit-2) → erase the prompt + warn. preventContinuation
        # → keep the prompt + "Operation stopped by hook". blockingError wins
        # over preventContinuation (TS checks it first). Either stop mode
        # short-circuits: TS returns at the first blocker, so we stop running
        # further hooks (their subprocesses would side-effect for nothing —
        # agent_server discards anything collected past a stop).
        block = getattr(result, "blocking_error", None)
        if block:
            outcome.blocked = True
            outcome.block_message = str(block)
            break
        if getattr(result, "prevent_continuation", False):
            outcome.prevented = True
            outcome.prevent_reason = (
                getattr(result, "stop_reason", None) or "blocked by hook"
            )
            break
        # additionalContext → injected as extra model-visible context, each
        # capped at MAX_HOOK_OUTPUT_LENGTH (TS processUserInput.ts:272-277).
        extra = getattr(result, "additional_contexts", None)
        if extra:
            outcome.additional_contexts.extend(
                str(c)[:MAX_HOOK_OUTPUT_LENGTH] for c in extra
            )

    return outcome


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
