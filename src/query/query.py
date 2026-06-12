from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable
from uuid import uuid4

from ..types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
    create_assistant_api_error_message,
)
from ..types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from ..tool_system.build_tool import Tool, Tools, find_tool_by_name
from ..tool_system.context import ToolContext
from ..tool_system.protocol import ToolCall, ToolResult
from ..tool_system.registry import ToolRegistry
from ..utils.abort_controller import AbortController, AbortError
from ..utils.image_validation import ImageSizeError
from ..providers.base import BaseProvider, ChatResponse

from .config import QueryConfig, build_query_config
from .continuation_nudge import (
    MAX_CONTINUATION_NUDGES,
    NUDGE_MESSAGE,
    detect_continuation_signal,
)
from .stop_hooks import StopHookResult, handle_stop_hooks_streaming
from .token_budget import (
    ContinueDecision,
    check_token_budget,
    create_budget_tracker,
)
from .tool_failure_loop_guard import (
    create_tool_failure_loop_guard_state,
    update_tool_failure_loop_guard,
)
from .transitions import (
    QueryState,
    Terminal,
    TerminalHolder,
    Transition,
    set_terminal,
)
from ..services.compact.pipeline import (
    CompressionPipeline,
    PipelineConfig,
    run_compression_pipeline,
)
from ..token_estimation import rough_token_count_estimation_for_messages

logger = logging.getLogger(__name__)

ESCALATED_MAX_TOKENS = 64_000

# ch04 round-3 G3 — 529/overloaded retry lane. TS withRetry.ts: general
# budget DEFAULT_MAX_RETRIES=10 with a 529-specific MAX_529_RETRIES=3
# counter that triggers model-fallback or the external-user bail
# (:346-385); the port adopts the bail posture (3 retries then the
# existing model_error path). Gated to foreground sources only
# (withRetry.ts:62-90) -- background lanes (compact, session_memory)
# bail immediately to avoid gateway amplification.
MAX_529_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 0.5
# Narrower than TS's FOREGROUND_529_RETRY_SOURCES (agents/sdk/compact/
# side_question, withRetry.ts:62-90) -- minimal posture; widen to agent
# sources when subagent traffic matters.
FOREGROUND_529_RETRY_SOURCES = frozenset({"repl_main_thread"})
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
PROMPT_TOO_LONG_ERROR_MESSAGE = (
    "Your conversation is too long. Please use /compact to reduce context size, "
    "or start a new conversation."
)


@dataclass
class QueryParams:
    messages: list[Message]
    # WI-1.1: ``system_prompt`` accepts either the legacy ``str`` shape
    # (joined sections, no cache_control markers) OR the block-list shape
    # ``list[dict]`` produced by ``build_full_system_prompt_blocks``. The
    # block-list shape is what engages Anthropic's prompt cache via
    # ``cache_control: {type: 'ephemeral'}`` markers; the str shape is
    # retained for backward compat with callers that pass a custom prompt.
    system_prompt: str | list[dict[str, Any]]
    tools: Tools
    tool_registry: ToolRegistry
    tool_use_context: ToolContext
    provider: BaseProvider
    abort_controller: AbortController
    query_source: str = "repl_main_thread"
    # ch05 round-3 G2: the +500k turn budget. Deliberate deviation from
    # TS's ambient bootstrap-global design — params is the carrier; the
    # bootstrap globals remain the mechanism (snapshot at query() entry).
    token_budget: int | None = None
    max_output_tokens_override: int | None = None
    max_turns: int | None = None
    user_context: dict[str, str] | None = None
    system_context: dict[str, str] | None = None
    pipeline_config: PipelineConfig | None = None
    # Ch5/F-followup: live streaming text callback. When set, the
    # provider's chat_stream_response receives this callback so each
    # SSE text-delta drives the UI in real time. Critical for the
    # TUI/headless live-stream UX after the F.2/F.3 migration to this
    # loop — without it, callers see the entire response materialize
    # at once after the model turn completes. The callback can also
    # raise AbortError from inside the SDK's stream context to tear
    # down the HTTP socket on ESC.
    on_text_chunk: Callable[[str], None] | None = None

    # Extended thinking ("adaptive" mode) — opt the model into a private
    # reasoning scratchpad before producing its visible answer. Defaults
    # to ``None`` which auto-enables on Anthropic Claude 4.x models
    # (the only family the API supports it on) and stays off elsewhere.
    # Pass ``False`` to force-disable (e.g. for determinism in tests).
    # Mirrors the TS reference which always passes
    # ``thinking: {type: "adaptive"}`` on these models.
    extended_thinking: bool | None = None
    # Output-effort hint forwarded as ``output_config.effort``. Anthropic
    # accepts ``"low" | "medium" | "high"``. Only sent when extended
    # thinking is active.
    thinking_effort: str = "medium"


@dataclass
class StreamEvent:
    type: str
    data: Any = None


def _is_prompt_too_long_message(msg: Message) -> bool:
    if not isinstance(msg, AssistantMessage):
        return False
    if not hasattr(msg, "_api_error"):
        return False
    return getattr(msg, "_api_error", None) == "prompt_too_long"


def _create_user_message(content: str, *, is_meta: bool = False) -> UserMessage:
    return UserMessage(
        content=content,
        isMeta=is_meta,
    )


def _create_assistant_api_error_message(
    content: str,
    *,
    error: str | None = None,
) -> AssistantMessage:
    msg = AssistantMessage(content=content, isApiErrorMessage=True)
    msg._api_error = error  # type: ignore[attr-defined]
    return msg


def _create_user_interruption_message(*, tool_use: bool = False) -> UserMessage:
    from ..types.messages import INTERRUPT_MESSAGE, INTERRUPT_MESSAGE_FOR_TOOL_USE
    content = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return UserMessage(content=content, isMeta=True)


def _create_max_turns_attachment(max_turns: int, turn_count: int) -> SystemMessage:
    return SystemMessage(
        content=f"Reached maximum number of turns ({max_turns})",
        subtype="max_turns_reached",
    )


def _drain_pending_user_messages(tool_use_context: Any) -> list[UserMessage]:
    """Drain the running agent's ``pending_messages`` inbox, if any.

    Chapter-10 / Chunk D / WI-3.3 hook. The TS implementation drains at
    the tool-round boundary inside the agent's run loop; the Python
    equivalent is here, between `tool_results` and the next API call,
    where the chapter's "messages arrive between tool rounds, not
    mid-execution" contract holds.

    No-op when:
    * The context has no ``agent_id`` (top-level / non-runtime-task agents).
    * The context has no ``runtime_tasks`` registry (test fixtures
      that didn't construct a real ToolContext).
    * The agent's entry isn't a ``LocalAgentTaskState`` (defensive —
      a future task type that runs through the same query loop).
    * The inbox is empty.

    Returns the drained messages as a list of fresh ``UserMessage``
    objects, which the caller appends to the next turn's prompt.
    """
    agent_id = getattr(tool_use_context, "agent_id", None)
    runtime = getattr(tool_use_context, "runtime_tasks", None)
    if not agent_id or runtime is None:
        return []
    # Local import to avoid pulling the tasks package into the query
    # module's import graph at startup; this hook only fires when an
    # agent_id is set, so the tasks module will already be loaded.
    try:
        from src.tasks.local_agent import (
            LocalAgentTaskState,
            drain_pending_messages,
        )
    except ImportError:
        return []
    state = runtime.get(agent_id)
    if not isinstance(state, LocalAgentTaskState):
        return []
    drained = drain_pending_messages(agent_id, runtime)
    if not drained:
        return []
    return [UserMessage(content=text) for text in drained]


def _yield_missing_tool_result_blocks(
    assistant_messages: list[AssistantMessage],
    error_message: str,
) -> list[UserMessage]:
    results: list[UserMessage] = []
    for assistant_msg in assistant_messages:
        content = assistant_msg.content
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ToolUseBlock):
                results.append(
                    UserMessage(
                        content=[
                            ToolResultBlock(
                                tool_use_id=block.id,
                                content=error_message,
                                is_error=True,
                            )
                        ],
                    )
                )
    return results


def _get_context_window(provider: BaseProvider) -> int:
    """Ch5/B.4 — return the model's context-window size in tokens.

    Defaults to 200_000 (the Claude family default) when the provider
    doesn't expose a real integer ``context_window`` attribute.

    The ``isinstance(_, int)`` guard handles MagicMock test fixtures:
    a MagicMock returns another mock for unset attributes, so a naive
    ``getattr(..., None) or default`` would short-circuit past the
    default. Be explicit about the type so the loop is robust to test
    doubles.
    """
    cw = getattr(provider, "context_window", None)
    if isinstance(cw, int) and cw > 0:
        return cw
    return 200_000


def _is_withheld_max_output_tokens(msg: Message | None) -> bool:
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "max_output_tokens"


def _is_withheld_prompt_too_long(msg: Message | None) -> bool:
    """Ch5/B.1 — mirrors TS ``isWithheldPromptTooLong`` at query.ts:877.

    Returns True for an assistant message that carries an
    ``_api_error == "prompt_too_long"`` tag. The query loop checks this
    after streaming to suppress the message from the yield stream until
    PTL recovery (reactive_compact) has been attempted (B.2).
    """
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "prompt_too_long"


def _is_withheld_media_size(msg: Message | None) -> bool:
    """Ch5/B.1 — mirrors TS ``isWithheldMediaSizeError`` at query.ts:892.

    Returns True for an assistant message that carries an
    ``_api_error == "media_size"`` tag.
    """
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "media_size"


