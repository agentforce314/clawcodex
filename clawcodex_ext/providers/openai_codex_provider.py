"""OpenAI Codex provider backed by ChatGPT OAuth tokens."""

from __future__ import annotations

import json
import os
from typing import Any, Generator, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from src.auth.codex_oauth import CODEX_BASE_URL, resolve_codex_runtime_credentials

from src.providers.base import ChatResponse, MessageInput
from clawcodex_ext.providers.codex_models import CODEX_FALLBACK_MODELS, get_codex_model_ids
from src.providers.openai_compatible import (
    OpenAICompatibleProvider,
    _convert_anthropic_messages_to_openai,
    _convert_to_openai_tool_schema,
)

_INTERNAL_CHAT_KWARGS = {"model", "tools", "abort_signal", "stream"}


class OpenAICodexProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(api_key, base_url or CODEX_BASE_URL, model or CODEX_FALLBACK_MODELS[0])

    def _create_client(self) -> Any:
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use OpenAICodexProvider."
            )
        credentials = resolve_codex_runtime_credentials()
        self.api_key = credentials.api_key
        self.base_url = self.base_url or credentials.base_url
        kwargs: dict[str, Any] = {"api_key": self.api_key, "base_url": self.base_url}
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx

            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    @property
    def client(self) -> Any:
        credentials = resolve_codex_runtime_credentials()
        if credentials.api_key != self.api_key:
            self.api_key = credentials.api_key
            self._client = None
        if self.base_url != credentials.base_url:
            self.base_url = credentials.base_url
            self._client = None
        return super().client

    def _prepare_responses_input(self, messages: list[MessageInput]) -> tuple[str | None, list[dict[str, Any]]]:
        provider_messages = _convert_anthropic_messages_to_openai(super()._prepare_messages(messages))
        instructions: list[str] = []
        responses_input: list[dict[str, Any]] = []

        for message in provider_messages:
            role = message.get("role", "user")
            content = message.get("content", "")

            if role == "system":
                _append_system_instruction(instructions, content)
                continue
            if role == "tool":
                responses_input.append(_tool_message_to_response_input(message, content))
                continue
            if role == "assistant" and message.get("tool_calls"):
                responses_input.extend(_assistant_tool_calls_to_response_input(message, content))
                continue
            if role in {"user", "assistant"}:
                responses_input.append({"role": role, "content": _content_to_responses_parts(content, role=role)})

        return ("\n\n".join(instructions) or None), responses_input

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> ChatResponse:
        model = self._get_model(**kwargs)
        instructions, responses_input = self._prepare_responses_input(messages)
        request_kwargs: dict[str, Any] = {
            "model": model,
            "input": responses_input,
            "store": False,
            "stream": True,
        }
        if instructions:
            request_kwargs["instructions"] = instructions
        converted_tools = _responses_tools(tools)
        if converted_tools:
            request_kwargs["tools"] = converted_tools
        request_kwargs.update({k: v for k, v in kwargs.items() if k not in _INTERNAL_CHAT_KWARGS})

        stream = self.client.responses.create(**request_kwargs)
        return _collect_responses_stream(stream, model=model)

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        response = self.chat(messages, tools, **kwargs)
        if response.content:
            yield response.content

    def chat_stream_response(self, *args: Any, on_text_chunk=None, **kwargs: Any) -> ChatResponse:
        response = self.chat(*args, **kwargs)
        if on_text_chunk and response.content:
            on_text_chunk(response.content)
        return response

    def get_available_models(self) -> list[str]:
        try:
            credentials = resolve_codex_runtime_credentials(refresh_if_expiring=True)
        except Exception:
            return list(CODEX_FALLBACK_MODELS)
        return get_codex_model_ids(credentials.api_key)


def _append_system_instruction(instructions: list[str], content: Any) -> None:
    text = _content_to_text(content)
    if text:
        instructions.append(text)


def _tool_message_to_response_input(message: dict[str, Any], content: Any) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": message.get("tool_call_id", ""),
        "output": _content_to_text(content),
    }


def _assistant_tool_calls_to_response_input(message: dict[str, Any], content: Any) -> list[dict[str, Any]]:
    responses_input: list[dict[str, Any]] = []
    text = _content_to_text(content)
    if text:
        responses_input.append({"role": "assistant", "content": text})

    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function", {})
        responses_input.append({
            "type": "function_call",
            "call_id": tool_call.get("id", ""),
            "name": function.get("name", ""),
            "arguments": function.get("arguments", "{}"),
        })
    return responses_input


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts = [_content_part_to_text(item) for item in content]
    return "\n".join(part for part in parts if part)


def _content_part_to_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)

    text = item.get("text")
    if isinstance(text, str):
        return text
    if item.get("type") == "image_url":
        return "[image]"
    if item.get("type") == "file":
        return "[file]"
    return json.dumps(item)


