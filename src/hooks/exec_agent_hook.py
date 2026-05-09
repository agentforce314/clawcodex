"""Agent hooks — multi-turn LLM evaluators with structured output.

Phase-7 / WI-7.2. Mirrors TS ``execAgentHook.ts:36-339`` (chapter
§"Agent Hooks").

**Multi-turn semantic.** The hook agent iterates an LLM dialogue (no
external tool use; this is a "single-skill validator" loop, not a full
agentic run with run_agent). Each turn:

  1. The agent's response is checked for valid structured output —
     a JSON object matching ``HookOutput`` (Phase-1 / WI-1.4 schema).
  2. If valid → done; extract decision and return.
  3. If invalid → append to conversation history with a "respond with
     valid JSON only" reminder, ask again.
  4. Cap at ``max_turns`` (default 50 per chapter; configurable via
     ``hook.timeout`` interpreted as seconds-to-turns).

The 50-turn cap matches the chapter's stated limit. Without a cap, a
misbehaving LLM could spin indefinitely.

**dontAsk semantics.** Agent hooks operate without tool use in this
implementation; ``dontAsk`` is irrelevant for a single-skill validator
loop. The kwarg is accepted for forward-compat with a future full
run_agent integration that DOES use tools (where dontAsk would suppress
permission prompts on the sub-agent's tool calls).

**Structured output validation.** Final assistant response MUST be
valid JSON satisfying ``HookOutput`` (decision/reason/updatedInput/
preventContinuation/etc.). The Pydantic schema rejects unknown fields
and enforces the literal decision values — same schema the executor
uses for command-hook stdout JSON parsing (Phase 1 / WI-1.4).

**Provider injection.** Same pattern as exec_prompt_hook + the
pre-Phase-7 single-call exec_agent_hook: provider + model as kwargs.
Tests inject mocks; production wires the session's provider through
the hook-dispatch path.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .hook_types import AGENT_HOOK_TIMEOUT_MS, HookConfig, HookResult
from .output_schema import HookOutput, parse_hook_output

logger = logging.getLogger(__name__)


# Chapter §"Agent Hooks" — 50-turn cap. Configurable via hook.timeout.
DEFAULT_MAX_TURNS = 50


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


# A reminder we append between turns when the agent's previous output
# failed schema validation. Steers the next response toward a valid
# JSON shape without restarting the conversation.
_RETRY_REMINDER = (
    "Your previous response was not valid JSON matching the required "
    "schema. Please respond with ONLY a JSON object of the form "
    '{"decision": "allow"|"deny"|"ask", "reason": "...", '
    '"updatedInput": null or object}. No prose before or after.'
)


def _extract_json_object(text: str) -> str | None:
    """Extract the first balanced top-level JSON object from ``text``.

    Mirrors the pre-Phase-7 fallback behavior where the agent might
    wrap JSON in prose. We look for the first ``{`` and find the
    matching ``}``. If no balanced object exists, return None.
    """
    start = text.find("{")
    if start < 0:
        return None
    # Find balanced close brace, accounting for strings.
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _hook_output_to_result(parsed: HookOutput, response_text: str) -> HookResult:
    """Convert a parsed HookOutput into a HookResult."""
    result = HookResult(
        exit_code=0,
        stdout=response_text,
    )
    if parsed.decision is not None:
        result.permission_behavior = parsed.decision
        result.hook_permission_decision_reason = parsed.reason
    if parsed.updatedInput:
        result.updated_input = parsed.updatedInput
    if parsed.preventContinuation:
        result.prevent_continuation = True
        result.stop_reason = parsed.stopReason
    if parsed.additionalContexts:
        result.additional_contexts = parsed.additionalContexts
    if parsed.updatedMCPToolOutput is not None:
        result.updated_mcp_tool_output = parsed.updatedMCPToolOutput
    return result


async def _call_provider(
    provider: Any,
    messages: list[dict[str, Any]],
    *,
    model: str,
    system: str,
) -> Any:
    """Wrapper for provider.chat_async / chat. Hides the sync/async
    capability check from the multi-turn loop."""
    if hasattr(provider, "chat_async"):
        return await provider.chat_async(
            messages=messages,
            tools=None,
            model=model,
            max_tokens=1024,
            system=system,
        )
    if hasattr(provider, "chat"):
        return provider.chat(
            messages=messages,
            tools=None,
            model=model,
            max_tokens=1024,
            system=system,
        )
    raise AttributeError("Provider does not support chat")


async def execute_agent_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    *,
    provider: Any = None,
    model: str | None = None,
    timeout_ms: int = AGENT_HOOK_TIMEOUT_MS,
    max_turns: int = DEFAULT_MAX_TURNS,
    dont_ask: bool = True,
) -> HookResult:
    """Run an agent hook: multi-turn LLM dialogue with structured output.

    Returns a ``HookResult`` whose ``permission_behavior`` is the agent's
    final decision (allow/deny/ask). On schema violation across all
    turns, ``blocking_error`` carries an explanation and the LLM
    transcript is preserved in ``stdout``.
    """
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

    # Build the initial conversation: a single user message carrying
    # the hook's instructions + the event JSON. The agent must respond
    # with valid HookOutput JSON.
    event_description = json.dumps(stdin_data, default=str, indent=2)
    initial_user_prompt = (
        f"Hook instructions:\n{instructions}\n\n"
        f"Event data:\n{event_description}\n\n"
        "Evaluate this event and respond with a JSON decision."
    )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": initial_user_prompt},
    ]
    effective_model = model or "claude-sonnet-4-20250514"

    last_response_text = ""
    last_validation_error: str | None = None

    # Multi-turn loop. We iterate up to ``max_turns`` times; each turn
    # the agent produces a response, we validate it as HookOutput, and
    # if it's not valid we append a reminder and continue.
    for turn in range(max_turns):
        try:
            response = await _call_provider(
                provider, messages,
                model=effective_model,
                system=AGENT_HOOK_SYSTEM_PROMPT,
            )
        except AttributeError as exc:
            return HookResult(
                blocking_error=f"Agent hook provider error: {exc}",
                exit_code=-1,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )
        except Exception as exc:
            return HookResult(
                blocking_error=f"Agent hook error: {exc}",
                exit_code=-1,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

        response_text = ""
        if hasattr(response, "content") and response.content:
            response_text = (
                response.content.strip()
                if isinstance(response.content, str)
                else str(response.content).strip()
            )
        last_response_text = response_text

        # Try direct schema parse first.
        parsed, err = parse_hook_output(response_text)
        if err is None and parsed is not None:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = _hook_output_to_result(parsed, response_text)
            result.duration_ms = duration_ms
            return result

        # Direct parse failed — try extracting an embedded object (the
        # agent may have wrapped JSON in prose; mirrors pre-Phase-7
        # fallback behavior).
        extracted = _extract_json_object(response_text)
        if extracted is not None:
            parsed, err = parse_hook_output(extracted)
            if err is None and parsed is not None:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                result = _hook_output_to_result(parsed, response_text)
                result.duration_ms = duration_ms
                return result

        # Both attempts failed. Record the validation error, append a
        # reminder, and try again on the next turn.
        last_validation_error = err
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content": _RETRY_REMINDER})

    # Max turns exhausted without producing valid structured output.
    # Surface as blocking_error so the parent agent doesn't treat the
    # garbage as an "allow."
    duration_ms = int((time.monotonic() - start_time) * 1000)
    return HookResult(
        blocking_error=(
            f"Agent hook did not produce valid structured output after "
            f"{max_turns} turn(s). Last validation error: "
            f"{last_validation_error or 'no valid JSON detected'}"
        ),
        exit_code=-1,
        stdout=last_response_text,
        duration_ms=duration_ms,
    )
