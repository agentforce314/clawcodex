"""Ch5/F.1 — compatibility adapter from query() → AgentLoopResult shape.

The headless and TUI production paths currently invoke
``run_agent_loop()`` (synchronous, no recovery ladder, no stop hooks,
no token budget). This adapter wraps the canonical ``query()`` async
generator and exposes the same return shape as ``run_agent_loop``, so
callers can migrate one entry point at a time.

Per the refactoring plan's F.3 critic-revised decision:
  * **Headless callers** (single-shot ``claude -p``) should wrap the
    adapter in ``asyncio.run()`` — the entry already starts its own
    event loop and runs to completion.
  * **TUI callers** (Textual ``@work(thread=True)`` workers) should
    invoke the adapter via ``asyncio.new_event_loop()`` inside the
    worker thread so the UI's event loop stays free. Do NOT use
    ``@work(thread=False)`` — it would put the loop on Textual's
    main event loop and block UI rendering during model streams.

The adapter does NOT touch ``run_agent_loop`` itself; existing call
sites continue to work until they're migrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..tool_system.context import ToolContext
from ..tool_system.registry import ToolRegistry
from ..providers.base import BaseProvider
from ..types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock
from ..types.messages import (
    AssistantMessage,
    Message,
    UserMessage,
)
from ..utils.abort_controller import AbortController, AbortSignal

from .query import QueryParams, StreamEvent, query
from .transitions import Terminal, TerminalHolder


# Re-use the existing ToolEvent + handler typedefs so callers don't
# have to refactor their event-handling code.
#
# IMPORTANT: import from ``src.tool_system.agent_loop`` directly,
# NOT from ``src.tui.tool_summary_renderers`` — that path causes a
# circular import (src/tui/__init__.py imports app → agent_bridge →
# agent_loop_compat). The TUI re-export module is the canonical
# import path for non-TUI/non-loop callers; for the loop adapter
# itself we go directly to the source.
from ..tool_system.agent_loop import (
    ToolEvent,
    ToolEventHandler,
    TextChunkHandler,
    summarize_tool_use,
    summarize_tool_result,
)


@dataclass(frozen=True)
class AgentLoopRunResult:
    """Adapter result shape. Mirrors the existing
    :class:`src.tool_system.agent_loop.AgentLoopResult` for callers
    that previously consumed ``run_agent_loop``, AND adds a typed
    ``terminal`` so wrappers can discriminate exit reason.
    """
    response_text: str
    usage: dict[str, int]
    num_turns: int
    terminal: Terminal | None = None


async def run_query_as_agent_loop(
    *,
    initial_messages: list[Message],
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    system_prompt: str | list[dict[str, Any]] = "You are a helpful assistant.",
    max_turns: int = 20,
    on_event: ToolEventHandler | None = None,
    on_text_chunk: TextChunkHandler | None = None,
    cancel_signal: AbortSignal | None = None,
    abort_controller: AbortController | None = None,
) -> AgentLoopRunResult:
    """Drive the canonical query() loop and adapt to AgentLoopResult.

    Parameters mirror the existing ``run_agent_loop`` signature where
    practical, but the adapter takes ``initial_messages`` directly
    instead of a ``Conversation`` — the typed messages are the same
    shape query() consumes natively.

    The ``on_event`` callback receives :class:`ToolEvent` instances
    for every tool_use observed in the model's responses and every
    tool_result yielded by the loop. ``on_text_chunk`` receives the
    assistant text in chunks (currently the whole text on
    completion, since streaming-text-chunk wiring is provider-side).

    ``cancel_signal`` is bridged into the loop's abort_controller so
    user-initiated cancels (Ctrl+C, /exit) propagate cleanly. When
    not supplied, the function constructs its own AbortController.
    """
    abort_controller = abort_controller or AbortController()
    if cancel_signal is not None and cancel_signal.aborted:
        # Pre-cancel — bridge the abort upfront so the loop exits early.
        abort_controller.abort(cancel_signal.reason or "user_interrupt")

    params = QueryParams(
        messages=list(initial_messages),
        system_prompt=system_prompt,
        tools=tool_registry.list_tools(),
        tool_registry=tool_registry,
        tool_use_context=tool_context,
        provider=provider,
        abort_controller=abort_controller,
        max_turns=max_turns,
    )

    holder = TerminalHolder()
    response_text_parts: list[str] = []
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    num_turns = 0
    last_assistant_text = ""

    async for msg in query(params, terminal_holder=holder):
        if isinstance(msg, StreamEvent):
            continue

        # Bridge cancel_signal: if it fires mid-stream, propagate to
        # the loop's abort_controller. The loop checks signal.aborted
        # at iteration boundaries so the next check will exit.
        if cancel_signal is not None and cancel_signal.aborted:
            if not abort_controller.signal.aborted:
                abort_controller.abort(
                    cancel_signal.reason or "user_interrupt"
                )

        if isinstance(msg, AssistantMessage):
            num_turns += 1
            # Sum usage across turns.
            mu = getattr(msg, "usage", None) or {}
            usage["input_tokens"] += mu.get("input_tokens", 0)
            usage["output_tokens"] += mu.get("output_tokens", 0)
            # Capture text content and tool_use events.
            text_parts: list[str] = []
            content = msg.content
            if isinstance(content, str):
                text_parts.append(content)
            else:
                for block in content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock) and on_event is not None:
                        on_event(ToolEvent(
                            kind="tool_use",
                            tool_name=block.name,
                            tool_input=block.input,
                            tool_use_id=block.id,
                        ))
            if text_parts:
                last_assistant_text = " ".join(text_parts).strip()
                if on_text_chunk is not None and last_assistant_text:
                    on_text_chunk(last_assistant_text)
            continue

        if isinstance(msg, UserMessage) and on_event is not None:
            # Tool result(s) arrive as UserMessages with ToolResultBlock
            # content. Dispatch as tool_result events.
            content = msg.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        on_event(ToolEvent(
                            kind="tool_result",
                            tool_name="",
                            tool_use_id=block.tool_use_id,
                            tool_output=block.content,
                            is_error=bool(block.is_error),
                            error=str(block.content) if block.is_error else None,
                        ))

    response_text = last_assistant_text or " ".join(response_text_parts).strip()
    return AgentLoopRunResult(
        response_text=response_text,
        usage=usage,
        num_turns=num_turns,
        terminal=holder.value,
    )