def _is_hook_stopped_continuation(msg: Message | None) -> bool:
    """Ch5/round2 — mirrors TS attachment scan at query.ts:1540-1545.

    Returns True for an ``AttachmentMessage`` whose ``attachments`` list
    contains an attachment with ``type == 'hook_stopped_continuation'``.
    The query loop checks this after the tool batch completes; if any
    tool_result carries the marker, the loop exits with
    ``Terminal(reason='hook_stopped')`` instead of advancing to the
    next turn — mirroring TS at query.ts:1698-1701.

    The attachment is produced by ``PreToolUse`` / ``PostToolUse`` hooks
    that set ``prevent_continuation`` — see
    ``src/services/tool_execution/tool_execution.py:362-372`` and
    ``src/services/tool_execution/tool_hooks.py:185-195``. The current
    production dispatch path (``_run_tools_partitioned`` → registry
    dispatch) does NOT route through those producers — that wiring is
    the C7 architectural-unification gap, deferred. Landing the terminal
    mapping here means the contract is in place the moment any future
    change wires hook-aware dispatch into the loop.

    Local import of ``AttachmentMessage`` matches the pattern at
    ``_drain_pending_user_messages`` — keeps the top-level import list
    lean and avoids a circular-import risk with future ``types.messages``
    refactors.
    """
    if msg is None:
        return False
    from ..types.messages import AttachmentMessage
    if not isinstance(msg, AttachmentMessage):
        return False
    attachments = getattr(msg, "attachments", None) or []
    for att in attachments:
        if isinstance(att, dict) and att.get("type") == "hook_stopped_continuation":
            return True
    return False


_THINKING_ELIGIBLE_MODEL_PATTERN = re.compile(
    r"claude-(?:sonnet|opus|haiku)-(?:4-\d+|[5-9]\b|\d{2,})",
    re.IGNORECASE,
)


def _model_supports_extended_thinking(model: str | None) -> bool:
    """True iff the model is on the Anthropic Claude 4.x or newer family.

    Extended thinking (``thinking={"type": "adaptive"}``) was introduced
    with the Claude 4 series — the Anthropic API rejects the parameter
    on 3.x and earlier. Detection is by name pattern so unreleased model
    snapshots (e.g. ``claude-opus-4-7-20260201``) opt in automatically.
    """
    if not model:
        return False
    return bool(_THINKING_ELIGIBLE_MODEL_PATTERN.search(model))


def _is_overloaded_error(e: Exception) -> bool:
    """Anthropic 529 / overloaded_error classification (duck-typed so
    test fakes and other providers' shapes participate)."""
    status = getattr(e, "status_code", None)
    if status == 529:
        return True
    text = str(e).lower()
    return "overloaded_error" in text or "overloaded" in text


