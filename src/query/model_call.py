"""模型能力解析与 provider 调用边界。

该模块属于 ``B3. Query refactor I``：只提取模型调用职责，不改变
canonical Query 的重试、恢复、事件顺序或终态控制流。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Callable

from ..providers.base import BaseProvider, ChatResponse
from ..tool_system.build_tool import Tools
from ..types.content_blocks import TextBlock, ToolUseBlock
from ..types.messages import AssistantMessage, Message
from ..utils.abort_controller import AbortError
from ..utils.image_validation import ImageSizeError

logger = logging.getLogger(__name__)

PROMPT_TOO_LONG_ERROR_MESSAGE = (
    "Your conversation is too long. Please use /compact to reduce context size, "
    "or start a new conversation."
)


def _create_assistant_api_error_message(
    content: str,
    *,
    error: str | None = None,
) -> AssistantMessage:
    message = AssistantMessage(content=content, isApiErrorMessage=True)
    message._api_error = error  # type: ignore[attr-defined]
    return message

_THINKING_ELIGIBLE_MODEL_PATTERN = re.compile(
    r"claude-(?:sonnet|opus|haiku|fable)-(?:4-\d+|[5-9]\b|\d{2,})",
    re.IGNORECASE,
)

VALID_THINKING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def _model_supports_extended_thinking(model: str | None) -> bool:
    if not model:
        return False
    return bool(_THINKING_ELIGIBLE_MODEL_PATTERN.search(model))


def _model_supports_adaptive_thinking(model: str | None) -> bool:
    if not model:
        return False
    value = model.lower()
    return any(
        name in value
        for name in (
            "fable-5", "opus-5", "opus-4-8", "opus-4-7",
            "opus-4-6", "sonnet-4-6",
        )
    )


def _model_supports_effort(model: str | None) -> bool:
    if not model:
        return False
    value = model.lower()
    return any(
        name in value
        for name in ("fable-5", "opus-5", "opus-4-8", "opus-4-6", "sonnet-4-6")
    )


def _model_supports_xhigh_effort(model: str | None) -> bool:
    if not model:
        return False
    value = model.lower()
    return "opus-5" in value or "opus-4-8" in value or "fable-5" in value


def resolve_thinking_effort(
    explicit: str | None,
    model: str | None,
    *,
    clamp_xhigh: bool = True,
) -> str | None:
    """按“显式会话值 → settings → 省略”解析 reasoning effort。"""

    value = (explicit or "").strip().lower()
    if value not in VALID_THINKING_EFFORT_LEVELS:
        from ..settings.settings import get_settings

        try:
            value = (get_settings().effort or "").strip().lower()
        except Exception:  # noqa: BLE001 — settings 不能破坏模型请求
            value = ""
    if value not in VALID_THINKING_EFFORT_LEVELS:
        return None
    if value == "xhigh" and clamp_xhigh and not _model_supports_xhigh_effort(model):
        logger.debug("effort xhigh not supported on %s; sending high instead", model)
        return "high"
    return value


def normalize_effort_for_provider(
    provider: Any,
    resolved_effort: str | None,
) -> str | None:
    """把统一 effort 值映射为 provider 自己的词汇，并校验返回值。"""

    if resolved_effort is None:
        return None
    normalize = getattr(provider, "normalize_reasoning_effort", None)
    if not callable(normalize):
        return resolved_effort
    try:
        mapped = normalize(resolved_effort)
    except Exception:  # noqa: BLE001 — effort 映射失败不得阻断请求
        return resolved_effort
    if isinstance(mapped, str) and mapped in VALID_THINKING_EFFORT_LEVELS:
        return mapped
    return resolved_effort


def build_anthropic_thinking_kwargs(
    model: str | None,
    *,
    explicit_effort: str | None = None,
    max_tokens: int = 0,
    force_thinking: bool = False,
) -> dict[str, Any]:
    """构建 Anthropic wire 的 thinking/output_config 参数。"""

    result: dict[str, Any] = {}
    if not (force_thinking or _model_supports_extended_thinking(model)):
        return result
    if _model_supports_adaptive_thinking(model):
        result["thinking"] = {"type": "adaptive"}
    else:
        resolved_max = int(max_tokens or 0)
        if resolved_max > 1024:
            result["thinking"] = {
                "type": "enabled",
                "budget_tokens": max(1024, resolved_max - 1),
            }
        else:
            logger.debug(
                "thinking omitted: max_tokens=%s too small for a valid budget on %s",
                resolved_max,
                model,
            )
    if _model_supports_effort(model):
        effort = resolve_thinking_effort(explicit_effort, model)
        if effort is not None:
            result["output_config"] = {"effort": effort}
    return result


async def invoke_provider(
    *,
    provider: BaseProvider,
    api_messages: list[dict[str, Any]],
    call_kwargs: dict[str, Any],
    abort_signal: Any = None,
    on_text_chunk: Callable[[str], None] | None = None,
    on_thinking_chunk: Callable[[str], None] | None = None,
    diagnostic: bool = False,
) -> ChatResponse:
    """执行一次 provider 调用；保留 streaming→chat 的既有回退语义。"""

    def call() -> ChatResponse:
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
                    try:
                        return provider.chat_stream_response(
                            api_messages,
                            on_text_chunk=on_text_chunk,
                            abort_signal=abort_signal,
                            **call_kwargs,
                        )
                    except TypeError:
                        return provider.chat_stream_response(
                            api_messages,
                            abort_signal=abort_signal,
                            **call_kwargs,
                        )
            return provider.chat_stream_response(
                api_messages,
                abort_signal=abort_signal,
                **call_kwargs,
            )
        except (NotImplementedError, AttributeError):
            if diagnostic:
                logger.warning("模型 streaming 不可用，回退到 chat()")
            response = provider.chat(api_messages, **call_kwargs)
            if on_text_chunk is not None and response.content:
                from ..tool_system.renderers import _emit_text_chunks

                _emit_text_chunks(on_text_chunk, response.content)
            return response

    # 后台调用离开事件循环线程；交互式 callback 保持原线程语义。
    if on_text_chunk is None:
        return await asyncio.to_thread(call)
    return call()

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
    thinking_effort: str | None = None,
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
    from ..tool_system.tool_search import (
        TOOL_SEARCH_BETA_HEADER_1P,
        filter_tools_for_request,
        is_deferred_tool,
    )

    provider_model = getattr(provider, "model", None) or ""
    request_tools = filter_tools_for_request(tools, provider_model, api_messages)
    deferred_tool_names = sorted(
        tool.name
        for tool in tools
        if is_deferred_tool(tool)
        and (
            not callable(getattr(tool, "is_enabled", None))
            or tool.is_enabled()
        )
        and tool not in request_tools
    )
    if deferred_tool_names:
        api_messages = [
            {
                "role": "user",
                "content": (
                    "<available-deferred-tools>\n"
                    + "\n".join(deferred_tool_names)
                    + "\n</available-deferred-tools>"
                ),
            },
            *api_messages,
        ]
    tool_schemas = []
    for tool in request_tools:
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

    from ..providers import is_anthropic_wire
    from ..providers.anthropic_provider import AnthropicProvider

    is_anthropic = is_anthropic_wire(provider)
    if deferred_tool_names and isinstance(provider, AnthropicProvider):
        has_custom_endpoint = getattr(provider, "has_custom_endpoint", None)
        if not callable(has_custom_endpoint) or not has_custom_endpoint():
            call_kwargs.setdefault("betas", []).append(
                TOOL_SEARCH_BETA_HEADER_1P
            )
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
            max_output_tokens_override,
            getattr(provider, "model", None),
            base_url=getattr(provider, "base_url", None),
        )
    elif max_output_tokens_override is not None:
        call_kwargs["max_tokens"] = max_output_tokens_override

    # Extended thinking (Claude 4.x family). Forwarded straight through
    # the provider's kwargs pass-through to client.messages.stream(
    # thinking=..., output_config=...). Off-API on older Claude versions
    # and on non-Anthropic providers, so guarded by both. ``None`` =
    # auto-enable; ``True`` / ``False`` = caller override.
    #
    # The adaptive-vs-budget selection mirrors TS claude.ts:1612-1640:
    # models that support thinking AND the adaptive type get
    # ``{type: "adaptive"}``; models that support thinking but NOT adaptive
    # (Sonnet 4.5, Haiku 4.5, older Opus 4.x, …) get an explicit
    # ``{type: "enabled", budget_tokens: N}`` instead — sending adaptive to
    # them is a hard 400 ("adaptive thinking is not supported on this
    # model"). ``output_config.effort`` is gated separately and even more
    # narrowly (Opus 4.6 / Sonnet 4.6 only). Getting either gate wrong
    # breaks every request on the affected model — including the whole
    # subscription/OAuth path, which is how this surfaced.
    if extended_thinking is not False and is_anthropic:
        provider_model = getattr(provider, "model", None) or call_kwargs.get("model")
        call_kwargs.update(
            build_anthropic_thinking_kwargs(
                provider_model,
                explicit_effort=thinking_effort,
                max_tokens=int(call_kwargs.get("max_tokens") or 0),
                force_thinking=extended_thinking is True,
            )
        )
    elif not is_anthropic:
        # NON-Anthropic wire: reasoning effort is a top-level
        # ``reasoning_effort`` body field, NOT ``output_config``. This is the
        # ONLY site that applies effort for this family — the interactive path
        # hands the level down as ``thinking_effort`` and does not wrap the
        # provider (see ``AgentSession._turn_effort_routing``, which used to
        # wrap it in an ``_EffortProvider`` and collided with this branch).
        # One level, one injection site: two of them silently inverted the
        # documented precedence, with ``settings.effort`` beating an explicit
        # session ``/effort``.
        #
        # Before this branch existed, effort was emitted only on the Anthropic
        # side, so ``--effort`` on the headless ``-p`` path (the one the
        # terminal-bench harness drives) was a SILENT no-op for every
        # OpenAI-compatible provider. Verified 2026-07-31 against a capture
        # server: ``--effort max --provider openrouter`` produced a body of
        # {messages, model, stream, stream_options, tools}, no effort field.
        #
        # Not gated on ``_model_supports_effort``: that allowlist is a list of
        # Anthropic model names for the ``output_config`` parameter and matches
        # nothing here. Gating on it would reintroduce the silent drop.
        #
        # SCOPE — this is ``not is_anthropic``, which is broader than
        # "OpenAI-compatible": Gemini lands here too, and its provider picks
        # named kwargs out of ``**kwargs`` rather than forwarding
        # ``extra_body``, so effort is still dropped there. Harmless (no
        # 400), but it means Gemini keeps the silent-no-op bug this branch
        # exists to kill; fixing it needs Gemini's own generation-config
        # shape, not this field.
        #
        # Every real OpenAI-compatible provider does forward it: the base
        # ``chat``/``chat_stream``/``_stream_attempt`` splat leftover kwargs
        # into ``client.chat.completions.create``, which handles ``extra_body``
        # natively, and openrouter/zai/deepseek add no overrides. The
        # ChatGPT-subscription path reads it back out of ``extra_body``
        # instead (openai_provider ``_subscription_reasoning_effort``) rather
        # than forwarding it into a Responses body that would reject it.
        # Providers that simply don't know the field ignore it (probed
        # 2026-07-31: deepseek-v4-pro and glm-5.2 both accept it without error).
        #
        # ``setdefault`` so an explicit caller-supplied extra_body wins.
        resolved_effort = resolve_thinking_effort(
            thinking_effort,
            getattr(provider, "model", None) or call_kwargs.get("model"),
            clamp_xhigh=False,
        )
        # Translate onto the provider's own vocabulary before it hits the
        # wire. The ladder above is Anthropic's; a provider that does not
        # share it silently DISCARDS the unknown level and applies its own
        # default, so the request goes out looking fine and the user gets a
        # level they did not choose. Identity for providers that take the
        # full ladder (OpenAI, OpenRouter) — see
        # ``BaseProvider.normalize_reasoning_effort``.
        #
        # No explicit unwrap here, deliberately: ``FusionProvider.__getattr__``
        # already delegates unknown attributes to its base, so this lookup
        # lands on the BASE provider's method and its vocabulary. (Contrast
        # ``is_anthropic_wire``, which cannot delegate — an ``isinstance``
        # test sees the wrapper's class and needs ``unwrap_provider``.) The
        # delegation is pinned by a test rather than left incidental.
        # The result is VALIDATED before use, not trusted. This is a duck-typed
        # ``getattr`` on whatever object the caller passed, and providers are
        # not all real ``BaseProvider`` subclasses — mocks, gateway shims and
        # third-party wrappers all reach here. A ``MagicMock`` in particular
        # answers every attribute with a callable returning another Mock, so an
        # unguarded assignment writes a ``<MagicMock ...>`` repr into the
        # request body. Anything that is not a plain string on the known ladder
        # is discarded in favour of the level we already resolved.
        resolved_effort = normalize_effort_for_provider(provider, resolved_effort)
        if resolved_effort is not None:
            extra_body = dict(call_kwargs.get("extra_body") or {})
            extra_body.setdefault("reasoning_effort", resolved_effort)
            call_kwargs["extra_body"] = extra_body

    # TS callModel() uses SSE streaming for faster first-byte latency and
    # progressive text display.  Use chat_stream_response() which streams
    # internally and reassembles the full ChatResponse.  Fall back to the
    # synchronous chat() if the provider doesn't support structured streaming.
    if _diag:
        logger.warning("[DIAG] _call_model_sync: calling provider (streaming)...")
    try:
        response = await invoke_provider(
            provider=provider,
            api_messages=api_messages,
            call_kwargs=call_kwargs,
            abort_signal=abort_signal,
            on_text_chunk=on_text_chunk,
            on_thinking_chunk=on_thinking_chunk,
            diagnostic=_diag,
        )
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

        # Ch5/B.1 — tag media errors so the loop can withhold them and (in
        # B.2) route through media recovery. Mirrors TS
        # `isWithheldMediaSizeError` at query.ts:892. `is_media_size_error`
        # expects a str (substring match), so pass error_str explicitly.
        #
        # RETRYABLE ERRORS FIRST. This branch RETURNS a tagged message rather
        # than re-raising, which takes the request out of the retry lane
        # entirely (``categorize_retryable_api_error`` only ever sees
        # exceptions that propagate out of this function). So a 429 or a 5xx
        # whose body happens to mention images — "Rate limit reached for
        # images: ..." is real provider wording — would be converted from
        # "back off and retry" into a non-retryable media terminal. Classify
        # on transport/status BEFORE matching on prose.
        from ..services.api.errors import (
            is_media_size_error,
            is_overloaded_error,
            is_rate_limit_error,
        )

        _status = getattr(e, "status", getattr(e, "status_code", None))
        _retryable = (
            is_rate_limit_error(e)
            or is_overloaded_error(e)
            or (isinstance(_status, int) and _status >= 500)
        )
        if is_media_size_error(error_str) and not _retryable:
            err_msg = _create_assistant_api_error_message(
                # "too large" was wrong for a COUNT rejection, which is what
                # this branch most often sees; the operator reads this string.
                f"Media rejected: {error_str}",
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

    # Signed Anthropic thinking must precede the text/tool blocks exactly as
    # returned.  Keeping it in message history both preserves reasoning state
    # across tool turns and makes it available to stream-json/session logs.
    if response.thinking_blocks:
        from ..types.content_blocks import content_block_from_dict

        for raw in response.thinking_blocks:
            assistant_blocks.append(content_block_from_dict(raw))

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
