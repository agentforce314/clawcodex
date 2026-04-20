"""Typed stream event models for provider streaming interoperability."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .content_blocks import ContentBlock, content_block_from_dict, content_block_to_dict


@dataclass
class MessageStart:
    message: dict[str, Any] = field(default_factory=dict)
    type: Literal["message_start"] = "message_start"


@dataclass
class ContentBlockStart:
    index: int = 0
    content_block: ContentBlock | dict[str, Any] = field(default_factory=dict)
    type: Literal["content_block_start"] = "content_block_start"


@dataclass
class ContentBlockDelta:
    index: int = 0
    delta: dict[str, Any] = field(default_factory=dict)
    type: Literal["content_block_delta"] = "content_block_delta"


@dataclass
class ContentBlockStop:
    index: int = 0
    type: Literal["content_block_stop"] = "content_block_stop"


@dataclass
class MessageDelta:
    delta: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None
    type: Literal["message_delta"] = "message_delta"


@dataclass
class MessageStop:
    type: Literal["message_stop"] = "message_stop"


StreamEvent: TypeAlias = (
    MessageStart
    | ContentBlockStart
    | ContentBlockDelta
    | ContentBlockStop
    | MessageDelta
    | MessageStop
)

_EVENT_MAP: dict[str, type] = {
    "message_start": MessageStart,
    "content_block_start": ContentBlockStart,
    "content_block_delta": ContentBlockDelta,
    "content_block_stop": ContentBlockStop,
    "message_delta": MessageDelta,
    "message_stop": MessageStop,
}


def stream_event_to_dict(event: StreamEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": event.type}

    if isinstance(event, MessageStart):
        payload["message"] = dict(event.message)
        return payload

    if isinstance(event, ContentBlockStart):
        payload["index"] = event.index
        payload["content_block"] = content_block_to_dict(event.content_block)
        return payload

    if isinstance(event, ContentBlockDelta):
        payload["index"] = event.index
        payload["delta"] = dict(event.delta)
        return payload

    if isinstance(event, ContentBlockStop):
        payload["index"] = event.index
        return payload

    if isinstance(event, MessageDelta):
        payload["delta"] = dict(event.delta)
        if event.usage is not None:
            payload["usage"] = dict(event.usage)
        return payload

    return payload


def stream_event_from_dict(data: dict[str, Any]) -> StreamEvent:
    event_type = str(data.get("type", ""))

    if event_type == "message_start":
        return MessageStart(message=data.get("message", {}))

    if event_type == "content_block_start":
        raw_block = data.get("content_block", {})
        block = content_block_from_dict(raw_block) if isinstance(raw_block, dict) else raw_block
        return ContentBlockStart(index=int(data.get("index", 0)), content_block=block)

    if event_type == "content_block_delta":
        return ContentBlockDelta(index=int(data.get("index", 0)), delta=data.get("delta", {}))

    if event_type == "content_block_stop":
        return ContentBlockStop(index=int(data.get("index", 0)))

    if event_type == "message_delta":
        return MessageDelta(delta=data.get("delta", {}), usage=data.get("usage"))

    return MessageStop()


__all__ = [
    "ContentBlockDelta",
    "ContentBlockStart",
    "ContentBlockStop",
    "MessageDelta",
    "MessageStart",
    "MessageStop",
    "StreamEvent",
    "stream_event_from_dict",
    "stream_event_to_dict",
]
