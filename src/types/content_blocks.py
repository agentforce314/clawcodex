"""Typed content block models mirroring TypeScript ContentBlock/ContentBlockParam."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias


@dataclass
class TextBlock:
    text: str = ""
    type: Literal["text"] = "text"


@dataclass
class ToolUseBlock:
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str = ""
    content: str | list[Any] = ""
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"
    # In-process only. Carries the original ToolResult.output dict so the
    # REPL/TUI can render rich previews (e.g. Edit's structuredPatch) that
    # are stripped by map_result_to_api before going on the wire. Never
    # serialized via content_block_to_dict.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThinkingBlock:
    thinking: str = ""
    signature: str | None = None
    type: Literal["thinking"] = "thinking"


@dataclass
class RedactedThinkingBlock:
    data: str = ""
    type: Literal["redacted_thinking"] = "redacted_thinking"


@dataclass
class ImageBlock:
    source: dict[str, Any] = field(default_factory=dict)
    type: Literal["image"] = "image"


@dataclass
class DocumentBlock:
    source: dict[str, Any] = field(default_factory=dict)
    type: Literal["document"] = "document"


ContentBlock: TypeAlias = (
    TextBlock
    | ToolUseBlock
    | ToolResultBlock
    | ThinkingBlock
    | RedactedThinkingBlock
    | ImageBlock
    | DocumentBlock
    | dict[str, Any]
)

_BLOCK_CLASSES = (
    TextBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock,
    RedactedThinkingBlock, ImageBlock, DocumentBlock,
)


def content_block_from_dict(data: Mapping[str, Any]) -> ContentBlock:
    block_type = str(data.get("type", ""))

    if block_type == "text":
        return TextBlock(text=str(data.get("text", "")))

    if block_type == "tool_use":
        raw_input = data.get("input", {})
        input_data = dict(raw_input) if isinstance(raw_input, Mapping) else {}
        return ToolUseBlock(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            input=input_data,
        )

    if block_type == "tool_result":
        raw_content = data.get("content", "")
        if isinstance(raw_content, list):
            content: str | list[Any] = [
                content_block_to_dict(item) if _is_content_block_instance(item) else item
                for item in raw_content
            ]
        elif isinstance(raw_content, str):
            content = raw_content
        else:
            content = str(raw_content)
        return ToolResultBlock(
            tool_use_id=str(data.get("tool_use_id", "")),
            content=content,
            is_error=bool(data.get("is_error", False)),
        )

    if block_type == "thinking":
        signature_val = data.get("signature")
        signature = str(signature_val) if isinstance(signature_val, str) else None
        return ThinkingBlock(
            thinking=str(data.get("thinking", "")),
            signature=signature,
        )

    if block_type == "redacted_thinking":
        return RedactedThinkingBlock(data=str(data.get("data", "")))

    if block_type == "image":
        raw_source = data.get("source", {})
        source = dict(raw_source) if isinstance(raw_source, Mapping) else {"value": raw_source}
        return ImageBlock(source=source)

    if block_type == "document":
        raw_source = data.get("source", {})
        source = dict(raw_source) if isinstance(raw_source, Mapping) else {"value": raw_source}
        return DocumentBlock(source=source)

    return dict(data)


def normalize_content_blocks(content: list[Any]) -> list[ContentBlock]:
    normalized: list[ContentBlock] = []
    for block in content:
        if _is_content_block_instance(block):
            normalized.append(block)
            continue
        if isinstance(block, Mapping):
            normalized.append(content_block_from_dict(block))
            continue
        normalized.append(TextBlock(text=str(block)))
    return normalized


def content_block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}

    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input),
        }

    if isinstance(block, ToolResultBlock):
        result_content: str | list[Any]
        if isinstance(block.content, list):
            result_content = [
                content_block_to_dict(item) if _is_content_block_instance(item) else item
                for item in block.content
            ]
        else:
            result_content = block.content
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": result_content,
            "is_error": block.is_error,
        }

    if isinstance(block, ThinkingBlock):
        payload: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
        if block.signature is not None:
            payload["signature"] = block.signature
        return payload

    if isinstance(block, RedactedThinkingBlock):
        return {"type": "redacted_thinking", "data": block.data}

    if isinstance(block, ImageBlock):
        return {"type": "image", "source": dict(block.source)}

    if isinstance(block, DocumentBlock):
        return {"type": "document", "source": dict(block.source)}

    if isinstance(block, Mapping):
        return dict(block)

    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": str(getattr(block, "text", ""))}
    if block_type == "tool_use":
        raw_input = getattr(block, "input", {})
        return {
            "type": "tool_use",
            "id": str(getattr(block, "id", "")),
            "name": str(getattr(block, "name", "")),
            "input": dict(raw_input) if isinstance(raw_input, Mapping) else {},
        }
    if block_type == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": str(getattr(block, "tool_use_id", "")),
            "content": getattr(block, "content", ""),
            "is_error": bool(getattr(block, "is_error", False)),
        }

    return {"type": "text", "text": str(block)}


def _is_content_block_instance(value: Any) -> bool:
    return isinstance(value, _BLOCK_CLASSES)


__all__ = [
    "ContentBlock",
    "DocumentBlock",
    "ImageBlock",
    "RedactedThinkingBlock",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "content_block_from_dict",
    "content_block_to_dict",
    "normalize_content_blocks",
]
