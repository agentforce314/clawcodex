from __future__ import annotations

import asyncio
import json
import logging
import math
import random
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
from ..tool_system.build_tool import Tool, Tools
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
# ch04 round-4 GAP B — general retryable budget (429/5xx/connection/
# timeout), TS DEFAULT_MAX_RETRIES (withRetry.ts:52).
DEFAULT_MAX_RETRIES = 10
RETRY_BASE_DELAY_SECONDS = 0.5
# Narrower than TS's FOREGROUND_529_RETRY_SOURCES (agents/compact/
# side_question etc., withRetry.ts:62-90) -- minimal posture; widen to
# agent sources when subagent traffic matters. 'sdk' added in ch05
# round-4 alongside the headless query_source relabel ('repl_main_thread'
# -> 'sdk'): TS's set includes 'sdk' (withRetry.ts:67), so headless keeps
# the yield-based retry lane after the relabel.
FOREGROUND_529_RETRY_SOURCES = frozenset({"repl_main_thread", "sdk"})
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
    # ch04 round-4 GAP B — model to switch to after MAX_529_RETRIES
    # consecutive overloaded errors (TS QueryParams.fallbackModel,
    # query.ts:276; --fallback-model in headless). Session-sticky switch
    # via provider.model; never persisted to settings.
    fallback_model: str | None = None
    # ch05 round-4 note — N/A-by-architecture on the LIVE paths: the
    # adapter's build_effective_system_prompt already injects CLAUDE.md
    # ("## Project Instructions") and the date via the SYSTEM prompt, so
    # wiring TS's prependUserContext there would double-inject. Only the
    # test-only engine path uses the message-based mechanism
    # (prepend_user_context at engine.py:221). One mechanism per surface.
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
    # Live thinking deltas (separate channel from on_text_chunk) for the TUI's
    # streaming thinking view. None → thinking isn't surfaced live.
    on_thinking_chunk: Callable[[str], None] | None = None

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


async def _fire_post_sampling_hooks(
    assistant_messages: list[AssistantMessage],
    provider: Any,
    tool_use_context: Any,
) -> None:
    """Run configured ``PostSampling`` hooks for a completed model stream.

    ch01 round-4 WI-2 — restores the dependency-graph edge "Query Loop
    fires Hooks" for the one event TS fires from query.ts itself
    (``executePostSamplingHooks``, query.ts:1079-1089). Reads the global
    ``AsyncHookRegistry`` (populated at startup by
    ``bootstrap_hook_config_manager``); with no ``PostSampling`` hooks
    configured this returns after one in-memory lookup.

    Deviation from TS, deliberate: awaited inline instead of
    fire-and-forget. The agent-server drives each turn with
    ``asyncio.run(...)``, so a task created on the loop's final iteration
    would be cancelled at teardown — losing the hook on exactly the
    turn-final response. Inline await trades stream/hook overlap for a
    completion guarantee.

    Hook failures are logged and swallowed — a hook must never kill the
    turn (TS parity: logError + continue). ``additional_contexts`` from
    hook results are debug-logged and dropped: TS post-sampling hooks
    return void, so there are no injection semantics to mirror; a uniform
    injection lane across events is the ch12 round-4 subject.
    """
    if not assistant_messages:
        return
    try:
        from ..hooks.post_sampling_hooks import run_post_sampling_hooks
        from ..hooks.trust_gate import should_skip_hook_due_to_trust

        last = assistant_messages[-1]
        results = await run_post_sampling_hooks(
            model=(
                getattr(last, "model", None)
                or getattr(provider, "model", None)
                or ""
            ),
            usage=getattr(last, "usage", None) or {},
            stop_reason=getattr(last, "stop_reason", None),
            # Same trust rule as the tool-hook lane: untrusted workspace →
            # policy hooks only (trust_gate WI-0.2).
            untrusted_workspace=should_skip_hook_due_to_trust(tool_use_context),
        )
        for entry in results:
            injected = entry.get("injected_messages")
            if injected:
                logger.debug(
                    "PostSampling hook additional_contexts dropped "
                    "(no injection lane yet — ch12): %d block(s)",
                    len(injected),
                )
    except (asyncio.CancelledError, AbortError):
        # User intent wins — AbortError subclasses Exception, so without the
        # explicit re-raise the blanket handler below would swallow it.
        raise
    except Exception:  # noqa: BLE001 — hook failure must never kill the turn
        logger.error("PostSampling hook execution failed", exc_info=True)


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
    that set ``prevent_continuation`` (see
    ``src/services/tool_execution/tool_execution.py`` and
    ``tool_hooks.py``). WIRED at ch07 round-3 PR-1: the production loop
    consumes orchestrator.run_tools, which routes through those
    producers; detection happens IN the consumption loop (the attachment
    never reaches the collected tool_results under raw user-role
    collection).

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