def _retry_after_seconds(e: Exception, default: float) -> float:
    """Honor a Retry-After header when the SDK exposes response headers."""
    response = getattr(e, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        raw = headers.get("retry-after")
        if raw:
            try:
                value = float(raw)
                if 0 < value <= 60:
                    return value
            except (TypeError, ValueError):
                pass
    return default


async def _fire_stop_failure_hooks(last_message: Any, tool_use_context: Any) -> None:
    """Dispatch StopFailure hooks at the error-exit paths (ch05 G1).

    TS fires fire-and-forget at query.ts:1256/:1263/:1347; the port
    awaits (terminal paths; latency bounded by the hook timeout) and
    never raises.
    """
    try:
        from ..hooks.hook_executor import execute_stop_failure_hooks

        async for _result in execute_stop_failure_hooks(
            last_message, tool_use_context
        ):
            pass
    except Exception:
        logger.debug("StopFailure hook dispatch failed", exc_info=True)


async def _call_model_sync(
    *,
    provider: BaseProvider,
    messages: list[Message],
    system_prompt: str,
    tools: Tools,
    max_output_tokens_override: int | None = None,
    abort_signal: Any = None,
    on_text_chunk: Callable[[str], None] | None = None,
    extended_thinking: bool | None = None,
    thinking_effort: str = "medium",
    sdk_max_retries: int | None = None,
) -> tuple[list[AssistantMessage], list[ToolUseBlock]]:
    from ..types.messages import normalize_messages_for_api
    from ..utils.advisor import (
        ADVISOR_BETA_HEADER,
        ADVISOR_MODE_CLIENT_SIDE,
        ADVISOR_MODE_INACTIVE,
        ADVISOR_MODE_SERVER_SIDE,
        ADVISOR_TOOL_INSTRUCTIONS,
        build_advisor_tool_schema,
        build_client_advisor_tool_schema,
        decide_advisor_mode,
        strip_advisor_blocks,
    )

    # Advisor activation decision. Three outcomes:
    #
    # * SERVER_SIDE: 1P Anthropic provider + opus-4-6/sonnet-4-6 main
    #   loop + valid server-side advisor target. Carries the beta
    #   header and the ``advisor_20260301`` schema; the API runs the
    #   reviewer model server-side. Optimal path — single roundtrip,
    #   cache-friendly.
    # * CLIENT_SIDE: any provider, any tool-calling main loop, any
    #   advisor model that routes to a known provider. Registers
    #   ``advisor`` as a regular client-side tool; the dispatcher
    #   (``src/tool_system/tools/advisor.py``) makes a separate API
    #   call to the configured advisor model. Two roundtrips but
    #   provider-agnostic.
    # * INACTIVE: no advisor on this request (env-disabled, no
    #   advisor_model set, or no path can be resolved).
    #
    # The full decision table lives in ``decide_advisor_mode``. Any
    # exception during the predicate degrades to INACTIVE rather than
    # killing the turn (critic M1 from the original advisor PR).
    main_loop_model = getattr(provider, "model", "") or ""
    advisor_mode = ADVISOR_MODE_INACTIVE
    advisor_model_normalized: str | None = None
    try:
        from ..settings.settings import get_settings
        settings = get_settings()
        configured = (getattr(settings, "advisor_model", "") or "").strip()
        configured_provider = (getattr(settings, "advisor_provider", "") or "").strip()
        force_client = bool(getattr(settings, "advisor_client_mode", False))
        if configured:
            from ..models.model import canonical_model_name
            candidate = canonical_model_name(configured)
            advisor_mode = decide_advisor_mode(
                provider,
                main_loop_model,
                candidate,
                force_client_mode=force_client,
                advisor_provider=configured_provider,
            )
            if advisor_mode != ADVISOR_MODE_INACTIVE:
                advisor_model_normalized = candidate
    except Exception:
        logger.exception(
            "Advisor activation check failed; treating advisor as inactive"
        )
        advisor_mode = ADVISOR_MODE_INACTIVE
        advisor_model_normalized = None

    api_messages = normalize_messages_for_api(messages)

    # Server-side advisor blocks (``server_tool_use(name=advisor)`` and
    # ``advisor_tool_result``) require the beta header on every request
    # that carries them — the API 400s otherwise. Strip from history on
    # any request that won't send the header.
    #
    # In CLIENT_SIDE mode the advisor surfaces as regular
    # ``tool_use``/``tool_result`` blocks, which pass through normal
    # message handling untouched. Only the SERVER_SIDE shape is gated
    # by the header, so stripping is keyed off "current request carries
    # the beta" — which is exactly SERVER_SIDE-and-only-SERVER_SIDE.
    if advisor_mode != ADVISOR_MODE_SERVER_SIDE:
        api_messages = strip_advisor_blocks(api_messages)

    # --- Diagnostic tracing ---
    _diag = os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes")
    if _diag:
        _total_chars = sum(
            len(m.get("content", "")) if isinstance(m.get("content"), str)
            else sum(len(str(b)) for b in m.get("content", []))
            for m in api_messages
        )
        if isinstance(system_prompt, str):
            sys_desc = f"{len(system_prompt)} chars"
        else:
            sys_total_chars = sum(
                len(blk.get("text", ""))
                for blk in system_prompt
                if isinstance(blk, dict)
            )
            sys_desc = f"{len(system_prompt)} blocks, {sys_total_chars} chars"
        logger.warning(
            "[DIAG] _call_model_sync: %d api_messages, ~%d chars, system_prompt=%s, %d tools",
            len(api_messages), _total_chars, sys_desc, len(list(tools)),
        )
        for i, m in enumerate(api_messages):
            role = m.get("role", "?")
            c = m.get("content", "")
            if isinstance(c, str):
                clen = len(c)
                logger.warning("[DIAG]   msg[%d] role=%s  content_len=%d  text=%s", i, role, clen, c[:80])
            else:
                block_types = []
                for b in c:
                    if isinstance(b, dict):
                        bt = b.get("type", "?")
                        if bt == "tool_use":
                            block_types.append(f"tool_use(id={b.get('id','')},name={b.get('name','')})")
                        elif bt == "tool_result":
                            block_types.append(f"tool_result(tool_use_id={b.get('tool_use_id','')})")
                        else:
                            block_types.append(bt)
                    else:
                        block_types.append(str(type(b).__name__))
                logger.warning("[DIAG]   msg[%d] role=%s  blocks=%s", i, role, block_types)
    _t0 = time.monotonic()
    tool_schemas = []
    for tool in tools:
        # Filter out internal/hidden tools (is_enabled=False) so they
        # don't leak into the API tools[] alongside the advisor schema
        # we append below. Some callers pass an unfiltered tool list
        # from ``registry.list_tools()``; this guard keeps the API
        # from receiving duplicate names. ``getattr`` with default
        # True keeps test fakes that don't implement is_enabled working.
        is_enabled_fn = getattr(tool, "is_enabled", None)
        if callable(is_enabled_fn) and not is_enabled_fn():
            continue
        tool_schemas.append({
            "name": tool.name,
            "description": tool.prompt(),
            "input_schema": dict(tool.input_schema),
        })

    # Append the advisor schema AFTER the regular tools so the
    # ``cache_control`` marker (which conventionally lives on the last
    # cached tool — the final entry in ``tool_schemas`` before this
    # append) stays in place. If we prepended or interleaved, toggling
    # /advisor would shift the marker and bust the prompt cache. Mirrors
    # TS claude.ts:1411-1421 explicitly.
    #
    # The schema shape differs by mode: server-side carries the dated
    # ``advisor_20260301`` discriminator + model field; client-side is
    # a regular tool_use schema with no params, routed through the
    # tool registry's AdvisorTool.
    if advisor_mode == ADVISOR_MODE_SERVER_SIDE:
        tool_schemas.append(build_advisor_tool_schema(advisor_model_normalized))
    elif advisor_mode == ADVISOR_MODE_CLIENT_SIDE:
        tool_schemas.append(build_client_advisor_tool_schema())

    call_kwargs: dict[str, Any] = {"tools": tool_schemas}

    if advisor_mode == ADVISOR_MODE_SERVER_SIDE:
        # Opt into the server-side advisor tool. ``betas`` lives outside
        # ``extra_headers`` because the SDK auto-converts it into the
        # ``anthropic-beta`` header AND filters out 3P-incompatible
        # entries on Bedrock/Vertex transports. Currently we send only
        # the advisor beta; if other betas are introduced, change this
        # to ``call_kwargs.setdefault("betas", []).append(...)``.
        call_kwargs["betas"] = [ADVISOR_BETA_HEADER]
        # CLIENT_SIDE deliberately does NOT set betas — 3P endpoints
        # reject the advisor beta, and 1P-with-force-client doesn't
        # need it because the advisor schema is a regular tool here.

    from ..providers.anthropic_provider import AnthropicProvider
    from ..providers.minimax_provider import MinimaxProvider

    is_anthropic = isinstance(provider, (AnthropicProvider, MinimaxProvider))
    advisor_instructions_active = advisor_mode != ADVISOR_MODE_INACTIVE
    if is_anthropic:
        # Forward whatever shape the engine produced — str or list[dict].
        # The SDK's ``system`` param accepts ``Union[str, Iterable[TextBlockParam]]``;
        # cache_control markers on blocks engage server-side prompt caching.
        #
        # When the advisor is active (server OR client side), append
        # ``ADVISOR_TOOL_INSTRUCTIONS`` AFTER the existing system prompt
        # blocks. Mirrors TS claude.ts:1395 — the advisor instructions
        # come AFTER the cached system blocks, so they land in the
        # request-scope partition and toggling /advisor doesn't churn
        # the cached prefix. The instruction text is provider-agnostic
        # (tells the model "use the advisor tool"), so it works for
        # both the server-side ``server_tool_use`` invocation and the
        # client-side regular ``tool_use`` invocation.
        if advisor_instructions_active:
            if isinstance(system_prompt, list):
                system_prompt = list(system_prompt) + [
                    {"type": "text", "text": ADVISOR_TOOL_INSTRUCTIONS}
                ]
            elif isinstance(system_prompt, str):
                system_prompt = (
                    f"{system_prompt}\n\n{ADVISOR_TOOL_INSTRUCTIONS}"
                    if system_prompt
                    else ADVISOR_TOOL_INSTRUCTIONS
                )
            else:
                # Defensive: the upstream contract is
                # ``str | list[dict[str, Any]]``. A future caller that
                # passes something else (e.g. None, a TextBlock object)
                # silently loses the instructions if we don't warn.
                logger.warning(
                    "Advisor active but system_prompt has unexpected type "
                    "%s — ADVISOR_TOOL_INSTRUCTIONS NOT injected",
                    type(system_prompt).__name__,
                )
        call_kwargs["system"] = system_prompt
    else:
        # Non-Anthropic providers (OpenAI-compat, GLM, etc.) consume the
        # system prompt as a single string injected as a ``system`` message.
        # Flatten the block-list shape to a string by concatenating block text;
        # cache_control markers don't apply to these providers anyway.
        #
        # Critically, FILTER OUT the dynamic-boundary marker block. The
        # literal ``__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`` is a cache-only
        # signal for the Anthropic backend; emitting it as raw text into
        # a non-Anthropic system prompt embeds an unintelligible token in
        # the prose that may confuse those models.
        if isinstance(system_prompt, list):
            from ..context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            flattened = "\n\n".join(
                str(blk.get("text", ""))
                for blk in system_prompt
                if isinstance(blk, dict)
                and blk.get("text")
                and blk.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            )
        else:
            flattened = system_prompt
        # CLIENT_SIDE on a 3P provider: append the advisor instructions
        # to the flattened system prompt so the model knows how + when
        # to invoke the ``advisor`` tool. (Server-side instructions
        # only land on 1P, handled by the is_anthropic branch above.)
        if advisor_instructions_active and advisor_mode == ADVISOR_MODE_CLIENT_SIDE:
            if flattened:
                flattened = f"{flattened}\n\n{ADVISOR_TOOL_INSTRUCTIONS}"
            else:
                flattened = ADVISOR_TOOL_INSTRUCTIONS
        api_messages = [{"role": "system", "content": flattened}, *api_messages]

    if is_anthropic and sdk_max_retries is not None:
        # ch04 round-3 G3(c): the loop's manual 529 lane passes 0 here so
        # SDK auto-retries don't stack under it; background loop sources
        # pass None and keep the SDK default (their silent resilience).
        call_kwargs["sdk_max_retries"] = sdk_max_retries

    if is_anthropic:
        # ch04 round-3 G0: resolve max_tokens on EVERY Anthropic request
        # (override → CLAUDE_CODE_MAX_OUTPUT_TOKENS env → per-model
        # table). Previously only the override branch set it and normal
        # requests silently went out at the provider-default 4096.
        # Non-Anthropic providers keep their override-only behavior —
        # they send NO max_tokens today (the provider-API default
        # applies) and capping them at the table default would be a
        # silent behavior change outside this gap's evidence.
        from ..models.context import resolve_max_output_tokens

        call_kwargs["max_tokens"] = resolve_max_output_tokens(
            max_output_tokens_override, getattr(provider, "model", None)
        )
    elif max_output_tokens_override is not None:
        call_kwargs["max_tokens"] = max_output_tokens_override

    # Extended thinking (Claude 4.x family). Forwarded straight through
    # the provider's kwargs pass-through to client.messages.stream(
    # thinking=..., output_config=...). Off-API on older Claude versions
    # and on non-Anthropic providers, so guarded by both. ``None`` =
    # auto-enable; ``True`` / ``False`` = caller override. Mirrors the
    # TS reference which sends ``thinking: {type: "adaptive"}`` and
    # ``output_config: {effort: ...}`` on every Claude 4.x request.
    if extended_thinking is not False and is_anthropic:
        provider_model = getattr(provider, "model", None) or call_kwargs.get("model")
        if extended_thinking is True or _model_supports_extended_thinking(provider_model):
            call_kwargs["thinking"] = {"type": "adaptive"}
            call_kwargs["output_config"] = {"effort": thinking_effort}

    # TS callModel() uses SSE streaming for faster first-byte latency and
    # progressive text display.  Use chat_stream_response() which streams
    # internally and reassembles the full ChatResponse.  Fall back to the
    # synchronous chat() if the provider doesn't support structured streaming.
    if _diag:
        logger.warning("[DIAG] _call_model_sync: calling provider (streaming)...")
    def _do_provider_call():
        # ``abort_signal`` reaches the provider so a tripped controller can close
        # the streaming HTTP response immediately. ``on_text_chunk`` (when set)
        # fires chunks live; falls back to a kwargless call if the provider's
        # signature doesn't accept it, and to plain ``chat()`` if the provider
        # has no streaming at all (emulating chunks from the final text).
        try:
            if on_text_chunk is not None:
                try:
                    return provider.chat_stream_response(
                        api_messages,
                        on_text_chunk=on_text_chunk,
                        abort_signal=abort_signal,
                        **call_kwargs,
                    )
                except TypeError:
                    return provider.chat_stream_response(
                        api_messages, abort_signal=abort_signal, **call_kwargs,
                    )
            return provider.chat_stream_response(
                api_messages, abort_signal=abort_signal, **call_kwargs,
            )
        except (NotImplementedError, AttributeError):
            if _diag:
                logger.warning("[DIAG] _call_model_sync: streaming not supported, falling back to chat()")
            resp = provider.chat(api_messages, **call_kwargs)
            if on_text_chunk is not None and resp.content:
                from ..tool_system.renderers import _emit_text_chunks
                _emit_text_chunks(on_text_chunk, resp.content)
            return resp

    try:
        # The provider call is a BLOCKING sync call (streaming read on a worker
        # thread + a poll loop). Running it directly on the event loop blocks it,
        # so concurrent agents (e.g. a workflow's parallel() fan-out) serialize —
        # one model call at a time. When there's no live-UI streaming callback
        # (background/workflow agents have on_text_chunk=None), run it OFF the
        # loop via to_thread so those agents truly run in parallel. The
        # interactive path keeps its callbacks firing on the event-loop thread.
        if on_text_chunk is None:
            response = await asyncio.to_thread(_do_provider_call)
        else:
            response = _do_provider_call()
    except AbortError:
        # User-initiated cancel — propagate so the query loop's
        # ``except AbortError: pass`` boundary unwinds to the
        # post-API abort-check block. We do NOT route this through
        # the error-message classification below: a future addition
        # to those substring checks could accidentally match an abort
        # reason and convert the cancel into a model-error reply.
        raise
    except ImageSizeError as e:
        # Client-side pre-API validation tripped (BaseProvider._prepare_messages).
        # Surface as a media_size error with the same classification the
        # server-side guard uses, so the reactive-compact recovery path
        # (Ch5/B.2) treats them identically.
        err_msg = _create_assistant_api_error_message(
            f"Media too large: {e}",
            error="media_size",
        )
        err_msg._api_error = "media_size"  # type: ignore[attr-defined]
        return [err_msg], []
    except Exception as e:
        if _diag:
            logger.warning("[DIAG] _call_model_sync: EXCEPTION after %.1fs: %s", time.monotonic() - _t0, e)
        error_str = str(e)
        if "prompt is too long" in error_str.lower() or "prompt_too_long" in error_str.lower():
            err_msg = _create_assistant_api_error_message(
                PROMPT_TOO_LONG_ERROR_MESSAGE,
                error="prompt_too_long",
            )
            err_msg._api_error = "prompt_too_long"  # type: ignore[attr-defined]
            return [err_msg], []

        if "max_tokens" in error_str.lower() or "max_output_tokens" in error_str.lower():
            err_msg = _create_assistant_api_error_message(
                "Output token limit reached.",
                error="max_output_tokens",
            )
            err_msg._api_error = "max_output_tokens"  # type: ignore[attr-defined]
            return [err_msg], []

        # Ch5/B.1 — tag media-size errors so the loop can withhold them
        # and (in B.2) route through reactive-compact recovery. Mirrors
        # TS `isWithheldMediaSizeError` at query.ts:892. `is_media_size_error`
        # expects a str (substring match), so pass error_str explicitly.
        from ..services.api.errors import is_media_size_error
        if is_media_size_error(error_str):
            err_msg = _create_assistant_api_error_message(
                f"Media too large: {error_str}",
                error="media_size",
            )
            err_msg._api_error = "media_size"  # type: ignore[attr-defined]
            return [err_msg], []

        # Model-capability rejection: the selected model has zero image
        # support, so the request can never succeed as long as the image
        # stays in conversation context. Tag the error so the engine
        # strips images from history (see QueryEngine.submit_message)
        # and surfaces a clear user-facing message instead of the raw
        # provider 404. The user-facing wording shape follows TS's
        # friendly error messages (e.g. getPdfInvalidErrorMessage at
        # typescript/src/services/api/errors.ts) but the case itself
        # is Python-new — TS has no dedicated handler for this
        # capability rejection, so don't grep TS for an analog branch.
        # See IMAGE_UNSUPPORTED_ERROR_MESSAGE in services/api/errors.py
        # for the longer rationale.
        from ..services.api.errors import (
            IMAGE_UNSUPPORTED_ERROR_MESSAGE,
            is_image_unsupported_error,
        )
        if is_image_unsupported_error(error_str):
            err_msg = _create_assistant_api_error_message(
                IMAGE_UNSUPPORTED_ERROR_MESSAGE,
                error="image_unsupported",
            )
            err_msg._api_error = "image_unsupported"  # type: ignore[attr-defined]
            # Preserve raw provider wording so a future bug report
            # ("the fix didn't work") has the actual 404 payload to
            # debug against, instead of just the friendly message.
            # Mirrors TS's ``errorDetails: error.message`` at
            # typescript/src/services/api/errors.ts:752.
            err_msg.errorDetails = error_str
            return [err_msg], []

        raise

    assistant_blocks: list[Any] = []
    tool_use_blocks: list[ToolUseBlock] = []

    if response.content:
        assistant_blocks.append(TextBlock(text=response.content))

    if response.tool_uses:
        for tu in response.tool_uses:
            block = ToolUseBlock(
                id=tu["id"],
                name=tu["name"],
                input=tu["input"],
            )
            assistant_blocks.append(block)
            tool_use_blocks.append(block)

    # Preserve advisor server-tool blocks as passthrough dicts so the
    # next turn can replay them to the API as a matched use/result pair.
    # ``normalize_messages_for_api`` round-trips dict blocks unchanged
    # (via ``content_block_to_dict``), and ``ensure_tool_result_pairing``
    # treats the advisor pair as a self-contained server-side
    # use/result on the assistant message (already paired in-message).
    # Stripping happens centrally in this function when ``advisor_active``
    # is False on a future turn.
    if response.raw_content_blocks:
        for raw in response.raw_content_blocks:
            assistant_blocks.append(dict(raw))

    stop_reason = response.finish_reason or "end_turn"

    if _diag:
        _elapsed = time.monotonic() - _t0
        _text_len = len(response.content) if response.content else 0
        _tool_count = len(response.tool_uses) if response.tool_uses else 0
        logger.warning(
            "[DIAG] _call_model_sync: response in %.1fs  text=%d chars  tools=%d  finish=%s  usage=%s",
            _elapsed, _text_len, _tool_count, stop_reason, response.usage,
        )

    # ch04 round-3 G1: the cost-accumulation head (TS addToTotalSessionCost,
    # claude.ts:2270-2275). Streaming and the watchdog chat() fallback
    # converge here, so every main-loop response is counted exactly once.
    # Empty usage (a stream whose final-message read failed) records zeros.
    try:
        from ..cost_tracker import record_api_usage

        record_api_usage(
            getattr(response, "model", None) or getattr(provider, "model", "unknown"),
            response.usage,
        )
    except Exception:
        logger.debug("cost recording failed", exc_info=True)

    assistant_msg = AssistantMessage(
        content=assistant_blocks if assistant_blocks else "",
        stop_reason=stop_reason,
        usage=response.usage,
    )
    if response.reasoning_content:
        # Preserve provider thinking metadata for follow-up turns.
        assistant_msg.reasoning_content = response.reasoning_content  # type: ignore[attr-defined]

    if stop_reason == "max_tokens":
        assistant_msg._api_error = "max_output_tokens"  # type: ignore[attr-defined]
        assistant_msg.isApiErrorMessage = False

    return [assistant_msg], tool_use_blocks


# Max tools to run in parallel (TS default: 10, configurable via env var).
# Reads CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY (the name documented in
# chapter 7 and used in the TS reference). Falls back to the legacy
# CLAWCODEX_MAX_TOOL_USE_CONCURRENCY with a DeprecationWarning so
# users learn to migrate.
def _resolve_max_tool_use_concurrency() -> int:
    canonical = os.environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY")
    if canonical is not None:
        return int(canonical)
    legacy = os.environ.get("CLAWCODEX_MAX_TOOL_USE_CONCURRENCY")
    if legacy is not None:
        import warnings
        warnings.warn(
            "CLAWCODEX_MAX_TOOL_USE_CONCURRENCY is deprecated; use "
            "CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return int(legacy)
    return 10


MAX_TOOL_USE_CONCURRENCY = _resolve_max_tool_use_concurrency()


@dataclass
class _ToolBatch:
    """A batch of tool_use blocks with the same concurrency classification."""
    is_concurrent_safe: bool
    blocks: list[ToolUseBlock]


def _partition_tool_calls(
    tool_use_blocks: list[ToolUseBlock],
    tools: Tools,
) -> list[_ToolBatch]:
    """Partition tool calls into batches per TS partitionToolCalls().

    Consecutive ConcurrencySafe tools are grouped for parallel execution.
    Non-safe tools each get their own exclusive batch.

    Mirrors TS: evaluates isConcurrencySafe per-call with actual tool input
    (not a static lookup), so e.g. read-only Bash commands can be parallel.
    """
    batches: list[_ToolBatch] = []
    for block in tool_use_blocks:
        tool = find_tool_by_name(tools, block.name)
        try:
            is_safe = bool(tool.is_concurrency_safe(block.input)) if tool else False
        except Exception:
            is_safe = False
        if batches and is_safe and batches[-1].is_concurrent_safe:
            batches[-1].blocks.append(block)
        else:
            batches.append(_ToolBatch(is_concurrent_safe=is_safe, blocks=[block]))
    return batches


def _is_user_cancelled_abort(tool_use_context: ToolContext) -> bool:
    """True iff the abort signal fired with a user-initiated reason.

    ``sibling_error`` is the streaming-executor's parallel-tool cascade
    and is NOT a user-rejected signal — surfacing REJECT_MESSAGE for it
    would mask the real underlying failure. Every other abort reason in
    the Python runtime (``user_interrupt`` from ESC, ``interrupt`` held
    in reserve for TS parity) is collapsed into the user-cancelled
    bucket here.

    Divergence vs TS: ``StreamingToolExecutor.ts:219-229`` treats
    ``'interrupt'`` (user typed mid-stream) and ``'user_interrupted'``
    (ESC) differently — for ``'interrupt'`` it only synthesizes
    REJECT_MESSAGE on tools whose ``interruptBehavior() === 'cancel'``.
    Python today emits neither ``'interrupt'`` nor any per-tool
    ``interrupt_behavior`` override on the production path, so the
    collapsed check is sound. If a future change wires up
    ``'interrupt'`` as a real reason, the per-tool gate must land first.
    """
    ctrl = tool_use_context.abort_controller
    if not ctrl.signal.aborted:
        return False
    return ctrl.signal.reason != "sibling_error"


def _build_user_cancelled_result(tool_use_id: str) -> UserMessage:
    """Synthetic tool_result returned when the user aborts mid-run.

    The bash tool's interrupted path emits
    ``<error>Command was aborted before completion</error>``, which the
    model reads as a generic command failure — on the next turn it tends
    to retry the command rather than honour the user's cancel. Replacing
    the tool_result with REJECT_MESSAGE makes the cancellation
    unambiguous. Mirrors
    ``typescript/src/services/tools/StreamingToolExecutor.ts:153-205``
    (``createSyntheticErrorMessage`` for ``user_interrupted``).
    """
    from ..types.messages import REJECT_MESSAGE
    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=REJECT_MESSAGE,
                is_error=True,
            )
        ],
    )


