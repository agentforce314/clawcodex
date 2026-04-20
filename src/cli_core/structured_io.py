"""Structured NDJSON stdin/stdout for headless CLI mode.

This is a focused port of ``typescript/src/cli/structuredIO.ts``. The goal is
to give SDK clients a stable, framing-safe protocol:

- ``StreamJsonReader`` parses ``--input-format stream-json`` lines from stdin
  into :class:`UserInputMessage` objects.
- ``StreamJsonWriter`` emits ``--output-format stream-json`` events, one
  NDJSON record per line, with U+2028/U+2029 escaped via
  :func:`ndjson_safe_dumps`.

The event vocabulary intentionally matches the TypeScript CLI: ``system`` for
init metadata, ``assistant`` for each completed assistant turn,
``partial_text`` for streaming token deltas, ``tool_use`` / ``tool_result`` /
``tool_error`` for tool activity, and ``result`` as the terminal event.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, IO, Iterable, Iterator

from .ndjson import ndjson_safe_dumps


# ---------------------------------------------------------------------------
# Output events


@dataclass
class HeadlessEvent:
    """Base class for headless stream-json events."""

    type: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class SystemEvent(HeadlessEvent):
    type: str = "system"
    subtype: str = "init"
    session_id: str | None = None
    model: str | None = None
    provider: str | None = None
    cwd: str | None = None
    tools: list[str] = field(default_factory=list)
    permission_mode: str | None = None


@dataclass
class PartialTextEvent(HeadlessEvent):
    type: str = "partial_text"
    text: str = ""


@dataclass
class AssistantEvent(HeadlessEvent):
    type: str = "assistant"
    text: str = ""


@dataclass
class ToolUseEvent(HeadlessEvent):
    type: str = "tool_use"
    tool_use_id: str | None = None
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent(HeadlessEvent):
    type: str = "tool_result"
    tool_use_id: str | None = None
    name: str = ""
    output: Any = None
    is_error: bool = False


@dataclass
class ResultEvent(HeadlessEvent):
    type: str = "result"
    subtype: str = "success"
    session_id: str | None = None
    num_turns: int = 0
    result: str = ""
    duration_ms: int = 0
    usage: dict[str, Any] | None = None
    is_error: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Input parsing


@dataclass
class UserInputMessage:
    """One logical user input read from stream-json stdin.

    The TypeScript SDK sends ``{"type": "user", "message": {"content": ...}}``
    where ``content`` is either a string or a list of Anthropic content blocks.
    We normalize both shapes into a plain ``text`` string for the Python agent
    loop, preserving the original ``content`` for callers that want the full
    object.
    """

    text: str
    content: Any = None
    raw: dict[str, Any] = field(default_factory=dict)


class StreamJsonReader:
    """Iterator over NDJSON lines from stdin.

    Lines that are not valid JSON or that don't carry a user message are
    yielded as ``None`` so callers can decide to skip, warn, or abort.
    """

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdin

    def __iter__(self) -> Iterator[UserInputMessage]:
        for raw_line in self._stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = self._parse(payload)
            if msg is not None:
                yield msg

    @staticmethod
    def _parse(payload: Any) -> UserInputMessage | None:
        if not isinstance(payload, dict):
            return None
        msg_type = payload.get("type")
        if msg_type and msg_type != "user":
            return None
        message = payload.get("message", payload)
        content = message.get("content") if isinstance(message, dict) else None
        text = _extract_text(content)
        if text is None and isinstance(message, dict) and isinstance(message.get("text"), str):
            text = message["text"]
        if text is None and isinstance(payload.get("prompt"), str):
            text = payload["prompt"]
        if text is None:
            return None
        return UserInputMessage(text=text, content=content, raw=payload)


def _extract_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts) if parts else None
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    return None


# ---------------------------------------------------------------------------
# Output writer


class StreamJsonWriter:
    """Emit NDJSON events to stdout (or any text stream)."""

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def write(self, event: HeadlessEvent | dict[str, Any]) -> None:
        payload = event.to_dict() if isinstance(event, HeadlessEvent) else dict(event)
        line = ndjson_safe_dumps(payload)
        self._stream.write(line + "\n")
        try:
            self._stream.flush()
        except Exception:
            pass

    def write_many(self, events: Iterable[HeadlessEvent | dict[str, Any]]) -> None:
        for event in events:
            self.write(event)