# ch04 round-4 GAP C.2 — required whenever a system block carries
# cache_control.scope == "global" (TS constants/betas.ts:17-18).
PROMPT_CACHING_SCOPE_BETA_HEADER = "prompt-caching-scope-2026-01-05"


def _strip_block_metadata(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return copies of ``blocks`` ready for the 1P Anthropic wire: the
    internal ``_cache_scope`` key removed AND the dynamic-boundary marker
    block dropped.

    The Anthropic provider forwards system blocks verbatim to its SDK
    (``call_kwargs["system"] = system_prompt``), so the inert ``_cache_scope``
    tag emitted by the prompt assembler must be stripped before it lands on a
    1P request. ch04 round-4 GAP C: the boundary is a SPLIT SIGNAL, never
    wire content — TS's splitSysPromptPrefix skips it (utils/api.ts:388,424);
    before this fix the literal ``__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`` text
    block went out on every 1P request. Non-Anthropic providers flatten via
    ``_split_system_prompt_blocks``, which already drops it.
    """
    from ..context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY

    cleaned: list[dict[str, Any]] = []
    for blk in blocks:
        if (
            isinstance(blk, dict)
            and blk.get("type") == "text"
            and blk.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        ):
            continue
        if isinstance(blk, dict) and "_cache_scope" in blk:
            blk = {k: v for k, v in blk.items() if k != "_cache_scope"}
        cleaned.append(blk)
    return cleaned


def _split_system_prompt_blocks(
    blocks: list[dict[str, Any]], *, relocate_request_scope: bool
) -> tuple[str, str]:
    """Flatten system-prompt blocks for an OpenAI-compatible provider.

    Returns ``(system_text, volatile_tail_text)``.

    The ``__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`` marker is always dropped (it is
    an Anthropic cache-only signal that would be unintelligible prose to other
    models).

    When ``relocate_request_scope`` is True (DeepSeek only), blocks tagged
    ``_cache_scope == "request"`` — the env section, the auto-memory section
    (which embeds the mutable ``MEMORY.md`` body), plan-mode / non-interactive
    / tool-restriction sections — are routed into ``volatile_tail_text`` so the
    caller can place them AFTER the conversation history. That keeps the
    ``system + tools + history`` prefix byte-stable across turns, so DeepSeek's
    automatic prefix cache covers it even when memory or the environment
    changes mid-session.

    When False (every other provider), the tail is empty and all non-boundary
    text is concatenated into ``system_text`` — byte-for-byte the prior
    behaviour.
    """
    from ..context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY

    stable: list[str] = []
    volatile: list[str] = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        text = blk.get("text")
        if not text or text == SYSTEM_PROMPT_DYNAMIC_BOUNDARY:
            continue
        if relocate_request_scope and blk.get("_cache_scope") == "request":
            volatile.append(str(text))
        else:
            stable.append(str(text))
    return "\n\n".join(stable), "\n\n".join(volatile)


def _append_session_context_tail(
    api_messages: list[dict[str, Any]], tail_text: str
) -> list[dict[str, Any]]:
    """Place the DeepSeek relocated-volatile sections AFTER the conversation.

    Wrapped as ambient ``<system-reminder>`` context. Merged into the trailing
    user message when that is a plain user turn (string content, or a
    content-block list with no ``tool_result``) so the wire keeps strict
    user/assistant alternation. Otherwise — e.g. the turn ends in a tool result
    (which converts to ``role:tool`` on the wire) — appended as a standalone
    trailing user message, which lands correctly after the tool messages.
    Returns a new list; ``api_messages`` is not mutated.
    """
    reminder = (
        "<system-reminder>\n"
        "Current session/environment context (ambient — not a new user request):\n"
        f"{tail_text}\n"
        "</system-reminder>"
    )
    last = api_messages[-1] if api_messages else None
    if isinstance(last, dict) and last.get("role") == "user":
        content = last.get("content")
        if isinstance(content, str):
            merged = dict(last)
            merged["content"] = f"{content}\n\n{reminder}" if content else reminder
            return [*api_messages[:-1], merged]
        if isinstance(content, list) and not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            merged = dict(last)
            merged["content"] = [*content, {"type": "text", "text": reminder}]
            return [*api_messages[:-1], merged]
    return [*api_messages, {"role": "user", "content": reminder}]


async def _call_model_sync(
    *,
    provider: BaseProvider,
    messages: list[Message],
    system_prompt: str,
    tools: Tools,
    max_output_tokens_override: int | None = None,
    abort_signal: Any = None,
    on_text_chunk: Callable[[str], None] | None = None,
    on_thinking_chunk: Callable[[str], None] | None = None,
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
        # Master switch (default False): the advisor stays inactive unless the
        # user opted in via `advisor_enabled` in ~/.clawcodex/config.json.
        advisor_enabled = bool(getattr(settings, "advisor_enabled", False))
        if configured and advisor_enabled:
            from ..models.model import canonical_model_name
            candidate = canonical_model_name(configured)
            advisor_mode = decide_advisor_mode(
                provider,
                main_loop_model,
                candidate,
                force_client_mode=force_client,
                advisor_provider=configured_provider,
                advisor_enabled=advisor_enabled,
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
        # entries on Bedrock/Vertex transports. setdefault-append so it
        # composes with the global-cache-scope beta (GAP C.2) below.
        call_kwargs.setdefault("betas", []).append(ADVISOR_BETA_HEADER)
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
        # Strip the inert ``_cache_scope`` metadata + the dynamic-boundary
        # marker block; Anthropic forwards the system list verbatim to its
        # SDK, so neither must reach the 1P wire (GAP C).
        if isinstance(system_prompt, list):
            system_prompt = _strip_block_metadata(system_prompt)
            # ch04 round-4 GAP C.2 — scope:'global' requires the
            # prompt-caching-scope beta (TS claude.ts:1231-1236 pushes
            # PROMPT_CACHING_SCOPE_BETA_HEADER whenever global cache is
            # active). The scope only appears when the operator enabled
            # CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE; without the header the
            # API rejects/ignores the field.
            if any(
                isinstance(blk, dict)
                and isinstance(blk.get("cache_control"), dict)
                and blk["cache_control"].get("scope") == "global"
                for blk in system_prompt
            ):
                call_kwargs.setdefault("betas", []).append(
                    PROMPT_CACHING_SCOPE_BETA_HEADER
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
        #
        # DeepSeek-only: route the per-request-volatile (REQUEST-scope)
        # sections to a trailing tail so the system prefix stays byte-stable
        # for DeepSeek's automatic prefix cache. ``relocate_request_scope`` is
        # False for every other provider, so ``flattened`` keeps every
        # non-boundary block (byte-for-byte the prior behaviour) and
        # ``volatile_tail`` is "".
        # ``is True`` (not ``bool(...)``): every real provider sets the flag to a
        # literal ``True``/``False`` (see ``BaseProvider.is_deepseek``), so this
        # is identical in production — but it also makes a bare test double (e.g.
        # ``MagicMock()``, whose auto-attributes are truthy) fall through to the
        # non-relocating path instead of silently exercising DeepSeek relocation.
        is_deepseek = getattr(provider, "is_deepseek", False) is True
        volatile_tail = ""
        if isinstance(system_prompt, list):
            flattened, volatile_tail = _split_system_prompt_blocks(
                system_prompt, relocate_request_scope=is_deepseek
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
        # DeepSeek: the relocated REQUEST-scope sections (env, auto-memory,
        # plan-mode, …) ride a trailing <system-reminder> user message so they
        # sit AFTER the conversation history. The system + tools + history
        # prefix then stays byte-stable turn-over-turn and hits DeepSeek's
        # automatic prefix cache even when memory or the environment changes
        # mid-session. ``volatile_tail`` is always "" for other providers, so
        # this is a strict no-op for them.
        if volatile_tail:
            api_messages = _append_session_context_tail(api_messages, volatile_tail)

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
                        on_thinking_chunk=on_thinking_chunk,
                        abort_signal=abort_signal,
                        **call_kwargs,
                    )
                except TypeError:
                    # Provider doesn't accept on_thinking_chunk — retry text-only,
                    # then kwargless, so text streaming never regresses.
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
        from ..bootstrap.state import add_to_total_duration_state
        from ..cost_tracker import record_api_usage

        record_api_usage(
            getattr(response, "model", None) or getattr(provider, "model", "unknown"),
            response.usage,
        )
        # The original records per-request API duration alongside cost
        # (addToTotalDuration beside addToTotalSessionCost); this feeds
        # /cost's "Total duration (API)". This layer can't split retries:
        # provider-internal ones are inside the span (so both counters get
        # the same value), while a raise-then-retry by our caller records
        # nothing for the failed attempt (the original's including-retries
        # counter would) — a slight undercount, accepted.
        _api_ms = int((time.monotonic() - _t0) * 1000)
        add_to_total_duration_state(_api_ms, _api_ms)
    except Exception:
        logger.debug("cost recording failed", exc_info=True)

    assistant_msg = AssistantMessage(
        content=assistant_blocks if assistant_blocks else "",
        stop_reason=stop_reason,
        # TS assistant messages carry the responding model (query.ts message
        # assembly); consumers like the PostSampling hook payload and cost
        # attribution read it. Same fallback chain as record_api_usage above.
        model=getattr(response, "model", None) or getattr(provider, "model", None),
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

    Recovery integration, stop hooks, token budget, the continuation
    nudge (ch05 rounds 2-3), and the retry lane with model fallback
    (ch04 round-4) are all wired: after ``MAX_529_RETRIES`` consecutive
    overloaded errors with ``params.fallback_model`` configured, the lane
    switches ``provider.model`` session-sticky (never persisted) and
    keeps going. No tombstones by design — the lane only retries when
    nothing streamed, so no partial assistant message ever needs
    retraction (TS retries wrap partial streams and must tombstone).
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

    # ch07 round-3 G1: the orchestrator lane sources tool lookup AND the
    # concurrency partition from context.options.tools — production
    # previously left it empty ([] default), which would silently route
    # every tool through the base-list fallback and classify off
    # defaults. Sync once per query.
    params.tool_use_context.options.tools = list(params.tools)

    # ch09 round-4 WI-1 — capture the parent's rendered system prompt onto
    # its context so a fork spawned DURING this turn threads the parent's
    # EXACT prompt (byte-identical), letting the fork child chain onto the
    # parent's warm [system+tools+history] cache. Without this the field
    # was dead scaffolding (permanently None) and fork children fell back
    # to DEFAULT_AGENT_PROMPT — diverging at byte 0 and reprocessing the
    # whole history at full price (fork's entire economic point, inert).
    # TS: toolUseContext.renderedSystemPrompt (AgentTool.tsx:496). The
    # captured value is the INPUT to _call_model_sync's assembly; the fork
    # child threads the same input and goes through the same assembly, so
    # the wire bytes match. Set unconditionally (cheap; only fork reads it;
    # a fork-of-subagent is guarded regardless).
    if params.system_prompt:
        try:
            params.tool_use_context.rendered_system_prompt = params.system_prompt
        except Exception:  # noqa: BLE001 — read-only stub context
            pass

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

        # ch04 round-3 G3, widened by round-4 GAP B: the retry lane.
        # Yield-based like TS withRetry (status surfaces in the message
        # stream, not a side channel). Constraints preserved from round-3:
        # foreground sources only; never after partial output (a mid-stream
        # failure with rendered text follows the normal error path — no
        # duplicate text); SDK auto-retry disabled underneath via
        # sdk_max_retries=0 so attempt counts tell the truth.
        #
        # Round-4 additions (TS withRetry.ts parity):
        #   * general retryable classes — 429 / 5xx / connection / timeout —
        #     retry under DEFAULT_MAX_RETRIES with exponential backoff +
        #     0-25% jitter (TS getRetryDelay); quota-exhausted and
        #     non-retryable errors bail immediately;
        #   * model fallback — after MAX_529_RETRIES consecutive 529s with
        #     params.fallback_model configured, switch provider.model
        #     (session-sticky like TS's mainLoopModel switch; NOT persisted
        #     to settings), announce it, reset the 529 counter, keep going.
        #     No tombstones needed (deliberate divergence): this lane only
        #     retries when NOTHING streamed; TS's retry wraps partial
        #     streams and must tombstone the orphans (query.ts:795-824).
        _is_foreground = params.query_source in FOREGROUND_529_RETRY_SOURCES
        _streamed_any = [False]
        _outer_chunk_cb = params.on_text_chunk

        def _marking_chunk_cb(text: str) -> None:
            _streamed_any[0] = True
            if _outer_chunk_cb is not None:
                _outer_chunk_cb(text)

        try:
            _general_attempts = 0
            _consecutive_529s = 0
            while True:
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
                        on_thinking_chunk=params.on_thinking_chunk,
                        extended_thinking=params.extended_thinking,
                        thinking_effort=params.thinking_effort,
                        sdk_max_retries=0 if _is_foreground else None,
                    )
                    break
                except AbortError:
                    raise
                except Exception as retry_exc:
                    if (
                        not _is_foreground
                        or _streamed_any[0]
                        or params.abort_controller.signal.aborted
                    ):
                        raise

                    from ..services.api.errors import (
                        categorize_retryable_api_error,
                        is_quota_exhausted,
                    )

                    is_529 = _is_overloaded_error(retry_exc)
                    if not is_529:
                        classification = categorize_retryable_api_error(retry_exc)
                        if not classification.retryable or is_quota_exhausted(retry_exc):
                            raise

                    _general_attempts += 1
                    if is_529:
                        _consecutive_529s += 1
                    else:
                        _consecutive_529s = 0

                    # Model fallback — TS withRetry.ts:345-369 →
                    # query.ts:977-1032. Fires once; a 529 storm on the
                    # fallback model then follows the normal exhaustion path
                    # (the model equality check keeps it single-shot).
                    if (
                        is_529
                        and _consecutive_529s >= MAX_529_RETRIES
                        and params.fallback_model
                        and params.fallback_model
                        != getattr(params.provider, "model", None)
                    ):
                        _original_model = getattr(params.provider, "model", "?")
                        try:
                            params.provider.model = params.fallback_model
                        except Exception:  # noqa: BLE001 — read-only provider stub
                            raise retry_exc
                        logger.warning(
                            "model fallback: %s -> %s after %d consecutive "
                            "overloaded errors",
                            _original_model, params.fallback_model,
                            _consecutive_529s,
                        )
                        yield SystemMessage(
                            content=(
                                f"Switched to {params.fallback_model} due to "
                                f"high demand for {_original_model}"
                            ),
                            level="warning",
                            subtype="model_fallback",
                        )
                        _consecutive_529s = 0
                        continue

                    if is_529 and _consecutive_529s > MAX_529_RETRIES:
                        raise
                    if _general_attempts > DEFAULT_MAX_RETRIES:
                        raise

                    _base = RETRY_BASE_DELAY_SECONDS * (2 ** (_general_attempts - 1))
                    # 0-25% jitter (TS getRetryDelay, withRetry.ts:561-566);
                    # capped so a long exponential tail can't exceed the
                    # Retry-After clamp either way.
                    _base = min(_base, 60.0) * (1.0 + random.random() * 0.25)
                    delay = _retry_after_seconds(retry_exc, _base)
                    if is_529:
                        _status = (
                            f"Server overloaded — retrying in {delay:.1f}s "
                            f"(attempt {_consecutive_529s}/{MAX_529_RETRIES})"
                        )
                    else:
                        _status = (
                            f"API error ({classification.error_type}) — "
                            f"retrying in {delay:.1f}s "
                            f"(attempt {_general_attempts}/{DEFAULT_MAX_RETRIES})"
                        )
                    yield SystemMessage(
                        content=_status, level="warning", subtype="api_retry",
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
                        # rather than a retry-lane model_error.
                        raise AbortError("interrupted during retry backoff") from retry_exc
                    continue
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

        # ch01 round-4 WI-2 — PostSampling hook wire (TS query.ts:1079-1089).
        # Placed after the abort check: TS fires before it, but its call is
        # non-blocking (`void …`) so pre-abort is free there; an inline await
        # before the abort return would delay ESC responsiveness by the hook
        # runtime. Consequence: hooks do not fire for user-aborted streams.
        if assistant_messages:
            await _fire_post_sampling_hooks(
                assistant_messages, params.provider, tool_use_context,
            )

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
            from ..services.tool_execution.orchestrator import (
                partition_tool_calls as _diag_partition,
            )
            _batches = _diag_partition(tool_use_blocks, tool_use_context)
            _batch_desc = ", ".join(
                f"[{'parallel' if b.is_concurrency_safe else 'exclusive'}: {[bl.name for bl in b.blocks]}]"
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

        # ch07 round-3 G1 — the unified lane: TS's ungated runTools path
        # (query.ts:1537-1539 else-branch), consumed per query.ts:1541-1565.
        # run_tool_use brings hooks, input backfill (permissions see
        # normalized input), call-input convergence, base-list fallback,
        # error classification, and context modifiers to production.
        from ..services.tool_execution.can_use_tool_adapter import (
            build_can_use_tool,
        )
        from ..services.tool_execution.orchestrator import run_tools

        can_use_tool = build_can_use_tool(tool_use_context)
        tool_results: list[UserMessage] = []
        hook_stopped = False
        async for update in run_tools(
            tool_use_blocks,
            assistant_messages,
            can_use_tool,
            tool_use_context,
        ):
            new_ctx = update.new_context
            if new_ctx is not None and new_ctx is not tool_use_context:
                # A context modifier produced a derived context — adopt
                # it, re-imposing loop-managed state (dataclasses.replace
                # does not carry dynamic attributes). Mirrors TS
                # re-imposing queryTracking at query.ts:1559-1563.
                setattr(new_ctx, "_active_provider", params.provider)
                tool_use_context = new_ctx
            msg = update.message
            if msg is None:
                continue  # batch-end context-only update
            # In-loop hook-stop detection (TS query.ts:1545-1550): the
            # marker rides an ATTACHMENT message, which is yielded to the
            # surface but never collected into tool_results.
            if _is_hook_stopped_continuation(msg):
                hook_stopped = True
            yield msg
            # Collect RAW user-role Message objects (tool results + meta
            # extras) for the next turn's history — normalization stays
            # at API prep, where the unconditional merge + hoist handles
            # the interleaved shape (ch07 G3). STRICT type check: TS
            # filters collected toolResults to type=='user'
            # (query.ts:1552-1557); AttachmentMessage SUBCLASSES
            # UserMessage here (type='attachment') and must NOT enter
            # next-turn history — attachment->history threading is the
            # ch11/ch12 attachment-transformation surface.
            if (
                isinstance(msg, UserMessage)
                and getattr(msg, "type", "user") == "user"
            ):
                tool_results.append(msg)

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

        if params.abort_controller.signal.aborted:
            # NOTE: unlike the streaming-abort path above (which calls
            # ``_yield_missing_tool_result_blocks`` for the in-flight
            # assistant turn), we deliberately do NOT synthesize results
            # for tool_use blocks left unrun by a mid-execution abort.
            # ``normalize_messages_for_api`` → ``ensure_tool_result_pairing``
            # backfills the missing tool_result blocks at API-prep time on
            # the next turn, so the model never sees a dangling tool_use.
            # Do not "fix" this by yielding blocks here without first
            # confirming it doesn't double-pair against that backstop.
            if params.abort_controller.signal.reason != "interrupt":
                yield _create_user_interruption_message(tool_use=True)
            # ch05 round-4 GAP C — TS query.ts:1621-1629: an abort that
            # lands ON the max-turns boundary also announces the limit
            # before the aborted_tools return (the terminal reason stays
            # aborted_tools either way).
            next_turn_count_on_abort = turn_count + 1
            if params.max_turns and next_turn_count_on_abort > params.max_turns:
                yield _create_max_turns_attachment(
                    params.max_turns, next_turn_count_on_abort,
                )
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
        if hook_stopped:
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
