from __future__ import annotations

import json
import logging
import time
from typing import Any

from .hook_types import HookConfig, HookResult, AGENT_HOOK_TIMEOUT_MS

logger = logging.getLogger(__name__)

AGENT_HOOK_SYSTEM_PROMPT = """\
You are a hook evaluator. You receive tool use events and must decide whether to allow, deny, or modify them.

Your response MUST be a valid JSON object with the following structure:
{
  "decision": "allow" | "deny" | "ask",
  "reason": "string explaining the decision",
  "updatedInput": null or object with modified input
}

Do NOT include any text before or after the JSON object.
"""


async def execute_agent_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    *,
    provider: Any = None,
    model: str | None = None,
    timeout_ms: int = AGENT_HOOK_TIMEOUT_MS,
) -> HookResult:
    instructions = hook.agent_instructions
    if not instructions:
        return HookResult(blocking_error="Agent hook has no instructions configured")

    start_time = time.monotonic()

    if provider is None:
        return HookResult(
            blocking_error="Agent hook requires a provider but none was provided",
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )

    try:
        event_description = json.dumps(stdin_data, default=str, indent=2)

        user_prompt = f"""\
Hook instructions:
{instructions}

Event data:
{event_description}

Evaluate this event and respond with a JSON decision."""

        messages = [{"role": "user", "content": user_prompt}]
        system_prompt = AGENT_HOOK_SYSTEM_PROMPT

        effective_model = model or "claude-sonnet-4-20250514"

        if hasattr(provider, "chat_async"):
            response = await provider.chat_async(
                messages=messages,
                tools=None,
                model=effective_model,
                max_tokens=1024,
                system=system_prompt,
            )
        elif hasattr(provider, "chat"):
            response = provider.chat(
                messages=messages,
                tools=None,
                model=effective_model,
                max_tokens=1024,
                system=system_prompt,
            )
        else:
            return HookResult(
                blocking_error="Provider does not support chat",
                exit_code=-1,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

        duration_ms = int((time.monotonic() - start_time) * 1000)
        response_text = response.content.strip() if hasattr(response, "content") and response.content else ""

        result = HookResult(
            exit_code=0,
            stdout=response_text,
            duration_ms=duration_ms,
        )

        if response_text:
            try:
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    parsed = json.loads(json_str)
                    if isinstance(parsed, dict):
                        decision = parsed.get("decision")
                        if decision in ("allow", "deny", "ask"):
                            result.permission_behavior = decision
                            result.hook_permission_decision_reason = parsed.get("reason")
                        if parsed.get("updatedInput"):
                            result.updated_input = parsed["updatedInput"]
                        if parsed.get("preventContinuation"):
                            result.prevent_continuation = True
                            result.stop_reason = parsed.get("stopReason")
            except (json.JSONDecodeError, ValueError):
                logger.warning("Agent hook returned non-JSON response: %s", response_text[:200])

        return result

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"Agent hook error: {e}",
            exit_code=-1,
            duration_ms=duration_ms,
        )
