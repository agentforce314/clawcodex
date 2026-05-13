"""Agent loop for multi-turn tool calling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import logging

from .registry import ToolRegistry
from .context import ToolContext
from .protocol import ToolCall
from ..agent.conversation import Conversation
from ..services.tool_execution import (
    dispatch_full,
    make_stub_assistant_message,
)
from ..types.content_blocks import TextBlock, ToolUseBlock
from ..types.messages import AssistantMessage as _AssistantMessage
from ..context_system import build_context_prompt
from ..outputStyles import resolve_output_style
from ..providers.base import BaseProvider, ChatResponse
from ..providers.anthropic_provider import AnthropicProvider
from ..providers.minimax_provider import MinimaxProvider
from ..utils.abort_controller import AbortError, AbortSignal

logger = logging.getLogger(__name__)


def _is_anthropic_provider(provider: BaseProvider) -> bool:
    return isinstance(provider, (AnthropicProvider, MinimaxProvider))


def _build_openai_tool_result_content(result_output: Any) -> str:
    """Format tool result as string for OpenAI/GLM."""
    if isinstance(result_output, str):
        return result_output
    return json.dumps(result_output, ensure_ascii=False)

def summarize_tool_result(name: str, output: Any) -> str:
    """Create a concise, single-line summary for tool result output."""
    if not isinstance(output, dict):
        return str(output)
    if name.lower() == "write":
        path = output.get("filePath") or output.get("file_path")
        op = output.get("type")
        return f"{name} · {path} · {op}"
    if name.lower() == "edit":
        path = output.get("filePath") or output.get("file_path")
        replace_all = output.get("replaceAll")
        return f"{name} · {path} · replaceAll={replace_all}"
    if name.lower() == "read":
        if isinstance(output, str):
            if "unchanged" in output.lower():
                return f"{name} · unchanged"
            return f"{name}"
        if output.get("type") == "text" and isinstance(output.get("file"), dict):
            f = output["file"]
            path = f.get("filePath")
            num = f.get("numLines")
            total = f.get("totalLines")
            start = f.get("startLine")
            return f"{name} · {path} · lines={start}-{(start or 1) + (num or 0) - 1}/{total}"
        if output.get("type") == "file_unchanged" and isinstance(output.get("file"), dict):
            return f"{name} · {output['file'].get('filePath')} · unchanged"
        if output.get("type") in {"image", "pdf", "notebook"} and isinstance(output.get("file"), dict):
            return f"{name} · {output['file'].get('filePath')} · {output.get('type')}"
        return f"{name}"
    if name.lower() == "glob":
        n = output.get("numFiles")
        return f"{name} · matches={n}"
    if name.lower() == "grep":
        n = output.get("numFiles")
        mode = output.get("mode")
        return f"{name} · mode={mode} · files={n}"
    if name.lower() == "bash":
        code = output.get("exit_code")
        return f"{name} · exit={code}"
    if name.lower() == "webfetch":
        url = output.get("url")
        ct = output.get("content_type")
        return f"{name} · {url} · {ct}"
    if name.lower() == "websearch":
        q = output.get("query")
        results = output.get("results")
        n = len(results) if isinstance(results, list) else None
        return f"{name} · \"{q}\" · results={n}"
    if name.lower() == "config":
        op = output.get("operation")
        setting = output.get("setting")
        return f"{name} · {op} · {setting}"
    if name.lower() == "taskstop":
        tid = output.get("task_id")
        stopped = output.get("stopped")
        return f"{name} · {tid} · stopped={stopped}"
    if name.lower() == "sendusermessage":
        n = 0
        atts = output.get("attachments")
        if isinstance(atts, list):
            n = len(atts)
        return f"{name} · attachments={n}"
    # default: truncate dict keys for brevity
    keys = ", ".join(list(output.keys())[:3])
    return f"{name} · {keys}"


@dataclass(frozen=True)
class ToolEvent:
    kind: str
    tool_name: str
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    error: str | None = None


@dataclass(frozen=True)
class AgentLoopResult:
    """Result of running the agent loop."""
    response_text: str
    usage: dict[str, Any] | None = None  # {"input_tokens": int, "output_tokens": int}
    num_turns: int = 0


ToolEventHandler = Callable[[ToolEvent], None]
TextChunkHandler = Callable[[str], None]


def _safe_call_handler(handler: ToolEventHandler | None, event: ToolEvent) -> None:
    if handler is None:
        return
    try:
        handler(event)
    except Exception:
        return


def _emit_text_chunks(handler: TextChunkHandler | None, text: str, *, chunk_size: int = 12) -> None:
    """Emit text in small chunks for user-visible streaming without changing loop semantics."""
    if handler is None or not text:
        return
    if chunk_size <= 0:
        chunk_size = len(text)
    for idx in range(0, len(text), chunk_size):
        try:
            handler(text[idx: idx + chunk_size])
        except Exception:
            return


def _call_provider_for_turn(
    *,
    provider: BaseProvider,
    api_messages: list[dict[str, Any]],
    call_kwargs: dict[str, Any],
    stream: bool,
    on_text_chunk: TextChunkHandler | None,
) -> tuple[Any, bool]:
    """Call the provider, preferring structured streaming when available.

    Returns (response, streamed_live_text).
    """
    if stream:
        try:
            response = provider.chat_stream_response(
                api_messages,
                on_text_chunk=on_text_chunk,
                **call_kwargs,
            )
            if not isinstance(response, ChatResponse):
                raise TypeError("Structured streaming must return ChatResponse")
            return response, True
        except NotImplementedError:
            pass
        except AbortError:
            # User-initiated cancel must propagate; do not fall through
            # to the non-streaming code path.
            raise
        except Exception:
            # Preserve existing stable behavior if streaming is unsupported or fails.
            pass

    response = provider.chat(api_messages, **call_kwargs)
    return response, False


def _build_effective_system_prompt(style_prompt: str, tool_context: ToolContext) -> str:
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


def summarize_tool_use(name: str, tool_input: dict[str, Any]) -> str:
    lowered = name.lower()
    if lowered == "bash":
        cmd = tool_input.get("command")
        if isinstance(cmd, str):
            s = cmd.strip().replace("\n", " ")
            return s if len(s) <= 80 else s[:77] + "..."
        return ""
    if lowered in {"read", "write", "edit"}:
        p = tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path")
        if isinstance(p, str):
            extra = ""
            if lowered == "read":
                off = tool_input.get("offset")
                lim = tool_input.get("limit")
                if isinstance(off, int) or isinstance(lim, int):
                    start = off if isinstance(off, int) else 1
                    if isinstance(lim, int):
                        extra = f" · lines {start}-{start + lim - 1}"
            return f"{p}{extra}"
        return ""
    if lowered == "glob":
        pat = tool_input.get("pattern")
        base = tool_input.get("path")
        if isinstance(pat, str) and isinstance(base, str):
            return f"{pat} · {base}"
        if isinstance(pat, str):
            return pat
        return ""
    if lowered == "grep":
        pat = tool_input.get("pattern")
        base = tool_input.get("path")
        if isinstance(pat, str) and isinstance(base, str):
            return f"{pat} · {base}"
        if isinstance(pat, str):
            return pat
        return ""
    if lowered == "webfetch":
        url = tool_input.get("url")
        return url if isinstance(url, str) else ""
    if lowered == "websearch":
        q = tool_input.get("query")
        return q if isinstance(q, str) else ""
    if lowered == "toolsearch":
        q = tool_input.get("query")
        return q if isinstance(q, str) else ""
    if lowered == "askuserquestion":
        qs = tool_input.get("questions")
        if isinstance(qs, list):
            return f"{len(qs)} question(s)"
        return ""
    if lowered == "sendusermessage":
        status = tool_input.get("status")
        return status if isinstance(status, str) else ""
    if lowered in ("agent", "task"):
        # Surface ``@<subagent_type>`` + the user-supplied ``description``
        # so a wall of ``Agent(...)`` calls in a single turn reads as
        # discrete, scannable activity instead of identical placeholders.
        sub = tool_input.get("subagent_type")
        desc = tool_input.get("description")
        parts: list[str] = []
        if isinstance(sub, str) and sub.strip():
            parts.append(f"@{sub.strip()}")
        if isinstance(desc, str) and desc.strip():
            s = desc.strip().replace("\n", " ")
            parts.append(s if len(s) <= 60 else s[:57] + "...")
        return " · ".join(parts)
    return ""



def run_agent_loop(
    conversation: Conversation,
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    max_turns: int = 20,
    stream: bool = False,
    verbose: bool = False,
    on_event: ToolEventHandler | None = None,
    on_text_chunk: TextChunkHandler | None = None,
    cancel_signal: AbortSignal | None = None,
) -> AgentLoopResult:
    """Run agent loop: LLM -> tools -> LLM until no more tools or max turns.

    Args:
        conversation: Conversation with initial user message
        provider: LLM provider
        tool_registry: Tool registry to use
        tool_context: Tool context
        max_turns: Maximum tool turns before stopping
        stream: Whether to stream responses
        verbose: Whether to print tool calls/results
        on_event: Optional callback for tool events
        on_text_chunk: Optional callback for incremental user-visible text chunks
        cancel_signal: Optional :class:`AbortSignal`. When triggered the loop
            stops at the next safe boundary (start of next turn or next tool
            call) and returns a ``[Cancelled]`` result. The signal is also
            checked inside the streaming callback the caller passes.

    Returns:
        AgentLoopResult with final text response, usage info, and turn count
    """
    tool_schemas = []
    for tool in tool_registry.list_tools():
        tool_schemas.append({
            "name": tool.name,
            "description": tool.prompt(),
            "input_schema": dict(tool.input_schema),
        })

    # For OpenAI/GLM, keep separate message list in OpenAI format
    openai_messages: list[dict[str, Any]] = []
    last_user_visible_message: str | None = None
    style_name = getattr(tool_context, "output_style_name", None)
    style_dir = getattr(tool_context, "output_style_dir", None)
    style_prompt = resolve_output_style(style_name, style_dir).prompt
    effective_system_prompt = _build_effective_system_prompt(style_prompt, tool_context)

    # Seed OpenAI messages from initial conversation messages
    for msg in conversation.messages:
        if isinstance(msg.content, str):
            openai_messages.append({"role": msg.role, "content": msg.content})
        else:
            # If there are already block messages, we are probably Anthropic; leave as is
            pass

    # Track usage across all turns
    total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    turn_count = 0

    def _check_cancel() -> None:
        if cancel_signal is not None and cancel_signal.aborted:
            raise AbortError(cancel_signal.reason or "user_interrupt")

    for turn in range(max_turns):
        _check_cancel()
        # WI-5.1: reset the per-message aggregate counter at each turn
        # boundary. The 200K cap is PER USER MESSAGE (the next batch of
        # tool results sent to the model), not cumulative across the
        # whole session. Without this reset the counter grows
        # monotonically and after ~200K of cumulative output every
        # subsequent block force-persists regardless of its individual
        # size. Mirrors ``query.py`` reset at the top of the per-turn
        # loop.
        tool_context.tool_result_chars_so_far = 0
        if _is_anthropic_provider(provider):
            api_messages = conversation.get_messages()
        else:
            # Use OpenAI formatted messages for non-Anthropic
            api_messages = openai_messages

        call_kwargs: dict[str, Any] = {"tools": tool_schemas}
        if _is_anthropic_provider(provider):
            call_kwargs["system"] = effective_system_prompt
        else:
            if turn == 0:
                api_messages = [{"role": "system", "content": effective_system_prompt}, *api_messages]
        response, streamed_live_text = _call_provider_for_turn(
            provider=provider,
            api_messages=api_messages,
            call_kwargs=call_kwargs,
            stream=stream,
            on_text_chunk=on_text_chunk,
        )
        turn_count += 1

        # Collect usage info
        if response.usage:
            total_usage["input_tokens"] += response.usage.get("input_tokens", 0)
            total_usage["output_tokens"] += response.usage.get("output_tokens", 0)

        # Build assistant content for Anthropic or just text for OpenAI
        final_assistant_content = response.content or ""

        if _is_anthropic_provider(provider):
            assistant_blocks: list = []
            if response.content:
                assistant_blocks.append(TextBlock(type="text", text=response.content))

            tool_uses = response.tool_uses or []
            for tool_use in tool_uses:
                assistant_blocks.append(ToolUseBlock(
                    type="tool_use",
                    id=tool_use["id"],
                    name=tool_use["name"],
                    input=tool_use["input"],
                ))

            conversation.add_assistant_message(assistant_blocks if assistant_blocks else "")
        else:
            # Persist assistant text for session history features like /render-last
            # and for subsequent non-Anthropic turns seeded from conversation.
            conversation.add_assistant_message(final_assistant_content)
            # Add assistant message to OpenAI messages (text only)
            openai_assistant_msg: dict[str, Any] = {"role": "assistant", "content": final_assistant_content}
            # DeepSeek/GLM thinking modes require reasoning_content to be replayed
            # on subsequent turns when the assistant response included it.
            if response.reasoning_content:
                openai_assistant_msg["reasoning_content"] = response.reasoning_content
            # If there are tool_uses, add them in OpenAI format
            if response.tool_uses:
                # Build OpenAI tool_calls
                tool_calls = []
                for tu in response.tool_uses:
                    tool_calls.append({
                        "id": tu["id"],
                        "type": "function",
                        "function": {
                            "name": tu["name"],
                            "arguments": json.dumps(tu["input"], ensure_ascii=False)
                        }
                    })
                openai_assistant_msg["tool_calls"] = tool_calls
            openai_messages.append(openai_assistant_msg)

        tool_uses = response.tool_uses or []

        if not tool_uses:
            # No more tools, done
            if stream and final_assistant_content and not streamed_live_text:
                _emit_text_chunks(on_text_chunk, final_assistant_content)
            if (final_assistant_content or "").strip() == "" and last_user_visible_message is not None:
                return AgentLoopResult(
                    response_text=last_user_visible_message,
                    usage=total_usage if total_usage["input_tokens"] > 0 or total_usage["output_tokens"] > 0 else None,
                    num_turns=turn_count,
                )
            return AgentLoopResult(
                response_text=final_assistant_content,
                usage=total_usage if total_usage["input_tokens"] > 0 or total_usage["output_tokens"] > 0 else None,
                num_turns=turn_count,
            )

        # Build a real AssistantMessage carrying the just-emitted
        # tool_use blocks so the full pipeline's hooks can see the
        # originating assistant turn.
        try:
            _amsg_blocks: list = []
            if final_assistant_content:
                _amsg_blocks.append(TextBlock(type="text", text=final_assistant_content))
            for _tu in tool_uses:
                _amsg_blocks.append(ToolUseBlock(
                    type="tool_use",
                    id=_tu["id"],
                    name=_tu["name"],
                    input=_tu["input"],
                ))
            assistant_msg_for_dispatch = _AssistantMessage(content=_amsg_blocks)
        except (TypeError, ValueError):
            # Defense in depth: bug in block construction should fall
            # back to a stub rather than crash the whole loop.
            assistant_msg_for_dispatch = make_stub_assistant_message()

        # Call each tool through the full 13-step pipeline via dispatch_full.
        # This replaces the legacy ToolRegistry.dispatch() shortcut so:
        # - PreToolUse/PostToolUse hooks fire.
        # - Per-tool max_result_size_chars + 200K aggregate budget engage.
        # - ToolResult.new_messages flow into the conversation.
        # - ToolResult.context_modifier mutates tool_context for the
        #   next tool call in this same turn (matches TS serial-batch
        #   semantics — agent_loop runs one tool at a time).
        # - Errors are telemetry-safe-classified into <tool_use_error>.

        # ``current_tool_context`` is the local-mutable view that
        # propagates context_modifier returns to the next tool in this
        # turn. Most modifiers mutate in place and return None (no-op
        # rebind); clone-style modifiers return a new context that we
        # adopt for subsequent dispatches.
        current_tool_context = tool_context
        for tool_use in tool_uses:
            # Two-tier abort handling (Phase 6 audit):
            # 1. ``_check_cancel()`` — agent_loop's caller-supplied
            #    ``cancel_signal`` (e.g., REPL's Ctrl+C handler). Fires
            #    BEFORE the next tool starts.
            # 2. ``dispatch_full → run_tool_use`` checks
            #    ``tool_use_context.abort_controller.signal.aborted`` at
            #    ``tool_execution.py:99-105`` — separate signal that
            #    may be set by hooks or external observers.
            # Mid-execution abort (Ctrl+C while Bash subprocess is
            # running) is NOT yet supported; deferred to a follow-up
            # that moves Bash to ``asyncio.create_subprocess_exec``.
            _check_cancel()
            tool_id = tool_use["id"]
            tool_name = tool_use["name"]
            tool_input = tool_use["input"]

            try:
                _safe_call_handler(
                    on_event,
                    ToolEvent(
                        kind="tool_use",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_use_id=tool_id,
                    ),
                )
                call = ToolCall(name=tool_name, input=tool_input, tool_use_id=tool_id)
                dispatch_result = dispatch_full(
                    call,
                    current_tool_context,
                    assistant_msg_for_dispatch,
                    tools=list(tool_registry.list_tools()),
                )
                # ``result_output`` keeps the typed shape (dict / str /
                # list) so the SendUserMessage / StructuredOutput
                # special cases below continue to read ``.get(...)``
                # from a dict, not a stringified blob.
                result_output = dispatch_result.output
                is_error = dispatch_result.is_error

                # ``tool_result_content`` is the already-budgeted +
                # mapped string that should reach the model.
                # ``map_result_to_api`` + Step-11 persistence have
                # already run inside the pipeline; we route this
                # (not raw ``output``) into the conversation so
                # oversized results carry the <persisted-output>
                # wrapper and empty results carry the no-output marker.
                tool_result_content = dispatch_result.tool_result_block.get(
                    "content", "",
                )

                if tool_name.lower() == "sendusermessage" and isinstance(result_output, dict):
                    msg = result_output.get("message")
                    if isinstance(msg, str):
                        last_user_visible_message = msg
                if tool_name.lower() == "structuredoutput" and isinstance(result_output, dict):
                    payload = result_output.get("structured_output")
                    try:
                        last_user_visible_message = json.dumps(payload, ensure_ascii=False, indent=2)
                    except Exception:
                        last_user_visible_message = str(payload)

                if verbose:
                    use_summary = summarize_tool_use(tool_name, tool_input)
                    if use_summary:
                        print(f"{tool_name} · {use_summary}")
                    summary = summarize_tool_result(tool_name, result_output)
                    print(f"{summary}")

                _safe_call_handler(
                    on_event,
                    ToolEvent(
                        kind="tool_result",
                        tool_name=tool_name,
                        tool_output=result_output,
                        tool_use_id=tool_id,
                        is_error=is_error,
                    ),
                )
                if _is_anthropic_provider(provider):
                    conversation.add_tool_result_message(
                        tool_id, tool_result_content, is_error=is_error,
                    )
                else:
                    # Tool_result_content is already a string for the
                    # OpenAI shape — no further json.dumps needed.
                    if not isinstance(tool_result_content, str):
                        tool_result_content = _build_openai_tool_result_content(
                            tool_result_content,
                        )
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": tool_result_content,
                    })

                # Append any new_messages from the pipeline (AgentTool
                # sub-agent transcripts, hook attachments, system
                # reminders, hook_stopped_continuation attachments)
                # AFTER the primary tool result so they appear in
                # submission order.
                #
                # Use ``append_raw_message`` (not ``add_message``) to
                # preserve subclass-specific fields:
                # - AttachmentMessage's ``attachments`` payload (hook
                #   stop-continuation metadata, sub-agent transcripts)
                # - SystemMessage's ``subtype``/``preventContinuation``
                # - AssistantMessage's ``model``/``usage``/``stop_reason``
                # ``add_message`` round-trips through ``create_message``
                # which drops everything but ``role``+``content``.
                for extra in dispatch_result.new_messages:
                    if hasattr(extra, "role") and hasattr(extra, "content"):
                        try:
                            conversation.append_raw_message(extra)
                        except Exception as extra_exc:  # noqa: BLE001
                            logger.warning(
                                "failed to append new_message after %s (%s): %s",
                                tool_name, tool_id, extra_exc,
                            )
                            continue

                # Apply context_modifier (serial — agent_loop runs one
                # tool at a time, so the next iteration sees the
                # mutated context). Clone-style modifiers return a new
                # context; in-place modifiers return None.
                if dispatch_result.context_modifier is not None:
                    try:
                        new_ctx = dispatch_result.context_modifier(current_tool_context)
                        if new_ctx is not None:
                            current_tool_context = new_ctx
                    except Exception as mod_exc:  # noqa: BLE001
                        # Failed modifier shouldn't crash the loop —
                        # next tool gets the prior context. Log so the
                        # bug is diagnosable.
                        logger.warning(
                            "context_modifier raised on %s (%s); "
                            "continuing with prior context: %s",
                            tool_name, tool_id, mod_exc,
                        )
            except Exception as e:
                error_str = f"Error: {e}"
                if verbose:
                    print(f"[Tool Error] {error_str}")
                _safe_call_handler(
                    on_event,
                    ToolEvent(
                        kind="tool_error",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_use_id=tool_id,
                        is_error=True,
                        error=error_str,
                    ),
                )
                if _is_anthropic_provider(provider):
                    conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                else:
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": error_str
                    })

    # Reached max turns
    return AgentLoopResult(
        response_text="[Max tool turns reached]",
        usage=total_usage if total_usage["input_tokens"] > 0 or total_usage["output_tokens"] > 0 else None,
        num_turns=turn_count,
    )
