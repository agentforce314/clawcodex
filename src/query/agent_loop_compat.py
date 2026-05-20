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
from ..utils.abort_controller import AbortController, AbortError, AbortSignal

from .query import QueryParams, StreamEvent, query
from .transitions import Terminal, TerminalHolder


# Renderer types are now canonically in ``src.tool_system.renderers``
# (per the F.4 extraction in PR #N). ``src/tui/__init__.py`` doesn't
# load the renderers module on import, so no circular hazard.
from ..tool_system.renderers import (
    ToolEvent,
    ToolEventHandler,
    TextChunkHandler,
    summarize_tool_use,
    summarize_tool_result,
)


def build_effective_system_prompt(style_prompt: str, tool_context: ToolContext) -> str:
    """Assemble the cold-start system prompt for headless+TUI cutover.

    Combines the user's output-style prompt with the workspace
    context block (CLAUDE.md, git status, cwd) produced by
    ``build_context_prompt``. Lives here because the only callers are
    the F.2/F.3 cutover code in ``src/entrypoints/headless.py`` and
    ``src/tui/agent_bridge.py``; the canonical query() loop expects
    the system_prompt pre-built (per its QueryParams.system_prompt
    contract) so the cutover code uses this helper to match what
    the legacy ``run_agent_loop`` did internally.
    """
    # Local import — context_system is a heavier dep; only the cutover
    # callers need it, no need to drag it into agent_loop_compat's
    # import time.
    from ..context_system import build_context_prompt
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
    on_message: Callable[[Message], None] | None = None,
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
    tool_result yielded by the loop.

    ``on_text_chunk`` is forwarded into the QueryParams so the
    provider's streaming layer fires text chunks LIVE (per-delta).
    Callers MUST provide this if they need real-time text rendering
    (TUI live streaming, ESC-mid-stream cancel teardown).

    ``on_message`` is fired for EVERY :class:`Message` yielded by the
    loop (Anthropic-shape AssistantMessage with full content blocks
    including tool_use, UserMessage with tool_result blocks, etc.).
    Use this to persist the full conversation transcript faithfully —
    `response_text` alone loses tool_use/tool_result structure across
    multi-turn sessions.

    ``cancel_signal`` is bridged into the loop's abort_controller so
    user-initiated cancels (Ctrl+C, /exit) propagate cleanly. When
    not supplied, the function constructs its own AbortController.
    """
    # Critic C2 fix: do NOT mint a fresh controller when the caller
    # provided one. The provider's chat_stream_response listens on
    # ``QueryParams.abort_controller.signal`` to tear down HTTP streams
    # mid-flight on ESC. A fresh controller breaks that wiring — ESC
    # would flip the user's signal but the provider would never see it
    # because the per-message bridge below only fires when query()
    # yields a message, and a tool-use-only turn yields nothing during
    # the multi-second generation. Caller's controller IS the user's
    # signal source; reuse it.
    if abort_controller is None:
        if cancel_signal is not None:
            # We received only the signal, not its owning controller.
            # Mint a new controller and bridge cancellation into it
            # both pre- and per-iteration (legacy fallback path).
            abort_controller = AbortController()
            if cancel_signal.aborted:
                abort_controller.abort(
                    cancel_signal.reason or "user_interrupt"
                )
        else:
            abort_controller = AbortController()

    params = QueryParams(
        messages=list(initial_messages),
        system_prompt=system_prompt,
        tools=tool_registry.list_tools(),
        tool_registry=tool_registry,
        tool_use_context=tool_context,
        provider=provider,
        abort_controller=abort_controller,
        max_turns=max_turns,
        # Critic-flagged: forward on_text_chunk into QueryParams so
        # the provider's chat_stream_response fires chunks LIVE. The
        # adapter must NOT call on_text_chunk(full_text) once at the
        # end — that breaks TUI live streaming AND the
        # ESC-mid-stream-cancel path which relies on the chunk
        # callback raising AbortError from inside the SDK stream.
        on_text_chunk=on_text_chunk,
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
            # Critic S1 fix: skip persisting API-error messages and
            # meta messages. The legacy run_agent_loop never added
            # these to the user's Conversation — letting them through
            # poisons multi-prompt sessions (the model sees prior
            # error text as its own past output, and PTL errors
            # persisted as assistant messages fertilize a PTL death
            # spiral on the next turn).
            _is_api_error = bool(getattr(msg, "isApiErrorMessage", False))
            _is_meta = bool(getattr(msg, "isMeta", False))
            if on_message is not None and not _is_api_error and not _is_meta:
                on_message(msg)
            if _is_api_error:
                # Don't count error turns or surface their text as the
                # "response" — those are exit signals, not output.
                continue
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
                # NB: do NOT fire on_text_chunk here. It was already
                # fired LIVE by the provider's chat_stream_response
                # via QueryParams.on_text_chunk threading. Firing
                # again would duplicate the entire response into the
                # caller's stream.
            continue

        if isinstance(msg, UserMessage):
            # Tool result(s) arrive as UserMessages with ToolResultBlock
            # content. Persist the full message (so the next turn's
            # API call can pair tool_use IDs to their results) AND
            # dispatch tool_result events. Skip meta (interruption /
            # cancellation synthesized by query.py) — those are loop
            # bookkeeping, not real user turns.
            if bool(getattr(msg, "isMeta", False)):
                continue
            content = msg.content
            if isinstance(content, list):
                has_tool_result = any(
                    isinstance(block, ToolResultBlock) for block in content
                )
                if has_tool_result and on_message is not None:
                    on_message(msg)
                if on_event is not None:
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

    # Critic C1 fix: surface terminal abort/error reasons as exceptions
    # so callers' existing ``except AbortError`` / ``except Exception``
    # paths fire. Without this, ESC during the loop sets a Terminal but
    # the adapter returns normally — headless reports exit 0 instead of
    # 130 with subtype:cancelled, TUI shows a blank response instead of
    # "Cancelled by user". Mirrors the AbortError raise legacy
    # run_agent_loop did at agent_loop.py:345-347.
    terminal = holder.value
    if terminal is not None:
        reason = getattr(terminal, "reason", None) or ""
        if reason in ("aborted_streaming", "aborted_tools", "interrupted"):
            raise AbortError(
                getattr(abort_controller.signal, "reason", None)
                or reason
                or "user_interrupt"
            )

    response_text = last_assistant_text or " ".join(response_text_parts).strip()
    return AgentLoopRunResult(
        response_text=response_text,
        usage=usage,
        num_turns=num_turns,
        terminal=holder.value,
    )
