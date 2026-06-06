"""LiteLLM provider extension implementation."""

from __future__ import annotations

import importlib.util
import json
from typing import Any, Generator, Optional

from src.providers.base import (
    BaseProvider,
    ChatResponse,
    MessageInput,
    TextChunkCallback,
    ThinkingChunkCallback,
)
from src.providers.openai_compatible import (
    _convert_anthropic_messages_to_openai,
    _convert_to_openai_tool_schema,
)


def is_litellm_available() -> bool:
    """Return whether the LiteLLM package can be imported."""
    return importlib.util.find_spec("litellm") is not None


def _load_litellm() -> Any:
    if not is_litellm_available():
        raise RuntimeError("LiteLLM is not installed")

    import litellm

    return litellm


def _get_attr_or_item(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
            "total_tokens": usage.get("total_tokens", 0),
        }
    return {
        "input_tokens": getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)),
        "output_tokens": getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }


def _message_tool_calls_to_tool_uses(tool_calls: Any) -> list[dict[str, Any]] | None:
    if not tool_calls:
        return None

    tool_uses: list[dict[str, Any]] = []
    for index, tool_call in enumerate(tool_calls):
        function = _get_attr_or_item(tool_call, "function", {})
        arguments = _get_attr_or_item(function, "arguments", "")
        try:
            parsed_arguments = json.loads(arguments) if arguments else {}
        except Exception:
            parsed_arguments = {}

        name = _get_attr_or_item(function, "name", "")
        if not name:
            continue

        tool_uses.append({
            "id": _get_attr_or_item(tool_call, "id", f"tool_call_{index}"),
            "name": name,
            "input": parsed_arguments,
        })

    return tool_uses or None