def _dispatch_single_tool(
    block: ToolUseBlock,
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools | None = None,
) -> tuple[UserMessage, list[UserMessage]]:
    """Dispatch a single tool and return ``(primary, extras)``.

    ``primary`` is always the tool_result UserMessage. ``extras`` is any
    supplemental ``new_messages`` the tool returned (e.g. the image
    dimensions metadata user message). Callers MUST emit all primaries
    before any extras across a multi-tool batch — otherwise tool_result
    pairing in ``ensure_tool_result_pairing`` mis-attributes the missing
    pair and injects a synthetic error placeholder.

    Routes through ``process_tool_result_block`` (mirrors TS Step 11 of
    the execution pipeline at ``processToolResultBlock``) so the per-tool
    persistence threshold AND the WI-5.1 per-message aggregate budget
    both engage on the production path. The running aggregate is held on
    ``tool_use_context.tool_result_chars_so_far`` (reset at the top of
    each per-turn loop in :func:`query`).
    """
    # Pre-tool gate: ESC may trip after the model picked this tool but
    # before we entered dispatch (e.g. between the post-streaming abort
    # check and the head of the partition loop). Hand back the
    # synthetic result instead of running the tool. Mirrors the initial-
    # abort branch in ``StreamingToolExecutor.collectResults``
    # (typescript/src/services/tools/StreamingToolExecutor.ts:278-292).
    if _is_user_cancelled_abort(tool_use_context):
        return _build_user_cancelled_result(block.id), []

    try:
        call = ToolCall(
            name=block.name,
            input=block.input,
            tool_use_id=block.id,
        )
        result = tool_registry.dispatch(call, tool_use_context)

        # Post-tool override: bash's interrupted payload reads as a
        # generic failure; replace it so the resume turn sees an
        # unambiguous "user rejected" signal. Mirrors TS at
        # ``StreamingToolExecutor.ts:332-345``.
        if _is_user_cancelled_abort(tool_use_context):
            return _build_user_cancelled_result(block.id), []

        tool = find_tool_by_name(tools, block.name) if tools else None
        metadata: dict[str, Any] = {}
        if isinstance(result.output, dict):
            metadata["tool_output"] = result.output

        if tool is not None:
            # WI-5.1: route through ``process_tool_result_block`` so the
            # 200K per-message aggregate cap is enforced. Without this
            # call the production REPL ran ``map_result_to_api`` directly
            # and never engaged the gate (critic B2).
            #
            # The read-decide-write on ``tool_result_chars_so_far`` is
            # serialized via ``_aggregate_lock`` because ``_run_tools_partitioned``
            # dispatches concurrency-safe tools (Read/Grep/Glob) via
            # ``asyncio.to_thread`` (critic B6). The decision MUST be
            # made on a fresh snapshot of the counter — otherwise N
            # threads racing the read all see 0, all decide "under cap"
            # and the cap is silently bypassed. ``process_tool_result_block``
            # is called inside the critical section: for small blocks
            # under threshold it just returns the block (no I/O); the
            # rare persist-to-disk path runs while serialized but those
            # are at most O(1) per turn (typically <5%).
            from ..services.tool_execution.tool_result_persistence import (
                compute_block_chars,
                process_tool_result_block,
                resolve_tool_results_dir,
            )
            tool_results_dir = resolve_tool_results_dir(tool_use_context)
            with tool_use_context._aggregate_lock:
                aggregate_so_far = tool_use_context.tool_result_chars_so_far
                api_block = process_tool_result_block(
                    tool,
                    result.output,
                    block.id,
                    tool_results_dir=tool_results_dir,
                    aggregate_chars_so_far=aggregate_so_far,
                )
                # Update the running aggregate AFTER the block is
                # finalized (post-persistence, so the wrapper message
                # size is what counts toward the budget — not the
                # original 200K output). Non-finite-threshold tools
                # (Read) don't count — TS skip-set semantics
                # (query.ts:419-423, toolResultStorage.ts:841-851).
                if math.isfinite(tool.max_result_size_chars):
                    tool_use_context.tool_result_chars_so_far += compute_block_chars(api_block)
            raw_content = api_block.get("content", "")
            # Preserve list-of-content-blocks shape so multimodal tool_results
            # (e.g. Read's image content blocks, bash data:image/... captures)
            # reach the API as proper image/document blocks instead of being
            # JSON-stringified into text. The Anthropic API rejects images
            # delivered as text and the model just sees JSON gibberish in the
            # tool_result content otherwise. ``ToolResultBlock.content`` and
            # ``content_block_to_dict`` already handle list shape end-to-end
            # (see content_blocks.py:159-173).
            if isinstance(raw_content, (str, list)):
                result_content: str | list[Any] = raw_content
            else:
                result_content = str(raw_content)
        elif isinstance(result.output, str):
            result_content = result.output
        elif isinstance(result.output, dict):
            result_content = json.dumps(result.output, ensure_ascii=False)
        else:
            result_content = str(result.output)

        result_msg = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=result_content,
                    is_error=result.is_error,
                    metadata=metadata,
                )
            ],
        )
        # Collect any supplemental messages the tool produced (e.g. the
        # FileReadTool image-dimensions metadata user message and PDF
        # page-image blocks). Mirrors TS messages flow where the Read
        # tool's createUserMessage({isMeta:true}) returns are pushed
        # into the conversation alongside the tool_result. Returned
        # separately so callers can emit all primaries before any extras
        # (see docstring).
        extras: list[UserMessage] = []
        if result.new_messages:
            for msg in result.new_messages:
                if isinstance(msg, UserMessage):
                    extras.append(msg)
                elif isinstance(msg, dict):
                    # Defensive: accept the raw-dict form too.
                    extras.append(UserMessage(
                        content=msg.get("content", ""),
                        isMeta=bool(msg.get("isMeta", False)),
                    ))
        return result_msg, extras
    except AbortError as abort_err:
        # Two contracts to satisfy at once:
        #
        # 1. **tool_use/tool_result pairing must stay intact** — every
        #    emitted tool_use needs a paired tool_result or the next
        #    API call 400s on the orphan. Returning a tool_result
        #    (not raising) preserves the pair. Pinned by
        #    ``tests/test_esc_reject_message_dispatch.py``.
        # 2. **No follow-up API turn after AbortError** — the loop
        #    must NOT issue another model call. Pinned by
        #    ``test_agent_loop_does_not_swallow_abort_error_as_tool_error``.
        #
        # Reconcile both: when AbortError surfaces from a tool and
        # the user-cancel signal isn't already tripped, trip it. The
        # post-tool gate downstream sees the signal aborted, sets
        # terminal=aborted_tools, and the adapter raises AbortError
        # to the caller. The conversation ends well-formed (tool_use
        # has its tool_result) AND the loop exits without another
        # API turn. Critic-flagged on Stage 4 review.
        if _is_user_cancelled_abort(tool_use_context):
            return _build_user_cancelled_result(block.id), []
        ctrl = getattr(tool_use_context, "abort_controller", None)
        if ctrl is not None:
            try:
                ctrl.abort("tool_raised_abort_error")
            except Exception:
                pass
        return UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=f"Error: Tool execution aborted ({abort_err})",
                    is_error=True,
                )
            ],
        ), []
    except Exception as e:
        # A late abort can still race the post-tool gate above (the
        # signal trips between the post-tool check and the exception).
        # Honour it here so a tool that raises an unrelated error AFTER
        # ESC landed doesn't get reported as a tool bug when the user
        # actually pressed ESC.
        if _is_user_cancelled_abort(tool_use_context):
            return _build_user_cancelled_result(block.id), []
        error_str = f"Error: {e}"
        return UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=error_str,
                    is_error=True,
                )
            ],
        ), []


