"""Ch5/F.1: Production agent-loop adapter.

:func:`run_query_as_agent_loop` is the canonical entry point that
drives :func:`src.query.query.query` and returns an
:class:`AgentLoopRunResult`. Headless (``src.entrypoints.headless``),
TUI (``src.tui.agent_bridge``), and the integration test suite all
call it (in PR 5 of the chapter-5 stack — until then this module is
introduced but not yet wired into production).

This adapter carries the full chapter 5 recovery stack:

  * PTL recovery via reactive_compact (B.2)
  * Stop hooks (C)
  * Token budget enforcement (D)
  * Model fallback (E.2)
  * Continuation nudge (E.4)
  * Blocking-limit + autocompact-circuit-breaker guards (B.4/B.5)
  * Typed Terminal return value (A.4)

The adapter is intentionally async; synchronous callers wrap with
``asyncio.run(...)`` (headless, integration tests) or run it inside
a worker thread that owns its own event loop (TUI).

Historical note: the module is named ``agent_loop_compat`` because
it originally bridged from the now-legacy ``run_agent_loop``
synchronous loop to the canonical async-generator. After the legacy
loop's removal in PR 5, the adapter is the production entry point —
not a compat shim. The filename is retained for stable import paths.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..agent.conversation import Conversation
from ..providers.base import BaseProvider
from ..tool_system.context import ToolContext
from ..tool_system.registry import ToolRegistry
from ..types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from ..types.messages import AssistantMessage, SystemMessage, UserMessage
from ..utils.abort_controller import (
    AbortController,
    AbortSignal,
    create_abort_controller,
)
from .query import QueryParams, query, run_query
from .transitions import Terminal, TerminalHolder

logger = logging.getLogger(__name__)


# Re-export ToolEvent + handlers so adapter consumers can keep
# importing from a single location.  AgentLoopResult is deliberately
# NOT re-exported — the legacy dataclass was deleted with
# run_agent_loop in PR 5; use AgentLoopRunResult (below) instead.
from ..tool_system.agent_loop import (  # noqa: E402  (intentional re-export)
    TextChunkHandler,
    ToolEvent,
    ToolEventHandler,
)


@dataclass(frozen=True)
class AgentLoopRunResult:
    """Result of :func:`run_query_as_agent_loop`.

    Carries the final assistant text, accumulated usage, and turn
    count — the original ``run_agent_loop``-shaped fields — plus the
    typed :class:`Terminal` so consumers can discriminate exit
    reasons (max_turns vs. completed vs. aborted_streaming etc.).
    """
    response_text: str
    usage: dict[str, int]
    num_turns: int
    terminal: Terminal


def _bridge_cancel_signal_to_abort_controller(
    cancel_signal: AbortSignal | None,
    abort_controller: AbortController,
) -> None:
    """Forward a cancel from the caller-provided AbortSignal into the
    query loop's AbortController. The signal-listener API is the
    minimum bridge — for production we can switch to a richer
    cancellation primitive in a follow-up.
    """
    if cancel_signal is None:
        return

    def _on_signal_aborted(reason: str | None) -> None:
        abort_controller.abort(reason or "cancel_signal")

    try:
        if hasattr(cancel_signal, "add_listener"):
            cancel_signal.add_listener(_on_signal_aborted)
        elif hasattr(cancel_signal, "on_abort"):
            cancel_signal.on_abort(_on_signal_aborted)
        elif cancel_signal.aborted:
            _on_signal_aborted(cancel_signal.reason)
    except Exception:
        logger.exception("Failed to bridge cancel_signal → abort_controller")


def _extract_final_text(yielded_messages: list[Any]) -> str:
    """Find the LAST assistant text content in the stream (matches
    run_agent_loop's ``response_text`` contract).

    Multiple text blocks in a single assistant message are joined
    with ``""`` (empty string) — provider text blocks already carry
    their own line breaks where the model intended them; inserting
    a separator would mangle reconstruction.
    """
    final = ""
    for msg in yielded_messages:
        if not isinstance(msg, AssistantMessage):
            continue
        content = msg.content
        if isinstance(content, str):
            final = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
            if parts:
                final = "".join(parts)
    return final


def _accumulate_usage(yielded_messages: list[Any]) -> dict[str, int]:
    total = {"input_tokens": 0, "output_tokens": 0}
    for msg in yielded_messages:
        usage = getattr(msg, "usage", None)
        if isinstance(usage, dict):
            total["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            total["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
    return total


def _build_effective_system_prompt_for_adapter(tool_context: ToolContext) -> str:
    """Mirrors the legacy run_agent_loop's prompt assembly so the
    adapter does not silently strip the system prompt. Uses the same
    helpers: style prompt + workspace context block. Best-effort —
    if either piece fails to load, returns an empty string and lets
    the loop run with no system prompt rather than crashing.
    """
    try:
        from ..context_system import build_context_prompt
        from ..outputStyles import resolve_output_style
        style_name = getattr(tool_context, "output_style_name", None)
        style_dir = getattr(tool_context, "output_style_dir", None)
        style_prompt = resolve_output_style(style_name, style_dir).prompt
    except Exception:
        return ""

    try:
        context_prompt = build_context_prompt(
            tool_context.workspace_root,
            cwd=tool_context.cwd,
        )
    except Exception:
        context_prompt = ""

    if not context_prompt.strip():
        return style_prompt
    return f"{style_prompt}\n\n{context_prompt}"


def _build_default_pipeline_config(
    provider: BaseProvider,
    tool_context: ToolContext,
) -> Any:
    """Build a PipelineConfig that wires the 5-layer compression
    pipeline AND lets Phase B.5 (autocompact circuit-breaker guard)
    fire — both require pipeline_config to be set on QueryParams.

    Returns None on any unrecoverable assembly error; the caller
    treats None as "no pipeline" (matches the loop's existing
    behavior when pipeline_config is None).
    """
    try:
        from ..services.compact.pipeline import PipelineConfig
        read_file_state: dict[str, Any] = {}
        try:
            for path, fp in tool_context.read_file_fingerprints.items():
                read_file_state[str(path)] = {"timestamp": fp[0]}
        except Exception:
            pass
        return PipelineConfig(
            provider=provider,
            model=getattr(provider, "model", "") or "",
            read_file_state=read_file_state or None,
        )
    except Exception:
        logger.exception("Failed to build default PipelineConfig for adapter")
        return None


async def run_query_as_agent_loop(
    conversation: Conversation,
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    *,
    max_turns: int = 20,
    stream: bool = False,
    verbose: bool = False,
    on_event: ToolEventHandler | None = None,
    on_text_chunk: TextChunkHandler | None = None,
    cancel_signal: AbortSignal | None = None,
    system_prompt: str | None = None,
    pipeline_config: Any = None,
) -> AgentLoopRunResult:
    """Async adapter that drives the canonical :func:`query` loop and
    returns an :class:`AgentLoopRunResult`.

    Kwarg surface (mirrors what the legacy ``run_agent_loop``
    exposed, plus two adapter-only knobs):

    * ``stream`` — ignored (the provider's own ``chat_stream_response``
      handles streaming inside ``_call_model_sync``).
    * ``verbose`` — ignored (verbose printing happens at the
      entrypoint layer, not the loop).
    * ``on_event`` — dispatched per tool_use / tool_result block as
      each message arrives from the loop (real-time). For tool_result
      events, ``tool_name`` is resolved from the matching tool_use's
      name (not the empty string).
    * ``on_text_chunk`` — invoked for each assistant text block as
      messages stream out of the loop. Note: the underlying
      ``_call_model_sync`` reassembles the provider's SSE stream into
      complete AssistantMessage objects per turn, so chunks here are
      "per turn" rather than "per token". For true token-by-token
      live streaming, subscribe at the provider layer instead.
    * ``cancel_signal`` — bridged into a fresh ``AbortController``
      that the loop consults.
    * ``max_turns`` — forwarded as-is.
    * ``tool_context`` — used directly as the loop's
      ``tool_use_context``.

    New kwargs (not in the legacy contract):

    * ``system_prompt`` — explicit override. If None, the adapter
      builds the effective system prompt the same way
      ``run_agent_loop`` does (style + workspace context). Pass an
      empty string to opt out.
    * ``pipeline_config`` — explicit override. If None, the adapter
      builds a default ``PipelineConfig`` so the 5-layer compression
      pipeline runs AND Phase B.5's autocompact circuit-breaker
      guard fires. Pass ``False`` to opt out.

    Returns ``AgentLoopRunResult`` with ``response_text``, ``usage``,
    ``num_turns``, and the typed ``Terminal`` so callers can
    differentiate "completed" from "max_turns" from "aborted_*" etc.
    """
    abort_controller = create_abort_controller()
    _bridge_cancel_signal_to_abort_controller(cancel_signal, abort_controller)

    initial_messages = list(getattr(conversation, "messages", []))

    if system_prompt is None:
        effective_system_prompt = _build_effective_system_prompt_for_adapter(
            tool_context,
        )
    else:
        effective_system_prompt = system_prompt

    if pipeline_config is None:
        effective_pipeline_config = _build_default_pipeline_config(
            provider, tool_context,
        )
    elif pipeline_config is False:
        effective_pipeline_config = None
    else:
        effective_pipeline_config = pipeline_config

    params = QueryParams(
        messages=initial_messages,
        system_prompt=effective_system_prompt,
        tools=tool_registry.list_tools(),
        tool_registry=tool_registry,
        tool_use_context=tool_context,
        provider=provider,
        abort_controller=abort_controller,
        max_turns=max_turns,
        pipeline_config=effective_pipeline_config,
    )

    # Consume the async generator incrementally so `on_event` and
    # `on_text_chunk` fire in real time as messages arrive — not in a
    # single burst at the end.
    holder = TerminalHolder()
    yielded: list[Any] = []
    name_by_id: dict[str, str] = {}

    async for msg in query(params, terminal_holder=holder):
        yielded.append(msg)
        # Per-block dispatch in CONTENT ORDER. Within a single
        # AssistantMessage, TextBlock → on_text_chunk and
        # ToolUseBlock → on_event fire in the order the blocks
        # appear in the message (which is the order the provider
        # emitted them).
        content = getattr(msg, "content", None)
        if (
            on_text_chunk is not None
            and isinstance(msg, AssistantMessage)
            and isinstance(content, str)
            and content
        ):
            try:
                on_text_chunk(content)
            except Exception:
                logger.exception("on_text_chunk raised")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, TextBlock):
                    if (
                        on_text_chunk is not None
                        and isinstance(msg, AssistantMessage)
                        and block.text
                    ):
                        try:
                            on_text_chunk(block.text)
                        except Exception:
                            logger.exception("on_text_chunk raised")
                elif isinstance(block, ToolUseBlock):
                    name_by_id[block.id] = block.name
                    if on_event is not None:
                        try:
                            on_event(ToolEvent(
                                kind="tool_use",
                                tool_name=block.name,
                                tool_input=dict(block.input or {}),
                                tool_use_id=block.id,
                            ))
                        except Exception:
                            logger.exception("on_event raised for tool_use")
                elif isinstance(block, ToolResultBlock):
                    if on_event is not None:
                        try:
                            on_event(ToolEvent(
                                kind="tool_result",
                                tool_name=name_by_id.get(block.tool_use_id, ""),
                                tool_input=None,
                                tool_output=block.content,
                                tool_use_id=block.tool_use_id,
                                is_error=bool(getattr(block, "is_error", False)),
                            ))
                        except Exception:
                            logger.exception("on_event raised for tool_result")

    terminal = holder.value
    if terminal is None:
        terminal = Terminal(
            reason="model_error",
            error=RuntimeError(
                "query() returned without setting Terminal",
            ),
        )

    num_turns = sum(1 for m in yielded if isinstance(m, AssistantMessage))
    response_text = _extract_final_text(yielded)
    usage = _accumulate_usage(yielded)

    return AgentLoopRunResult(
        response_text=response_text,
        usage=usage,
        num_turns=num_turns,
        terminal=terminal,
    )
