"""Native Gemini provider using the ``google-genai`` SDK.

Google's Gemini API has its own request shape (``contents``/``parts`` with
explicit ``role`` of ``user`` or ``model``, separate ``system_instruction``,
function calls vs Anthropic-style tool use). This provider converts the
internal Anthropic-style message format used elsewhere in clawcodex into the
Gemini shape and back. We use the SDK directly rather than Gemini's
OpenAI-compat endpoint because the compat layer enforces strict OpenAI
content-part schemas that reject our typical message payloads.
"""

from __future__ import annotations

import json
from typing import Any, Generator, Optional

try:
    from google import genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    genai = None  # type: ignore
    genai_types = None  # type: ignore

from .base import BaseProvider, ChatResponse, MessageInput, TextChunkCallback


def _ensure_sdk() -> None:
    if genai is None:
        raise ModuleNotFoundError(
            "google-genai package is not installed. Run "
            "`pip install google-genai` to use GeminiProvider."
        )


def _text_from_anthropic_block(block: dict[str, Any]) -> str:
    """Flatten an Anthropic-style text or text-list block to a plain string."""
    if not isinstance(block, dict):
        return str(block)
    if block.get("type") == "text":
        return str(block.get("text", ""))
    return str(block)


_GEMINI_SCHEMA_DISALLOWED = frozenset(
    {
        # Polymorphic constraints — Gemini's Schema is single-type
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        # Object-shape extensions Gemini rejects
        "additionalProperties",
        "additional_properties",
        "patternProperties",
        "pattern_properties",
        "propertyNames",
        "property_names",
        "minProperties",
        "min_properties",
        "maxProperties",
        "max_properties",
        # JSON-Schema bookkeeping fields
        "$schema",
        "$ref",
        "$defs",
        "definitions",
        "$id",
        # Conditional / dependency keywords
        "if",
        "then",
        "else",
        "dependentSchemas",
        "dependentRequired",
        "dependencies",
        # Array-only extensions Gemini doesn't support
        "uniqueItems",
        "unique_items",
        "contains",
        "minContains",
        "maxContains",
        "prefixItems",
        # Documentation hints Gemini rejects
        "examples",
        "default",
        "const",
        "title",
    }
)


