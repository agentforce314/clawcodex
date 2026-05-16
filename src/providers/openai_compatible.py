"""OpenAI-compatible provider base class.

This base class consolidates shared logic for providers that use the
OpenAI-style /chat/completions API (OpenAI, GLM, Minimax, etc.).
"""

from __future__ import annotations

import json
from abc import abstractmethod
from typing import Any, Generator, Optional

from .base import BaseProvider, ChatResponse, MessageInput, TextChunkCallback


def _convert_anthropic_messages_to_openai(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic-format messages to OpenAI chat-completion format.

    Handles two transformations:
    1. Assistant messages with tool_use content blocks → assistant + tool_calls
    2. User messages with tool_result content blocks → role=tool messages
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Pass through simple string content and system messages
        if isinstance(content, str) or role == "system":
            result.append(msg)
            continue

        if not isinstance(content, list):
            result.append(msg)
            continue

        # --- assistant with tool_use blocks ---
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            other_blocks: list[dict[str, Any]] = []

            for block in content:
                if not isinstance(block, dict):
                    other_blocks.append(block)
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    inp = block.get("input", {})
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(inp) if isinstance(inp, dict) else str(inp),
                        },
                    })
                else:
                    other_blocks.append(block)

            if tool_calls:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": tool_calls,
                }
                # DeepSeek thinking mode requires reasoning_content to be
                # replayed together with assistant tool call messages.
                reasoning_content = msg.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
                text_content = "\n".join(text_parts) if text_parts else None
                if text_content:
                    assistant_msg["content"] = text_content
                else:
                    assistant_msg["content"] = None
                result.append(assistant_msg)
            else:
                # No tool calls, keep original
                result.append(msg)
            continue

        # --- user with tool_result blocks ---
        if role == "user":
            tool_results: list[dict[str, Any]] = []
            non_tool_blocks: list[dict[str, Any]] = []

            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append(block)
                else:
                    non_tool_blocks.append(block)

            # Emit non-tool content first as user message
            if non_tool_blocks:
                result.append({"role": "user", "content": non_tool_blocks})

            # Emit each tool_result as a separate role=tool message
            for tr in tool_results:
                raw_content = tr.get("content", "")
                if isinstance(raw_content, list):
                    # Flatten nested content blocks to text
                    parts = []
                    for item in raw_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                        elif isinstance(item, str):
                            parts.append(item)
                        else:
                            parts.append(json.dumps(item) if isinstance(item, dict) else str(item))
                    flat_content = "\n".join(parts) if parts else ""
                elif isinstance(raw_content, str):
                    flat_content = raw_content
                else:
                    flat_content = str(raw_content)

                result.append({
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id", ""),
                    "content": flat_content,
                })
            continue

        # Fallback
        result.append(msg)
    return result


def _convert_to_openai_tool_schema(anthropic_tool: dict[str, Any]) -> dict[str, Any] | None:
    """Convert Anthropic tool schema to OpenAI/GLM/Minimax function format.

    Returns None if the schema is invalid (missing type, type is None, or other issues).
    """
    input_schema = anthropic_tool.get("input_schema")
    if not input_schema or not isinstance(input_schema, dict):
        return None
    schema_type = input_schema.get("type")
    if schema_type is None or schema_type == "None":
        return None
    # Some providers (Azure) require type=object to have properties
    if schema_type == "object" and "properties" not in input_schema and "anyOf" not in input_schema and "oneOf" not in input_schema:
        # Try to add an empty properties dict if none provided
        input_schema = {**input_schema, "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": anthropic_tool["name"],
            "description": anthropic_tool.get("description", ""),
            "parameters": input_schema,
        },
    }