class LiteLLMProvider(BaseProvider):
    """Provider extension backed by LiteLLM's completion API."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider_name: str = "openai",
    ):
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        self.provider_name = provider_name

    def _get_litellm_model(self, **kwargs) -> str:
        model = self._get_model(**kwargs)
        if not model:
            raise ValueError("A model is required for LiteLLM provider calls")
        if "/" in model:
            return model
        return f"{self.provider_name}/{model}" if self.provider_name else model

    def _prepare_messages(self, messages: list[MessageInput]) -> list[dict[str, Any]]:
        prepared = super()._prepare_messages(messages)
        return _convert_anthropic_messages_to_openai(prepared)

    def _prepare_tools(self, tools: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        converted = [_convert_to_openai_tool_schema(tool) for tool in tools]
        return [tool for tool in converted if tool is not None] or None

    def _completion_kwargs(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]],
        **kwargs,
    ) -> dict[str, Any]:
        request_kwargs = {k: v for k, v in kwargs.items() if k not in {"model", "tools", "stream"}}
        converted_tools = self._prepare_tools(tools)
        if converted_tools is not None:
            request_kwargs["tools"] = converted_tools
        if self.api_key is not None:
            request_kwargs["api_key"] = self.api_key
        if self.base_url:
            request_kwargs["base_url"] = self.base_url
        return {
            "model": self._get_litellm_model(**kwargs),
            "messages": self._prepare_messages(messages),
            **request_kwargs,
        }

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> ChatResponse:
        litellm = _load_litellm()
        response = litellm.completion(**self._completion_kwargs(messages, tools, **kwargs))
        return self._convert_response(response)

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        litellm = _load_litellm()
        stream = litellm.completion(
            **self._completion_kwargs(messages, tools, **kwargs),
            stream=True,
        )
        for chunk in stream:
            content = self._extract_delta_value(chunk, "content")
            if content:
                yield str(content)

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        on_thinking_chunk: ThinkingChunkCallback | None = None,
        abort_signal: Any = None,
        **kwargs,
    ) -> ChatResponse:
        from src.providers._stream_abort import StreamAbortGuard

        litellm = _load_litellm()
        guard = StreamAbortGuard(abort_signal)
        guard.raise_if_pre_aborted()

        stream_kwargs = self._completion_kwargs(messages, tools, **kwargs)
        existing_stream_options = stream_kwargs.pop("stream_options", None) or {}
        stream_kwargs["stream_options"] = {**existing_stream_options, "include_usage": True}
        stream = litellm.completion(**stream_kwargs, stream=True)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        response_model = stream_kwargs["model"]
        finish_reason = "stop"
        usage_obj: Any = None
        tool_calls_by_index: dict[int, dict[str, str]] = {}

        import queue as _queue
        import threading as _threading

        done = object()
        chunk_queue: _queue.Queue[Any] = _queue.Queue()

        def drain_stream() -> None:
            try:
                for streamed_chunk in stream:
                    chunk_queue.put(streamed_chunk)
            except BaseException as exc:  # noqa: BLE001 - forwarded to consumer
                chunk_queue.put(exc)
            finally:
                chunk_queue.put(done)

        worker = _threading.Thread(
            target=drain_stream,
            daemon=True,
            name=f"litellm-stream-{id(stream)}",
        )

        with guard.attach(stream):
            worker.start()
            while True:
                try:
                    item = chunk_queue.get(timeout=0.1)
                except _queue.Empty:
                    if guard.aborted:
                        guard.raise_if_post_aborted()
                    continue

                if item is done:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, Exception):
                        guard.reraise_if_aborted(item)
                        raise item
                    raise item

                chunk = item
                response_model = _get_attr_or_item(chunk, "model", response_model)
                usage_candidate = _get_attr_or_item(chunk, "usage")
                if usage_candidate is not None:
                    usage_obj = usage_candidate

                choices = _get_attr_or_item(chunk, "choices", []) or []
                if choices:
                    choice = choices[0]
                    finish_reason_candidate = _get_attr_or_item(choice, "finish_reason")
                    if finish_reason_candidate:
                        finish_reason = finish_reason_candidate

                    delta = _get_attr_or_item(choice, "delta")
                    if delta is not None:
                        content_piece = _get_attr_or_item(delta, "content")
                        if content_piece:
                            piece = str(content_piece)
                            content_parts.append(piece)
                            if on_text_chunk is not None:
                                on_text_chunk(piece)

                        reasoning_piece = _get_attr_or_item(delta, "reasoning_content")
                        if reasoning_piece:
                            piece = str(reasoning_piece)
                            reasoning_parts.append(piece)
                            if on_thinking_chunk is not None:
                                on_thinking_chunk(piece)

                        for tool_call in _get_attr_or_item(delta, "tool_calls", []) or []:
                            index = _get_attr_or_item(tool_call, "index", 0)
                            entry = tool_calls_by_index.setdefault(
                                index,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            tool_call_id = _get_attr_or_item(tool_call, "id")
                            if tool_call_id:
                                entry["id"] = str(tool_call_id)
                            function = _get_attr_or_item(tool_call, "function")
                            if function is not None:
                                function_name = _get_attr_or_item(function, "name")
                                if function_name:
                                    entry["name"] += str(function_name)
                                function_arguments = _get_attr_or_item(function, "arguments")
                                if function_arguments:
                                    entry["arguments"] += str(function_arguments)

                if guard.aborted:
                    guard.raise_if_post_aborted()

        guard.raise_if_post_aborted()

        tool_uses: list[dict[str, Any]] = []
        for index in sorted(tool_calls_by_index):
            item = tool_calls_by_index[index]
            if not item["name"]:
                continue
            try:
                parsed_arguments = json.loads(item["arguments"]) if item["arguments"] else {}
            except Exception:
                parsed_arguments = {}
            tool_uses.append({
                "id": item["id"] or f"tool_call_{index}",
                "name": item["name"],
                "input": parsed_arguments,
            })

        return ChatResponse(
            content="".join(content_parts),
            model=response_model,
            usage=_usage_to_dict(usage_obj),
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
            tool_uses=tool_uses or None,
        )

    def _convert_response(self, response: Any) -> ChatResponse:
        choices = _get_attr_or_item(response, "choices", []) or []
        choice = choices[0] if choices else {}
        message = _get_attr_or_item(choice, "message", {}) or {}

        return ChatResponse(
            content=_get_attr_or_item(message, "content", "") or "",
            model=_get_attr_or_item(response, "model", self.model or ""),
            usage=_usage_to_dict(_get_attr_or_item(response, "usage")),
            finish_reason=_get_attr_or_item(choice, "finish_reason", ""),
            reasoning_content=_get_attr_or_item(message, "reasoning_content"),
            tool_uses=_message_tool_calls_to_tool_uses(_get_attr_or_item(message, "tool_calls")),
        )

    def _extract_delta_value(self, chunk: Any, key: str) -> Any:
        choices = _get_attr_or_item(chunk, "choices", []) or []
        if not choices:
            return None
        delta = _get_attr_or_item(choices[0], "delta")
        if delta is None:
            return None
        return _get_attr_or_item(delta, key)

    def get_available_models(self) -> list[str]:
        if not is_litellm_available():
            return []
        litellm = _load_litellm()
        models = getattr(litellm, "model_list", []) or []
        return list(models)


def create_litellm_provider(
    provider_name: str,
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs: Any,
) -> LiteLLMProvider:
    return LiteLLMProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_name=provider_name,
        **kwargs,
    )


__all__ = ["LiteLLMProvider", "create_litellm_provider", "is_litellm_available"]
