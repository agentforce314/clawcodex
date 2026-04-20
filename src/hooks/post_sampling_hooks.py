from __future__ import annotations

import logging
from typing import Any

from .hook_types import HookResult, PostSamplingHookInput
from .registry import AsyncHookRegistry, get_global_hook_registry

logger = logging.getLogger(__name__)


async def run_post_sampling_hooks(
    registry: AsyncHookRegistry | None = None,
    *,
    model: str = "",
    usage: dict[str, int] | None = None,
    stop_reason: str | None = None,
    response_content: Any = None,
) -> list[dict[str, Any]]:
    reg = registry or get_global_hook_registry()
    hooks = await reg.get_hooks_for_event("PostSampling")

    if not hooks:
        return []

    stdin_data: dict[str, Any] = {
        "hook_event": "PostSampling",
        "model": model,
        "usage": usage or {},
        "stop_reason": stop_reason,
    }

    results: list[dict[str, Any]] = []
    for hook in hooks:
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

        entry: dict[str, Any] = {
            "hook": hook.config,
            "result": result,
        }

        if result.additional_contexts:
            entry["injected_messages"] = result.additional_contexts

        results.append(entry)

    return results