class OpenAICompatibleProvider(BaseProvider):
    """Base class for providers using OpenAI-style chat completions API.

    Subclasses must implement:
    - _create_client(): Create and return the provider-specific SDK client
    - get_available_models(): Return list of available model names

    The client is created lazily on first use.
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """Initialize OpenAI-compatible provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL for API endpoint
            model: Default model to use
        """
        super().__init__(api_key, base_url, model)
        self._client: Optional[Any] = None

    @abstractmethod
    def _create_client(self) -> Any:
        """Create the provider-specific SDK client.

        Returns:
            An instance of the provider's SDK client.
        """
        pass

    @property
    def client(self) -> Any:
        """Get or create the SDK client (lazy initialization)."""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _prepare_messages(self, messages: list[MessageInput]) -> list[dict[str, Any]]:
        """Convert messages to OpenAI format, translating Anthropic tool blocks."""
        base = super()._prepare_messages(messages)
        return _convert_anthropic_messages_to_openai(base)

    def _build_usage_dict(self, usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        return {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> ChatResponse:
        """Synchronous chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas (Anthropic format)
            **kwargs: Additional parameters

        Returns:
            Chat response
        """
        model = self._get_model(**kwargs)

        # Convert messages
        provider_messages = self._prepare_messages(messages)

        # Convert tools to OpenAI format
        extra_kwargs: dict[str, Any] = {}
        if tools:
            converted = [_convert_to_openai_tool_schema(t) for t in tools]
            extra_kwargs["tools"] = [t for t in converted if t is not None]

        # Make API call
        response = self.client.chat.completions.create(
            model=model,
            messages=provider_messages,
            **extra_kwargs,
            **{k: v for k, v in kwargs.items() if k not in ["model", "tools"]},
        )

        # Extract content
        choice = response.choices[0]

        # Handle reasoning content (GLM specific, but harmless for others)
        reasoning_content: Optional[str] = None
        if (
            hasattr(choice.message, "reasoning_content")
            and choice.message.reasoning_content
        ):
            reasoning_content = choice.message.reasoning_content

        # Extract tool calls (OpenAI format -> Anthropic format)
        tool_uses: Optional[list[dict[str, Any]]] = None
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_uses = []
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except Exception:
                    args = {}
                tool_uses.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        return ChatResponse(
            content=choice.message.content or "",
            model=response.model,
            usage=self._build_usage_dict(getattr(response, "usage", None)),
            finish_reason=choice.finish_reason,
            reasoning_content=reasoning_content,
            tool_uses=tool_uses,
        )

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """Streaming chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas (Anthropic format)
            **kwargs: Additional parameters

        Yields:
            Chunks of response content
        """
        model = self._get_model(**kwargs)

        # Convert messages
        provider_messages = self._prepare_messages(messages)

        # Convert tools to OpenAI format
        extra_kwargs: dict[str, Any] = {}
        if tools:
            converted = [_convert_to_openai_tool_schema(t) for t in tools]
            extra_kwargs["tools"] = [t for t in converted if t is not None]

        # Stream API call
        stream = self.client.chat.completions.create(
            model=model,
            messages=provider_messages,
            stream=True,
            **extra_kwargs,
            **{k: v for k, v in kwargs.items() if k not in ["model", "tools"]},
        )

        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        abort_signal: Any = None,
        **kwargs
    ) -> ChatResponse:
        """Stream OpenAI-compatible chunks while rebuilding the final response.

        ESC-cancellation runs the SDK iteration on a daemon worker
        thread that pushes chunks into a ``queue.Queue``. The main
        thread polls the queue with a 100 ms timeout and re-checks
        ``guard.aborted`` between ticks. On abort the main thread
        raises ``AbortError`` immediately and orphans the worker —
        the worker dies when the underlying connection eventually
        closes.

        Why the worker indirection (vs. the simpler in-loop check
        used in earlier revisions): the OpenAI Python SDK uses sync
        ``httpx`` for streaming, and ``response.close()`` from
        another thread is purely advisory. For LiteLLM-proxied
        connections (and certain other httpx + chunked-transfer
        configurations) the SDK's blocking socket read doesn't
        actually return when the response is "closed" — it keeps
        consuming bytes. Unlike JavaScript's native ``fetch +
        AbortSignal`` integration (which the TypeScript reference at
        ``typescript/src/services/api/openaiShim.ts`` uses), Python
        has no portable way to make a sync blocking read honor an
        abort from another thread, so the worker exists to keep the
        main thread's response time independent of the SDK's
        cooperation.
        """
        from ._stream_abort import StreamAbortGuard

        guard = StreamAbortGuard(abort_signal)
        guard.raise_if_pre_aborted()

        model = self._get_model(**kwargs)
        provider_messages = self._prepare_messages(messages)

        extra_kwargs: dict[str, Any] = {}
        if tools:
            converted = [_convert_to_openai_tool_schema(t) for t in tools]
            extra_kwargs["tools"] = [t for t in converted if t is not None]

        # ``stream_options.include_usage`` opts the OpenAI streaming API
        # into emitting a final ``usage`` chunk; without it, ``chunk.usage``
        # is always ``None`` and the rebuilt ChatResponse has empty token
        # counts. The spinner row + ``/stats`` rely on this — see
        # ``_build_usage_dict`` below and the consumer in
        # ``src/query/query.py``.
        stream_kwargs = {k: v for k, v in kwargs.items() if k not in ["model", "tools"]}
        existing_stream_options = stream_kwargs.pop("stream_options", None) or {}
        stream_kwargs["stream_options"] = {
            **existing_stream_options,
            "include_usage": True,
        }
        stream = self.client.chat.completions.create(
            model=model,
            messages=provider_messages,
            stream=True,
            **extra_kwargs,
            **stream_kwargs,
        )

        content_parts: list[str] = []
        response_model = model
        finish_reason = "stop"
        reasoning_parts: list[str] = []
        usage_obj: Any = None
        tool_calls_by_index: dict[int, dict[str, str]] = {}

        # Worker-thread iteration. The OpenAI Python SDK uses sync
        # ``httpx`` for streaming, and ``response.close()`` from another
        # thread is best-effort — for LiteLLM-proxied connections (and
        # some other httpx configurations) the SDK's blocking socket
        # read doesn't actually return when the response is closed.
        # Unlike JavaScript's native ``fetch + AbortSignal`` integration
        # (which the TypeScript reference uses), Python has no portable
        # way to make a sync blocking read honor an abort from another
        # thread.
        #
        # Workaround: hoist the iteration onto a daemon worker thread
        # that pushes chunks into a queue. The main thread polls the
        # queue with a short timeout and re-checks ``guard.aborted``
        # each tick. On abort we raise ``AbortError`` immediately and
        # orphan the worker — it'll die when the underlying connection
        # eventually closes (server-side, idle timeout, or the SDK's
        # natural exhaustion). The cost is some wasted bandwidth on
        # the orphaned read; the benefit is that the user's prompt
        # comes back in ~100 ms regardless of LiteLLM/httpx behavior.
        import queue as _queue
        import threading as _threading

        _DONE = object()
        chunk_queue: _queue.Queue = _queue.Queue()

        def _drain_stream() -> None:
            try:
                for c in stream:
                    chunk_queue.put(c)
            except BaseException as exc:  # noqa: BLE001 — surface to consumer
                chunk_queue.put(exc)
            finally:
                chunk_queue.put(_DONE)

        worker = _threading.Thread(
            target=_drain_stream,
            daemon=True,
            name=f"openai-stream-{id(stream)}",
        )

        with guard.attach(stream):
            worker.start()
            while True:
                try:
                    item = chunk_queue.get(timeout=0.1)
                except _queue.Empty:
                    # No chunk available right now — check abort and
                    # loop. The 100 ms tick bounds how long the user
                    # waits between pressing ESC and the prompt
                    # returning, regardless of how slow / blocked the
                    # underlying SDK iteration is.
                    if guard.aborted:
                        # Use ``raise_if_post_aborted`` so the abort
                        # reason from the controller is preserved
                        # (rather than hardcoding ``"user_interrupt"``,
                        # which would silently downgrade a non-default
                        # reason like a future ``"rate_limit_backoff"``).
                        guard.raise_if_post_aborted()
                    continue

                if item is _DONE:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, Exception):
                        guard.reraise_if_aborted(item)
                        raise item
                    # KeyboardInterrupt/SystemExit from the worker
                    # path — re-raise as-is so the outer signal-
                    # handling story stays intact.
                    raise item

                chunk = item
                response_model = getattr(chunk, "model", response_model)
                usage_candidate = getattr(chunk, "usage", None)
                if usage_candidate is not None:
                    usage_obj = usage_candidate

                choices = getattr(chunk, "choices", None) or []
                if choices:
                    choice = choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason

                    delta = getattr(choice, "delta", None)
                    if delta is not None:
                        content_piece = getattr(delta, "content", None)
                        if content_piece:
                            piece = str(content_piece)
                            content_parts.append(piece)
                            if on_text_chunk is not None:
                                on_text_chunk(piece)

                        reasoning_piece = getattr(delta, "reasoning_content", None)
                        if reasoning_piece:
                            reasoning_parts.append(str(reasoning_piece))

                        tool_call_deltas = getattr(delta, "tool_calls", None) or []
                        for tc in tool_call_deltas:
                            idx = getattr(tc, "index", 0)
                            entry = tool_calls_by_index.setdefault(idx, {"id": "", "name": "", "arguments": ""})

                            tc_id = getattr(tc, "id", None)
                            if tc_id:
                                entry["id"] = str(tc_id)

                            function = getattr(tc, "function", None)
                            if function is not None:
                                fn_name = getattr(function, "name", None)
                                if fn_name:
                                    entry["name"] += str(fn_name)
                                fn_args = getattr(function, "arguments", None)
                                if fn_args:
                                    entry["arguments"] += str(fn_args)

                # Check abort AFTER processing this chunk so any
                # already-delivered content is preserved (matches the
                # in-loop-check semantics from the old implementation:
                # the chunk-list test pins that the chunk we received
                # before the abort gets processed; we just don't take
                # the next one).
                if guard.aborted:
                    guard.raise_if_post_aborted()

        # Stream completed naturally OR the in-loop check broke out.
        # In the latter case the signal is already tripped; raise so
        # the caller bails at the same place every other path does.
        guard.raise_if_post_aborted()

        tool_uses: list[dict[str, Any]] = []
        for idx in sorted(tool_calls_by_index.keys()):
            item = tool_calls_by_index[idx]
            if not item["name"]:
                continue
            try:
                parsed_args = json.loads(item["arguments"]) if item["arguments"] else {}
            except Exception:
                parsed_args = {}
            tool_uses.append({
                "id": item["id"] or f"tool_call_{idx}",
                "name": item["name"],
                "input": parsed_args,
            })

        reasoning_content = "".join(reasoning_parts) if reasoning_parts else None
        return ChatResponse(
            content="".join(content_parts),
            model=response_model,
            usage=self._build_usage_dict(usage_obj),
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
            tool_uses=tool_uses or None,
        )