async def _run_tools_partitioned(
    tool_use_blocks: list[ToolUseBlock],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools,
) -> list[UserMessage]:
    """Run tools with TS-matching concurrency: safe tools parallel, unsafe exclusive.

    Mirrors typescript/src/tools/partitionToolCalls + runTools (Mode 2).
    ConcurrencySafe tools (Read, Grep, Glob, etc.) run in parallel up to
    MAX_TOOL_USE_CONCURRENCY.  Non-safe tools (Bash, Edit, Write) run
    exclusively one at a time.
    """
    batches = _partition_tool_calls(tool_use_blocks, tools)
    # Emit all primary tool_result messages first, then all supplemental
    # (isMeta) messages. Interleaving (e.g. [a_result, a_meta, b_result])
    # breaks ensure_tool_result_pairing because the merge guard refuses to
    # combine tool_result-bearing user messages with text-only ones, so
    # b_result becomes orphaned and the pairing logic injects a synthetic
    # "[Tool result missing due to internal error]" placeholder.
    primaries: list[UserMessage] = []
    extras: list[UserMessage] = []

    def _accumulate(pair: tuple[UserMessage, list[UserMessage]]) -> None:
        primaries.append(pair[0])
        extras.extend(pair[1])

    for batch in batches:
        if batch.is_concurrent_safe and len(batch.blocks) > 1:
            coros = [
                asyncio.to_thread(
                    _dispatch_single_tool, block, tool_registry, tool_use_context, tools,
                )
                for block in batch.blocks[:MAX_TOOL_USE_CONCURRENCY]
            ]
            for pair in await asyncio.gather(*coros):
                _accumulate(pair)
            if len(batch.blocks) > MAX_TOOL_USE_CONCURRENCY:
                overflow = [
                    asyncio.to_thread(
                        _dispatch_single_tool, block, tool_registry, tool_use_context, tools,
                    )
                    for block in batch.blocks[MAX_TOOL_USE_CONCURRENCY:]
                ]
                for pair in await asyncio.gather(*overflow):
                    _accumulate(pair)
        else:
            for block in batch.blocks:
                pair = await asyncio.to_thread(
                    _dispatch_single_tool, block, tool_registry, tool_use_context, tools,
                )
                _accumulate(pair)

    return [*primaries, *extras]


def _run_tools_sync(
    tool_use_blocks: list[ToolUseBlock],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
) -> list[UserMessage]:
    """Legacy synchronous tool execution (no partitioning).

    Same primaries-first invariant as :func:`_run_tools_partitioned`.
    """
    primaries: list[UserMessage] = []
    extras: list[UserMessage] = []
    for block in tool_use_blocks:
        primary, extras_for_block = _dispatch_single_tool(block, tool_registry, tool_use_context)
        primaries.append(primary)
        extras.extend(extras_for_block)
    return [*primaries, *extras]