def _content_to_responses_parts(content: Any, *, role: str) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return _content_to_text(content)

    parts: list[dict[str, Any]] = []
    text_type = "output_text" if role == "assistant" else "input_text"
    for item in content:
        if isinstance(item, str):
            parts.append({"type": text_type, "text": item})
            continue
        if not isinstance(item, dict):
            parts.append({"type": text_type, "text": str(item)})
            continue
        item_type = item.get("type")
        if item_type in {"text", "input_text", "output_text"}:
            text = item.get("text", "")
            if text:
                parts.append({"type": text_type, "text": text})
        elif item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                parts.append({"type": "input_image", "image_url": image_url["url"]})
        elif item_type == "file":
            parts.append({"type": text_type, "text": "[file]"})
        else:
            parts.append({"type": text_type, "text": json.dumps(item)})
    return parts or ""


def _responses_tools(tools: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        openai_tool = _convert_to_openai_tool_schema(tool)
        if not openai_tool:
            continue
        function = openai_tool.get("function", {})
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        converted.append({
            "type": "function",
            "name": name,
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}}),
            "strict": False,
        })
    return converted or None


def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _collect_responses_stream(stream: Any, *, model: str) -> ChatResponse:
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    response_model = model
    finish_reason = "stop"
    final_response: Any = None

    for event in stream:
        event_type = _get_attr_or_key(event, "type", "")
        if event_type in {"response.output_text.delta", "output_text.delta"}:
            delta = _get_attr_or_key(event, "delta", "")
            if delta:
                text_parts.append(delta)
        elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
            final_response = _get_attr_or_key(event, "response", None)
            if final_response is not None:
                response_model = _get_attr_or_key(final_response, "model", response_model)
                usage = _build_responses_usage_dict(_get_attr_or_key(final_response, "usage", None))
                finish_reason = _get_attr_or_key(final_response, "status", finish_reason)
                final_text, final_tool_uses = _parse_responses_output(final_response)
                if final_text:
                    text_parts = [final_text]
                if final_tool_uses:
                    tool_uses = final_tool_uses
        elif event_type in {"response.output_item.done", "output_item.done"}:
            item = _get_attr_or_key(event, "item", None)
            if item is not None and _get_attr_or_key(item, "type") == "function_call":
                tool_uses.append(_parse_function_call(item))
        elif event_type == "":
            final_response = event

    if final_response is not None and not usage:
        response_model = _get_attr_or_key(final_response, "model", response_model)
        usage = _build_responses_usage_dict(_get_attr_or_key(final_response, "usage", None))
        finish_reason = _get_attr_or_key(final_response, "status", finish_reason)
        final_text, final_tool_uses = _parse_responses_output(final_response)
        if final_text:
            text_parts = [final_text]
        if final_tool_uses:
            tool_uses = final_tool_uses

    return ChatResponse(
        content="".join(text_parts),
        model=response_model,
        usage=usage,
        finish_reason=finish_reason,
        tool_uses=tool_uses or None,
    )


def _parse_responses_output(response: Any) -> tuple[str, list[dict[str, Any]] | None]:
    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    for item in _get_attr_or_key(response, "output", []) or []:
        item_type = _get_attr_or_key(item, "type")
        if item_type == "message":
            text_parts.extend(_parse_message_text_parts(item))
        elif item_type == "function_call":
            tool_uses.append(_parse_function_call(item))
    return "\n".join(text_parts), tool_uses or None


def _parse_message_text_parts(item: Any) -> list[str]:
    text_parts: list[str] = []
    for part in _get_attr_or_key(item, "content", []) or []:
        part_type = _get_attr_or_key(part, "type")
        if part_type not in {"output_text", "text"}:
            continue
        text = _get_attr_or_key(part, "text", "")
        if text:
            text_parts.append(text)
    return text_parts


def _parse_function_call(item: Any) -> dict[str, Any]:
    return {
        "id": _get_attr_or_key(item, "call_id") or _get_attr_or_key(item, "id", ""),
        "name": _get_attr_or_key(item, "name", ""),
        "input": _parse_function_call_arguments(_get_attr_or_key(item, "arguments", "{}") or "{}"),
    }


def _parse_function_call_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            parsed_arguments = json.loads(arguments)
        except Exception:
            return {}
    else:
        parsed_arguments = arguments

    if isinstance(parsed_arguments, dict):
        return parsed_arguments
    return {"value": parsed_arguments}


def _build_responses_usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    input_tokens = _get_attr_or_key(usage, "input_tokens", _get_attr_or_key(usage, "prompt_tokens", 0))
    output_tokens = _get_attr_or_key(usage, "output_tokens", _get_attr_or_key(usage, "completion_tokens", 0))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": _get_attr_or_key(usage, "total_tokens", input_tokens + output_tokens),
    }
