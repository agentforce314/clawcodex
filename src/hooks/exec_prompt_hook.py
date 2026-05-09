"""Prompt hooks — single-call LLM evaluators.

Phase-7 / WI-7.1. Mirrors TS ``execPromptHook`` (chapter §"Prompt Hooks").

Pre-Phase-7, ``execute_prompt_hook`` was a stub that returned the
configured ``prompt_text`` as ``additional_context`` directly — no LLM
call (gap analysis #20). The chapter's intended behavior is:

  1. The hook config supplies a prompt template (``prompt_text``).
  2. The executor renders the template with placeholders from the hook
     event's ``stdin_data`` — e.g., ``{tool_name}`` becomes the actual
     tool name; ``{tool_input}`` becomes a JSON dump of the tool input.
  3. The executor calls the configured LLM with the rendered prompt.
  4. The LLM's response surfaces as ``additional_context`` so the parent
     agent loop sees it as augmenting context for the in-flight tool
     call.

**Provider injection.** Like ``execute_agent_hook``, this function
takes ``provider`` + ``model`` as kwargs. Production callers (the
executor's prompt-hook dispatch path; lifecycle routers) thread
``tool_use_context.provider`` or equivalent through. Tests inject mock
providers.

**No provider → blocking_error.** Pre-Phase-7 the stub silently echoed
``prompt_text``; that's the wrong default since prompt hooks are
defined to make an LLM call. Returning a blocking_error makes the
configuration mistake visible.

**Template rendering.** Simple ``str.format``-style substitution of
field names from ``stdin_data``. Unknown fields render as empty
strings (forgiving) rather than raising — a hook author who references
``{tool_name}`` in a Stop hook (which has no tool_name) shouldn't blow
up.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .hook_types import HookConfig, HookResult

logger = logging.getLogger(__name__)


def _render_prompt_template(template: str, stdin_data: dict[str, Any]) -> str:
    """Substitute ``{key}`` placeholders in ``template`` from
    ``stdin_data``. Unknown keys render as empty strings.

    Mirrors TS' template rendering for prompt hooks. For values that
    aren't strings (e.g., ``tool_input`` dict), serialize to JSON so the
    rendered prompt has a coherent representation.
    """
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    safe_values: dict[str, Any] = {}
    for k, v in stdin_data.items():
        if isinstance(v, str):
            safe_values[k] = v
        else:
            try:
                safe_values[k] = json.dumps(v, default=str)
            except (TypeError, ValueError):
                safe_values[k] = str(v)

    try:
        return template.format_map(_SafeDict(safe_values))
    except (KeyError, IndexError, ValueError) as exc:
        # Defensive: if format_map blows up on a malformed template,
        # fall back to the raw template (better than crashing the hook
        # call). Logged so authors notice.
        logger.warning("prompt template render failed: %s", exc)
        return template


async def execute_prompt_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    *,
    provider: Any = None,
    model: str | None = None,
) -> HookResult:
    """Run a prompt hook: render template + call LLM + return response
    as ``additional_context``.

    Empty / unset ``prompt_text`` → no-op success (hook author chose to
    register a no-op). Configured ``prompt_text`` but no ``provider`` →
    blocking_error: prompt hooks are defined to make an LLM call.
    """
    prompt_text = hook.prompt_text
    if not prompt_text:
        return HookResult(exit_code=0)

    start_time = time.monotonic()

    if provider is None:
        # Bootstrap wires ``ToolContext.provider`` at session start
        # (Phase-7 follow-up D5). If a hook still hits this path, it
        # means either the dispatch helper called us without a context
        # (test fixture) OR the bootstrap path didn't populate the
        # field (configuration mistake).
        return HookResult(
            blocking_error=(
                "Prompt hook requires a provider on ToolContext but none "
                "was found. Either configure the provider at session start "
                "or pass it explicitly to execute_prompt_hook."
            ),
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )

    rendered = _render_prompt_template(prompt_text, stdin_data)
    messages = [{"role": "user", "content": rendered}]
    effective_model = model or "claude-sonnet-4-20250514"

    try:
        if hasattr(provider, "chat_async"):
            response = await provider.chat_async(
                messages=messages,
                tools=None,
                model=effective_model,
                max_tokens=1024,
            )
        elif hasattr(provider, "chat"):
            response = provider.chat(
                messages=messages,
                tools=None,
                model=effective_model,
                max_tokens=1024,
            )
        else:
            return HookResult(
                blocking_error="Provider does not support chat",
                exit_code=-1,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )
    except Exception as exc:
        return HookResult(
            blocking_error=f"Prompt hook LLM call failed: {exc}",
            exit_code=-1,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)
    response_text = ""
    if hasattr(response, "content") and response.content:
        response_text = response.content.strip() if isinstance(response.content, str) else str(response.content).strip()

    if not response_text:
        # Empty LLM response: still succeed (the hook ran), but don't
        # add empty additional_contexts.
        return HookResult(exit_code=0, duration_ms=duration_ms)

    return HookResult(
        exit_code=0,
        stdout=response_text,
        duration_ms=duration_ms,
        additional_contexts=[response_text],
    )