async def query(
    params: QueryParams,
    *,
    terminal_holder: TerminalHolder | None = None,
) -> AsyncGenerator[Message | StreamEvent, None]:
    """Canonical agent loop (chapter 5, Phase A foundation).

    The async generator yields messages and stream events to the consumer.
    The final ``Terminal`` is written to ``terminal_holder.value`` just
    before the generator returns (Python async generators cannot return
    values: PEP 525). Callers who care about the terminal pass their own
    ``TerminalHolder`` and read its ``.value`` after iteration.

    See :func:`run_query` for a convenience helper that consumes the
    generator and returns ``(messages, terminal)``.

    This PR (Phase A) introduces the typed Terminal infrastructure;
    recovery integration, stop hooks, token budget, model fallback,
    and continuation nudge land in subsequent PRs.
    """
    _diag = os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes")
    holder = terminal_holder or TerminalHolder()
    # Inner-only flag for the future outer two-layer wrapper (Phase G).
    # Until that lands, the flag is local; set_terminal still writes it
    # so every exit site uses the canonical helper.
    natural_termination: list[bool] = [False]
    state = QueryState(
        messages=list(params.messages),
        tool_use_context=params.tool_use_context,
        max_output_tokens_override=params.max_output_tokens_override,
    )
    config = build_query_config()
    # Created once per query() call, persisting across turns — mirrors TS
    # query.ts:311 (state built before the while(true) at :327). Any
    # successful tool result resets the counters inside the guard.
    tool_failure_guard_state = create_tool_failure_loop_guard_state()
    # ch05 round-3 G2: snapshot the turn-token baseline + budget into the
    # bootstrap globals (zero callers before this — without the snapshot,
    # get_turn_output_tokens() returns SESSION-cumulative tokens and the
    # budget check is silently wrong after any prior output). Tracker is
    # once-per-query (TS query.ts:299) — per-iteration construction would
    # disable diminishing-returns detection.
    from ..bootstrap.state import snapshot_output_tokens_for_turn

    # Top-level queries only: a nested subagent query() (Agent tool runs
    # inside the main turn's tool phase) or a sidechannel must NOT
    # re-snapshot — it would null the budget, re-baseline the turn counter,
    # and zero the continuation count mid-turn. TS snapshots only at the
    # REPL surface (REPL.tsx:2944); agent_id mirrors check_token_budget's
    # own subagent discriminator.
    if (
        getattr(params.tool_use_context, "agent_id", None) is None
        and params.query_source not in ("compact", "session_memory")
    ):
        snapshot_output_tokens_for_turn(params.token_budget)
    budget_tracker = create_budget_tracker()

    while True:
        messages = state.messages
        if _diag:
            logger.warning(
                "[DIAG] query loop: turn=%d  messages=%d  transition=%s",
                state.turn_count, len(messages),
                state.transition.reason if state.transition else "initial",
            )
        tool_use_context = state.tool_use_context
        # WI-5.1: reset the per-message aggregate counter at each turn
        # boundary. The 200K cap is PER USER MESSAGE (the next batch of
        # tool_result blocks the model will see), not per session. Without
        # this reset the counter grows monotonically and every tool result
        # eventually gets persisted regardless of size. Mirrors TS
        # ``toolResultStorage.ts:collectCandidatesByMessage`` which
        # partitions evaluation by message.
        tool_use_context.tool_result_chars_so_far = 0
        max_output_tokens_recovery_count = state.max_output_tokens_recovery_count
        has_attempted_reactive_compact = state.has_attempted_reactive_compact
        max_output_tokens_override = state.max_output_tokens_override
        turn_count = state.turn_count

        yield StreamEvent(type="stream_request_start")

        # --- Phase 0: Compression Pipeline ---
        # Mirrors TS query loop Phase 0: toolResultBudget → snip → microcompact → collapse → autocompact
        snip_tokens_freed = 0
        if params.pipeline_config is not None:
            try:
                # Estimate input tokens so layer 5 (autocompact) can decide
                # whether to fire. Without this the MIN_INPUT_TOKENS_FOR_AUTOCOMPACT
                # guard short-circuits and auto-compact never triggers.
                est_input_tokens = rough_token_count_estimation_for_messages(messages)
                pipeline_result = await run_compression_pipeline(
                    messages,
                    input_token_count=est_input_tokens,
                    config=params.pipeline_config,
                )
                if pipeline_result.tokens_saved > 0:
                    messages = pipeline_result.messages
                    snip_tokens_freed = pipeline_result.tokens_saved
                    if _diag:
                        logger.warning(
                            "[DIAG] Compression pipeline saved %d tokens (layers: %s)",
                            pipeline_result.tokens_saved,
                            ", ".join(pipeline_result.layers_applied),
                        )
            except Exception:
                logger.warning("Compression pipeline failed, continuing with original messages", exc_info=True)

        # Ch5/B.4 + B.5 — pre-emption guards before the API call.
        # Two distinct guards:
        #   B.4: hard blocking limit (auto-compact off OR no other
        #        recovery available) — saves the 500 the API would
        #        return anyway.
        #   B.5: autocompact circuit-breaker tripped (3 consecutive
        #        failures) AND we're still over the autocompact
        #        threshold — gives the user an actionable message
        #        instead of an opaque 500.
        # Skip when this iteration is a recovery retry (the messages
        # were already validated under the limit), or when this is a
        # compact/session_memory forked query (those need to run to
        # REDUCE the token count, blocking would deadlock).
        from ..services.compact.autocompact import (
            MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
            calculate_token_warning_state,
            is_auto_compact_enabled,
        )

        skip_blocking_guards = (
            params.query_source in ("compact", "session_memory")
            or (
                state.transition is not None
                and state.transition.reason
                in ("collapse_drain_retry", "reactive_compact_retry")
            )
        )

        if not skip_blocking_guards:
            context_window = _get_context_window(params.provider)
            # NB: ``messages`` here is already the post-pipeline list (we
            # reassigned ``messages = pipeline_result.messages`` above when
            # the pipeline freed tokens). So ``rough_token_count_estimation``
            # already reflects all compression-layer savings; subtracting
            # ``snip_tokens_freed`` again would double-count. The variable
            # is retained for B.5 logging / future TS-style snip-only
            # measurement, but the guard math uses the post-pipeline count
            # directly.
            token_usage = rough_token_count_estimation_for_messages(messages)
            warning = calculate_token_warning_state(token_usage, context_window)

            # B.5 (checked first — gives the more actionable message
            # when the breaker has tripped). Mirrors TS query.ts:705-725.
            tracking = state.auto_compact_tracking or (
                params.pipeline_config.autocompact_tracking
                if params.pipeline_config is not None
                else None
            )
            consec = (
                getattr(tracking, "consecutive_failures", 0)
                if tracking is not None
                else 0
            )
            if (
                consec >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
                and is_auto_compact_enabled()
                and warning["is_above_auto_compact_threshold"]
            ):
                yield _create_assistant_api_error_message(
                    content=(
                        "The conversation has exceeded the context limit "
                        "and automatic compaction has failed. Press esc twice "
                        "to go up a few messages and try again, or start a "
                        "new session with /new."
                    ),
                    error="invalid_request",
                )
                set_terminal(
                    holder, natural_termination, Terminal(reason="blocking_limit"),
                )
                return

            # B.4 (hard blocking limit). Mirrors TS query.ts:683-696.
            # Only fires when reactive-compact recovery is NOT available
            # — otherwise we let the API 413 and the B.2 recovery path
            # handle it. This matches TS: the guard exists to short-
            # circuit only when no recovery would catch the 500 anyway.
            elif (
                warning["is_at_blocking_limit"]
                and not (
                    config.reactive_compact_enabled
                    and is_auto_compact_enabled()
                )
            ):
                yield _create_assistant_api_error_message(
                    content=PROMPT_TOO_LONG_ERROR_MESSAGE,
                    error="invalid_request",
                )
                set_terminal(
                    holder, natural_termination, Terminal(reason="blocking_limit"),
                )
                return

        assistant_messages: list[AssistantMessage] = []
        tool_results: list[UserMessage] = []
        tool_use_blocks: list[ToolUseBlock] = []
        needs_follow_up = False

        # ch04 round-3 G3: overloaded-retry lane. Yield-based like TS
        # withRetry (status surfaces in the message stream, not a side
        # channel). Constraints: foreground sources only; never after
        # partial output (a mid-stream 529 with rendered text follows
        # the normal error path -- no duplicate text); SDK auto-retry
        # disabled underneath via sdk_max_retries=0 so "attempt k/3"
        # tells the truth.
        _is_foreground = params.query_source in FOREGROUND_529_RETRY_SOURCES
        _streamed_any = [False]
        _outer_chunk_cb = params.on_text_chunk

        def _marking_chunk_cb(text: str) -> None:
            _streamed_any[0] = True
            if _outer_chunk_cb is not None:
                _outer_chunk_cb(text)

        try:
            for _attempt in range(1, MAX_529_RETRIES + 2):
                _streamed_any[0] = False
                try:
                    returned_assistants, returned_tool_blocks = await _call_model_sync(
                        provider=params.provider,
                        messages=messages,
                        system_prompt=params.system_prompt,
                        tools=params.tools,
                        max_output_tokens_override=max_output_tokens_override,
                        abort_signal=params.abort_controller.signal,
                        on_text_chunk=(
                            _marking_chunk_cb if _outer_chunk_cb is not None
                            else None
                        ),
                        extended_thinking=params.extended_thinking,
                        thinking_effort=params.thinking_effort,
                        sdk_max_retries=0 if _is_foreground else None,
                    )
                    break
                except AbortError:
                    raise
                except Exception as retry_exc:
                    if (
                        _is_foreground
                        and _attempt <= MAX_529_RETRIES
                        and not _streamed_any[0]
                        and _is_overloaded_error(retry_exc)
                        and not params.abort_controller.signal.aborted
                    ):
                        delay = _retry_after_seconds(
                            retry_exc,
                            RETRY_BASE_DELAY_SECONDS * (2 ** (_attempt - 1)),
                        )
                        yield SystemMessage(
                            content=(
                                f"Server overloaded — retrying in {delay:.1f}s "
                                f"(attempt {_attempt}/{MAX_529_RETRIES})"
                            ),
                            level="warning",
                            subtype="api_retry",
                        )
                        # Abort-aware backoff (critic): TS sleeps WITH the
                        # signal (withRetry sleep(delay, signal)); a
                        # Retry-After can direct up to 60s — ESC must not
                        # be stuck behind it. Sliced sleep, checked every
                        # 250ms; an abort falls through to the existing
                        # aborted_streaming handling on the next attempt.
                        _remaining = delay
                        while _remaining > 0:
                            if params.abort_controller.signal.aborted:
                                break
                            _tick = min(0.25, _remaining)
                            await asyncio.sleep(_tick)
                            _remaining -= _tick
                        if params.abort_controller.signal.aborted:
                            # Land in the REAL abort lane (the outer
                            # except AbortError + post-call abort block),
                            # so the user sees the interruption message
                            # rather than a 529 model_error.
                            raise AbortError("interrupted during retry backoff") from retry_exc
                        continue
                    raise
            assistant_messages = returned_assistants
            tool_use_blocks = returned_tool_blocks
            needs_follow_up = len(tool_use_blocks) > 0

            for msg in assistant_messages:
                # Ch5/B.1 — three-source withholding pattern.
                # Mirrors TS query.ts:866-903. Recoverable errors are
                # suppressed from the yield stream so SDK consumers
                # (Cowork, the desktop app) don't disconnect mid-recovery.
                # If recovery exhausts, the message is surfaced later by
                # the dispatch code in the no-follow-up branch (B.2).
                withheld = (
                    _is_withheld_max_output_tokens(msg)
                    or _is_withheld_prompt_too_long(msg)
                    or _is_withheld_media_size(msg)
                )
                if not withheld:
                    yield msg

        except AbortError:
            # The provider's abort listener closed the streaming HTTP
            # response mid-flight (ESC pressed while the model was still
            # generating). The signal is already tripped, so let the
            # ``if params.abort_controller.signal.aborted`` block right
            # below us do the cancellation processing in exactly one
            # place — anything we did here would duplicate that work.
            pass

        except Exception as e:
            logger.error("Query error: %s", e)
            error_message = str(e)

            for err_msg in _yield_missing_tool_result_blocks(assistant_messages, error_message):
                yield err_msg

            yield _create_assistant_api_error_message(content=error_message)
            set_terminal(holder, natural_termination, Terminal(reason="model_error", error=e))
            return

        if params.abort_controller.signal.aborted:
            for err_msg in _yield_missing_tool_result_blocks(
                assistant_messages, "Interrupted by user"
            ):
                yield err_msg

            if params.abort_controller.signal.reason != "interrupt":
                yield _create_user_interruption_message(tool_use=False)
            set_terminal(holder, natural_termination, Terminal(reason="aborted_streaming"))
            return

        if not needs_follow_up:
            last_message = assistant_messages[-1] if assistant_messages else None

            if _is_withheld_max_output_tokens(last_message):
                if (
                    max_output_tokens_override is None
                    and max_output_tokens_recovery_count == 0
                ):
                    state = QueryState(
                        messages=messages,
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=has_attempted_reactive_compact,
                        max_output_tokens_override=ESCALATED_MAX_TOKENS,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        transition=Transition(reason="max_output_tokens_escalate"),
                    )
                    continue

                if max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    recovery_message = _create_user_message(
                        "Output token limit hit. Resume directly — no apology, no recap of what you were doing. "
                        "Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces.",
                        is_meta=True,
                    )
                    state = QueryState(
                        messages=[*messages, *assistant_messages, recovery_message],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=max_output_tokens_recovery_count + 1,
                        has_attempted_reactive_compact=has_attempted_reactive_compact,
                        max_output_tokens_override=None,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        transition=Transition(
                            reason="max_output_tokens_recovery",
                            attempt=max_output_tokens_recovery_count + 1,
                        ),
                    )
                    continue

                yield last_message  # type: ignore[arg-type]

            # Ch5/B.2 — PTL / media-size recovery via reactive_compact.
            # Mirrors TS query.ts:1195-1260. When the streaming model
            # returned a withheld PTL or media-size error AND we have
            # not yet attempted reactive_compact in this loop iteration,
            # try to compact and retry. The one-shot guard
            # (``has_attempted_reactive_compact``) prevents the
            # death-spiral failure mode documented in chapter §"Death
            # Spiral Guard": without it, a compact-then-still-413 loop
            # burns thousands of API calls.
            is_withheld_ptl = _is_withheld_prompt_too_long(last_message)
            is_withheld_media = _is_withheld_media_size(last_message)

            # Phase B post-critic: if the guard ALREADY tripped (we tried
            # reactive_compact this turn and the post-compact retry STILL
            # raised PTL/media), surface the withheld error and emit the
            # appropriate Terminal — do NOT fall through to the
            # "API error → Terminal(completed)" path. Mirrors TS at
            # query.ts:1244-1252.
            if (
                (is_withheld_ptl or is_withheld_media)
                and has_attempted_reactive_compact
            ):
                if last_message is not None:
                    yield last_message
                # ch05 G1: StopFailure fires on error exits (TS :1256).
                await _fire_stop_failure_hooks(last_message, tool_use_context)
                set_terminal(
                    holder,
                    natural_termination,
                    Terminal(
                        reason="image_error"
                        if is_withheld_media
                        else "prompt_too_long"
                    ),
                )
                return

            if (
                (is_withheld_ptl or is_withheld_media)
                and not has_attempted_reactive_compact
                and config.reactive_compact_enabled
            ):
                from ..services.compact.reactive_compact import (
                    ReactiveCompactResult,
                    reactive_compact,
                )
                from ..services.api.errors import PromptTooLongError

                # Synthesize an exception for reactive_compact's
                # is_prompt_too_long_error check. The withheld
                # message holds the original error string; we don't
                # need to round-trip it precisely because
                # reactive_compact only uses the exception for
                # classification.
                synthetic_err = PromptTooLongError(
                    "withheld during streaming, recovering"
                )
                result: ReactiveCompactResult = await reactive_compact(
                    messages=messages,
                    error=synthetic_err,
                    provider=params.provider,
                    model=config.model,
                )
                if result.compacted:
                    # ReactiveCompactResult.messages is list[Message]
                    # (verified 2026-05-12 against reactive_compact.py
                    # :33-39, :205-210, :230-236; the field
                    # concatenates CompactionResult.summary_messages
                    # which is list[UserMessage], with
                    # messages_to_keep which is list[Message]).
                    post_compact_messages: list[Message] = result.messages
                    for msg in post_compact_messages:
                        yield msg
                    # Critic finding (Phase B post-review): a successful
                    # reactive_compact MUST reset the engine's autocompact
                    # circuit-breaker counter. Otherwise the next iteration's
                    # B.5 guard re-reads the engine's persistent
                    # ``consecutive_failures`` (still ≥3 if the breaker
                    # tripped earlier in the session) and would trip
                    # ``Terminal(blocking_limit)`` immediately even though
                    # we just successfully compacted. Mirrors TS query.ts
                    # auto-compact success path (resets failures to 0).
                    if (
                        params.pipeline_config is not None
                        and params.pipeline_config.autocompact_tracking is not None
                    ):
                        params.pipeline_config.autocompact_tracking.consecutive_failures = 0
                    state = QueryState(
                        messages=post_compact_messages,
                        tool_use_context=tool_use_context,
                        # Carry the engine's tracking through the retry so
                        # next iteration's B.5 reads the post-reset count.
                        auto_compact_tracking=(
                            params.pipeline_config.autocompact_tracking
                            if params.pipeline_config is not None
                            else None
                        ),
                        max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=True,  # one-shot
                        max_output_tokens_override=None,
                        stop_hook_active=state.stop_hook_active,
                        turn_count=turn_count,
                        pending_tool_use_summary=state.pending_tool_use_summary,
                        continuation_nudge_count=state.continuation_nudge_count,
                        transition=Transition(reason="reactive_compact_retry"),
                    )
                    continue

                # No recovery — surface the (until-now withheld) error
                # and exit with the appropriate Terminal reason.
                # DEATH-SPIRAL GUARD: do NOT fall through to a future
                # stop-hooks call here. Mirrors TS query.ts:1244-1252
                # ("error -> hook blocking -> retry -> error -> ...
                # the hook injects more tokens each cycle"). When C.1
                # lands the stop-hooks dispatch, this early return
                # must remain.
                if last_message is not None:
                    yield last_message
                # ch05 G1: StopFailure fires on error exits (TS :1263).
                await _fire_stop_failure_hooks(last_message, tool_use_context)
                set_terminal(
                    holder,
                    natural_termination,
                    Terminal(
                        reason="image_error"
                        if is_withheld_media
                        else "prompt_too_long"
                    ),
                )
                return

            if last_message and (
                getattr(last_message, "isApiErrorMessage", False)
                or _is_withheld_max_output_tokens(last_message)
            ):
                # Death-spiral guard (TS query.ts:1346-1349): NO Stop hooks
                # on an API-error response ("error -> hook blocking ->
                # retry -> error -> ... the hook injects more tokens each
                # cycle"); StopFailure hooks fire instead. The recovery-
                # exhausted max-output-tokens message is included explicitly:
                # the port tags it isApiErrorMessage=False (it carries real
                # partial content), while TS's equivalent is a synthetic
                # error message with isApiErrorMessage=true
                # (claude.ts:2289-2295) — without this clause a blocking
                # Stop hook re-opens the truncation spiral the recovery
                # counter just closed.
                await _fire_stop_failure_hooks(last_message, tool_use_context)
                set_terminal(holder, natural_termination, Terminal(reason="completed"))
                return

            # ch05 round-3 G1 — Stop hooks at the clean no-tool-use exit
            # (TS query.ts:1351-1391 via query/stopHooks.ts). The handler
            # streams its own progress/system messages, then yields the
            # final StopHookResult.
            stop_result = StopHookResult()
            try:
                async for item in handle_stop_hooks_streaming(
                    messages,
                    assistant_messages,
                    # The system-prompt param is signature-parity only —
                    # _handle_stop_hooks_generator never reads it, so ""
                    # for block-list prompts is deliberate.
                    params.system_prompt
                    if isinstance(params.system_prompt, str)
                    else "",
                    tool_use_context,
                    params.query_source,
                    state.stop_hook_active,
                ):
                    if isinstance(item, StopHookResult):
                        stop_result = item
                    else:
                        yield item
            except Exception:
                logger.exception("stop hooks failed; continuing to exit")

            if stop_result.prevent_continuation:
                set_terminal(
                    holder,
                    natural_termination,
                    Terminal(reason="stop_hook_prevented"),
                )
                return

            if stop_result.blocking_errors:
                # Stop hook says "not done" — retry with the blocking
                # errors appended. PRESERVE has_attempted_reactive_compact:
                # if compact already ran and couldn't recover, retrying
                # after a stop-hook blocking error will produce the same
                # result; resetting to False here caused an infinite loop
                # burning thousands of API calls (TS query.ts:1375-1381).
                state = QueryState(
                    messages=[
                        *messages,
                        *assistant_messages,
                        *stop_result.blocking_errors,
                    ],
                    tool_use_context=tool_use_context,
                    auto_compact_tracking=state.auto_compact_tracking,
                    max_output_tokens_recovery_count=0,
                    has_attempted_reactive_compact=has_attempted_reactive_compact,
                    max_output_tokens_override=None,
                    stop_hook_active=True,
                    turn_count=turn_count,
                    pending_tool_use_summary=None,
                    continuation_nudge_count=state.continuation_nudge_count,
                    transition=Transition(reason="stop_hook_blocking"),
                )
                continue

            # ch05 round-3 G2 — token budget (TS query.ts:1393-1441).
            # No MAX_CONTINUATION_NUDGES interaction: budget continuations
            # are bounded only by check_token_budget's 90%/diminishing
            # rules (the nudge cap below is a SEPARATE mechanism).
            from ..bootstrap.state import (
                get_current_turn_token_budget,
                get_turn_output_tokens,
                increment_budget_continuation_count,
            )

            budget_decision = check_token_budget(
                budget_tracker,
                getattr(tool_use_context, "agent_id", None),
                get_current_turn_token_budget(),
                get_turn_output_tokens(),
            )
            if isinstance(budget_decision, ContinueDecision):
                increment_budget_continuation_count()
                logger.debug(
                    "Token budget continuation #%d: %d%% (%d/%d)",
                    budget_decision.continuation_count,
                    budget_decision.pct,
                    budget_decision.turn_tokens,
                    budget_decision.budget,
                )
                state = QueryState(
                    messages=[
                        *messages,
                        *assistant_messages,
                        UserMessage(
                            content=budget_decision.nudge_message,
                            isMeta=True,
                        ),
                    ],
                    tool_use_context=tool_use_context,
                    auto_compact_tracking=state.auto_compact_tracking,
                    max_output_tokens_recovery_count=0,
                    has_attempted_reactive_compact=False,
                    max_output_tokens_override=None,
                    stop_hook_active=None,
                    turn_count=turn_count,
                    pending_tool_use_summary=None,
                    continuation_nudge_count=state.continuation_nudge_count,
                    transition=Transition(reason="token_budget_continuation"),
                )
                continue
            if getattr(budget_decision, "completion_event", None):
                logger.debug(
                    "token budget completed: %s",
                    budget_decision.completion_event,
                )

            # ch05 round-3 G5 — continuation nudge (TS query.ts:1443-1512):
            # the model SAID it would act but called no tools. Capped at
            # MAX_CONTINUATION_NUDGES per turn-chain.
            if (
                assistant_messages
                and (params.max_turns is None or turn_count < params.max_turns)
                and state.continuation_nudge_count < MAX_CONTINUATION_NUDGES
            ):
                last_assistant = assistant_messages[-1]
                content = getattr(last_assistant, "content", "")
                if isinstance(content, str):
                    last_text = content
                else:
                    last_text = " ".join(
                        getattr(b, "text", "")
                        for b in content
                        if getattr(b, "type", None) == "text"
                    )
                if last_text and detect_continuation_signal(last_text):
                    logger.debug(
                        "Continuation nudge triggered (%d/%d)",
                        state.continuation_nudge_count + 1,
                        MAX_CONTINUATION_NUDGES,
                    )
                    state = QueryState(
                        messages=[
                            *messages,
                            *assistant_messages,
                            UserMessage(content=NUDGE_MESSAGE, isMeta=True),
                        ],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=0,
                        has_attempted_reactive_compact=False,
                        max_output_tokens_override=None,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        pending_tool_use_summary=None,
                        continuation_nudge_count=state.continuation_nudge_count + 1,
                        transition=Transition(reason="continuation_nudge"),
                    )
                    continue

            set_terminal(holder, natural_termination, Terminal(reason="completed"))
            return

        for block in tool_use_blocks:
            yield SystemMessage(
                content=f"Running tool: {block.name}",
                subtype="tool_use_progress",
            )

        if _diag:
            _tools_t0 = time.monotonic()
            _batches = _partition_tool_calls(tool_use_blocks, params.tools)
            _batch_desc = ", ".join(
                f"[{'parallel' if b.is_concurrent_safe else 'exclusive'}: {[bl.name for bl in b.blocks]}]"
                for b in _batches
            )
            logger.warning(
                "[DIAG] query loop: running %d tools in %d batches: %s",
                len(tool_use_blocks), len(_batches), _batch_desc,
            )

        # Snapshot the current conversation onto the ToolContext so
        # tools that need history (currently: the client-side advisor)
        # can read it via ``ctx.messages``. The list is the post-pipeline
        # message stream up to and including the assistant message that
        # just emitted the tool_use blocks we're about to dispatch. The
        # advisor strips its own prior blocks via
        # ``build_advisor_forwarded_messages`` before forwarding. Other
        # tools ignore the field (the existing default factory was an
        # empty list, so behavior is unchanged for them).
        tool_use_context.messages = list(messages)
        # Also snapshot the active provider via a dynamic attribute so
        # the client-side advisor can reuse it (and its config) when
        # the user is on a proxy that probably proxies the advisor
        # model too. Set as a plain attribute (not a dataclass field)
        # to avoid touching ToolContext's public surface.
        setattr(tool_use_context, "_active_provider", params.provider)

        tool_results = await _run_tools_partitioned(
            tool_use_blocks,
            params.tool_registry,
            tool_use_context,
            params.tools,
        )

        if _diag:
            logger.warning(
                "[DIAG] query loop: tools finished in %.1fs, %d results",
                time.monotonic() - _tools_t0, len(tool_results),
            )
            for tr in tool_results:
                if isinstance(tr.content, list):
                    for b in tr.content:
                        if hasattr(b, 'content'):
                            clen = len(b.content) if isinstance(b.content, str) else len(str(b.content))
                            logger.warning("[DIAG]   result: tool_use_id=%s  is_error=%s  content_len=%d", getattr(b, 'tool_use_id', '?'), getattr(b, 'is_error', False), clen)

        for result_msg in tool_results:
            yield result_msg

        if params.abort_controller.signal.aborted:
            if params.abort_controller.signal.reason != "interrupt":
                yield _create_user_interruption_message(tool_use=True)
            set_terminal(holder, natural_termination, Terminal(reason="aborted_tools"))
            return

        # Ch5/round2 — hook_stopped terminal mapping. Mirrors TS at
        # query.ts:1698-1701. After tool execution, scan tool_results
        # for any AttachmentMessage carrying a
        # ``hook_stopped_continuation`` marker. If found, exit cleanly
        # with Terminal(reason='hook_stopped') rather than advancing to
        # the next turn.
        #
        # Order matters:
        #   * AFTER abort check (above) — user-driven abort wins,
        #     matching TS at query.ts:1665.
        #   * BEFORE max_turns check (below) — a hook-stopped exit must
        #     NOT also yield ``max_turns_reached``; the loop is exiting
        #     for a different reason. Matches TS where the hook_stopped
        #     return at :1701 precedes the max_turns check at :1885.
        #   * BEFORE state reconstruction — no next iteration follows.
        if any(_is_hook_stopped_continuation(msg) for msg in tool_results):
            set_terminal(
                holder, natural_termination, Terminal(reason="hook_stopped"),
            )
            return

        # Tool-failure-loop guard — mirrors TS query.ts:1638-1666: runs
        # AFTER the abort and hook_stopped returns, BEFORE max_turns. On
        # trip, yield the explanation as an API-error assistant message
        # (TS createAssistantAPIErrorMessage at :1663-1665) and exit with
        # the dedicated terminal reason. TS also fires a telemetry event
        # here (tengu_tool_failure_loop_guard_tripped); the port has no
        # logEvent analogue, so logger.debug carries the diagnostics.
        guard_decision = update_tool_failure_loop_guard(
            state=tool_failure_guard_state,
            tool_use_blocks=tool_use_blocks,
            tool_results=tool_results,
        )
        if guard_decision.tripped:
            logger.debug(
                "Tool failure loop guard tripped: kind=%s threshold=%s "
                "tool_name=%s error_category=%s path=%s",
                guard_decision.kind,
                guard_decision.threshold,
                guard_decision.tool_name,
                guard_decision.error_category,
                guard_decision.path,
            )
            yield create_assistant_api_error_message(guard_decision.message or "")
            set_terminal(
                holder,
                natural_termination,
                Terminal(reason="tool_failure_loop"),
            )
            return

        next_turn_count = turn_count + 1

        if params.max_turns and next_turn_count > params.max_turns:
            yield _create_max_turns_attachment(params.max_turns, next_turn_count)
            set_terminal(
                holder,
                natural_termination,
                Terminal(reason="max_turns", turn_count=next_turn_count),
            )
            return

        # Chapter-10 / Chunk D / WI-3.3 — pending-messages drain at the
        # tool-round boundary. Per chapter §"Background: Three Channels":
        # *"messages arrive between tool rounds, not mid-execution. The
        # agent finishes its current thought, then receives the new
        # information."* We drain AFTER tool_results have been appended
        # but BEFORE the next API call, so the model sees the queued
        # messages on the next turn alongside the tool results.
        injected_messages = _drain_pending_user_messages(tool_use_context)
        for inj in injected_messages:
            yield inj

        state = QueryState(
            messages=[*messages, *assistant_messages, *tool_results, *injected_messages],
            tool_use_context=tool_use_context,
            auto_compact_tracking=state.auto_compact_tracking,
            turn_count=next_turn_count,
            max_output_tokens_recovery_count=0,
            has_attempted_reactive_compact=False,
            max_output_tokens_override=None,
            stop_hook_active=state.stop_hook_active,
            # Phase A: reset per-turn counter; carry pending summary forward.
            continuation_nudge_count=0,
            pending_tool_use_summary=state.pending_tool_use_summary,
            transition=Transition(reason="next_turn"),
        )


async def run_query(
    params: QueryParams,
) -> tuple[list[Message | StreamEvent], Terminal]:
    """Ch5/A.4: Convenience helper for callers that want both the
    yielded messages and the Terminal in one call.

    Drives the canonical :func:`query` async generator, collects all
    yielded messages into a list, and returns ``(messages, terminal)``.
    The terminal's reason discriminates why the loop stopped (11
    distinct reasons, matching TS query/transitions.ts).

    Tests and convenience entry points should use this helper.
    Streaming consumers (REPL, TUI) should keep using ``async for``
    with their own ``TerminalHolder``.
    """
    holder = TerminalHolder()
    messages: list[Message | StreamEvent] = []
    async for msg in query(params, terminal_holder=holder):
        messages.append(msg)
    if holder.value is None:
        # Contract violation — the loop returned without setting
        # the terminal. Fall back to a model_error so callers don't
        # see ``None`` and crash.
        holder.value = Terminal(
            reason="model_error",
            error=RuntimeError("query() returned without setting Terminal"),
        )
    return messages, holder.value
