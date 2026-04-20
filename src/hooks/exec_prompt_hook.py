from __future__ import annotations

import logging
import time
from typing import Any

from .hook_types import HookConfig, HookResult

logger = logging.getLogger(__name__)


async def execute_prompt_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
) -> HookResult:
    prompt_text = hook.prompt_text
    if not prompt_text:
        return HookResult(exit_code=0)

    start_time = time.monotonic()

    try:
        result = HookResult(
            exit_code=0,
            stdout=prompt_text,
            duration_ms=int((time.monotonic() - start_time) * 1000),
            additional_contexts=[prompt_text],
        )
        return result

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"Prompt hook error: {e}",
            exit_code=-1,
            duration_ms=duration_ms,
        )