def _sanitize_schema_for_gemini(schema: Any) -> Any:
    """Recursively rewrite a JSON Schema to fit Gemini's strict subset.

    Gemini's ``Schema`` supports type/format/description/enum/items/properties/
    required/nullable. It rejects ``oneOf``/``anyOf``/``allOf``/``not``,
    ``additionalProperties``, ``patternProperties``, ``$ref``/``$defs``,
    ``if``/``then``/``else``, and several other JSON-Schema-isms.
    clawcodex's tool input_schemas frequently include these. We strip them and
    collapse polymorphic constraints to ``type: string`` so the tool stays
    callable; we lose constraint fidelity but gain Gemini compatibility.
    """
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in _GEMINI_SCHEMA_DISALLOWED:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _sanitize_schema_for_gemini(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _sanitize_schema_for_gemini(v)
        elif isinstance(v, dict):
            out[k] = _sanitize_schema_for_gemini(v)
        elif isinstance(v, list):
            out[k] = [
                _sanitize_schema_for_gemini(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            out[k] = v

    if any(k in schema for k in ("oneOf", "anyOf", "allOf")) and "type" not in out:
        out["type"] = "string"
        existing = out.get("description", "")
        hint = "(polymorphic — accepts any value, coerced to string)"
        out["description"] = f"{existing} {hint}".strip()

    # Gemini requires every object to declare ``properties`` (can be empty).
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}

    return out


class GeminiProvider(BaseProvider):
    """Native Gemini provider via the google-genai SDK."""

    DEFAULT_MODEL = "gemini-2.5-pro"

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(api_key, base_url, model or self.DEFAULT_MODEL)
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        _ensure_sdk()
        self._client = genai.Client(api_key=self.api_key)
        return self._client

    # ---------- message conversion ----------

    def _convert_messages(
        self, messages: list[MessageInput]
    ) -> tuple[list[Any], Optional[str]]:
        """Convert Anthropic-style messages → (gemini contents, system_instruction)."""
        _ensure_sdk()
        contents: list[Any] = []
        system_parts: list[str] = []

        for msg in messages:
            md = msg if isinstance(msg, dict) else msg.to_dict()
            role = md.get("role", "user")
            content = md.get("content", "")

            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                elif isinstance(content, list):
                    for b in content:
                        system_parts.append(_text_from_anthropic_block(b))
                continue

            gemini_role = "model" if role == "assistant" else "user"
            parts: list[Any] = []

            if isinstance(content, str):
                if content:
                    parts.append(genai_types.Part.from_text(text=content))
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        if block:
                            parts.append(genai_types.Part.from_text(text=str(block)))
                        continue
                    btype = block.get("type", "text")
                    if btype == "text":
                        text = block.get("text", "")
                        if text:
                            parts.append(genai_types.Part.from_text(text=str(text)))
                    elif btype == "tool_use":
                        # Assistant message asking to call a tool — Gemini calls
                        # this a FunctionCall part. ``input`` becomes ``args``.
                        parts.append(
                            genai_types.Part.from_function_call(
                                name=str(block.get("name", "")),
                                args=dict(block.get("input", {}) or {}),
                            )
                        )
                    elif btype == "tool_result":
                        raw = block.get("content", "")
                        if isinstance(raw, list):
                            raw_text = "\n".join(
                                _text_from_anthropic_block(b) for b in raw
                            )
                        else:
                            raw_text = str(raw)
                        # Gemini FunctionResponse needs a name; tool_use_id is
                        # an Anthropic-only correlation id. Use it as the name
                        # if no better signal is available.
                        parts.append(
                            genai_types.Part.from_function_response(
                                name=str(block.get("tool_use_id", "tool")),
                                response={"output": raw_text},
                            )
                        )

            if parts:
                contents.append(
                    genai_types.Content(role=gemini_role, parts=parts)
                )

        system_instruction = "\n".join(p for p in system_parts if p) or None
        return contents, system_instruction

    # ---------- tool conversion ----------

    def _convert_tools(
        self, tools: Optional[list[dict[str, Any]]]
    ) -> Optional[list[Any]]:
        if not tools:
            return None
        _ensure_sdk()
        declarations: list[Any] = []
        for tool in tools:
            name = tool.get("name")
            if not name:
                continue
            description = tool.get("description", "") or ""
            raw_params = (
                tool.get("input_schema")
                or tool.get("parameters")
                or {"type": "object", "properties": {}}
            )
            params = _sanitize_schema_for_gemini(raw_params)
            try:
                declarations.append(
                    genai_types.FunctionDeclaration(
                        name=str(name),
                        description=str(description),
                        parameters=params,
                    )
                )
            except Exception:
                # Individual tool schema is incompatible with Gemini even after
                # sanitization — drop it rather than failing the whole request.
                continue
        if not declarations:
            return None
        return [genai_types.Tool(function_declarations=declarations)]

    # ---------- response building ----------

    def _build_chat_response(self, response: Any, model: str) -> ChatResponse:
        content_text = ""
        tool_uses: list[dict[str, Any]] = []
        finish_reason = "stop"

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            cand = candidates[0]
            fr = getattr(cand, "finish_reason", None)
            if fr is not None:
                finish_reason = str(fr).split(".")[-1].lower()
            content_obj = getattr(cand, "content", None)
            parts = getattr(content_obj, "parts", None) or []
            for part in parts:
                text_val = getattr(part, "text", None)
                if isinstance(text_val, str) and text_val:
                    content_text += text_val
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    args = getattr(fc, "args", None)
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    elif args is None:
                        args = {}
                    else:
                        args = dict(args)
                    tool_uses.append(
                        {
                            "id": str(getattr(fc, "name", "")),
                            "name": str(getattr(fc, "name", "")),
                            "input": args,
                        }
                    )

        usage = getattr(response, "usage_metadata", None)
        usage_dict = {
            "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0)
            if usage
            else 0,
            "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0)
            if usage
            else 0,
        }
        return ChatResponse(
            content=content_text,
            model=model,
            usage=usage_dict,
            finish_reason=finish_reason,
            tool_uses=tool_uses if tool_uses else None,
        )

    # ---------- public API ----------

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> ChatResponse:
        client = self._ensure_client()
        model = self._get_model(**kwargs)
        contents, system_instruction = self._convert_messages(messages)
        gemini_tools = self._convert_tools(tools)

        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if gemini_tools:
            config_kwargs["tools"] = gemini_tools
        max_tokens = kwargs.get("max_tokens")
        if max_tokens:
            config_kwargs["max_output_tokens"] = int(max_tokens)
        if "temperature" in kwargs:
            config_kwargs["temperature"] = float(kwargs["temperature"])

        config = (
            genai_types.GenerateContentConfig(**config_kwargs)
            if config_kwargs
            else None
        )
        response = client.models.generate_content(
            model=model,
            contents=contents,
            **({"config": config} if config else {}),
        )
        return self._build_chat_response(response, model)

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        # Fall back to non-streaming for first pass — caller can iterate the
        # single concatenated chunk. The headless ``-p`` path doesn't use this.
        response = self.chat(messages, tools, **kwargs)
        if response.content:
            yield response.content

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        **kwargs,
    ) -> ChatResponse:
        response = self.chat(messages, tools, **kwargs)
        if on_text_chunk is not None and response.content:
            on_text_chunk(response.content)
        return response

    def get_available_models(self) -> list[str]:
        return [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ]
