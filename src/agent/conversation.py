"""Conversation management for Claw Codex."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..types.content_blocks import (
    ContentBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    normalize_content_blocks,
)
from ..types.messages import (
    Message,
    MessageContent,
    create_message,
    create_user_message,
    message_from_dict,
    message_to_dict,
    normalize_messages_for_api,
)


@dataclass
class Conversation:
    messages: list[Message] = field(default_factory=list)
    max_history: int = 100

    def add_message(self, role: str, content: MessageContent):
        if len(self.messages) >= self.max_history:
            self.messages.pop(0)

        normalized_content = _normalize_message_content(content)
        self.messages.append(create_message(role, normalized_content))

    def add_user_message(self, text: str):
        self.add_message("user", text)

    def add_assistant_message(self, content: MessageContent):
        self.add_message("assistant", content)

    def add_tool_result_message(self, tool_use_id: str, content: str | list[dict[str, Any]], is_error: bool = False):
        block = ToolResultBlock(
            tool_use_id=tool_use_id,
            content=content,
            is_error=is_error,
        )
        self.add_message("user", [block])

    def append_raw_message(self, message: Message) -> None:
        """Append a Message instance preserving its subclass identity.

        ``add_message`` constructs a fresh ``Message`` via
        ``create_message`` which drops subclass-specific fields
        (AttachmentMessage's ``attachments``, SystemMessage's
        ``subtype``/``preventContinuation``, AssistantMessage's
        ``model``/``usage``, etc.). Use this method when routing
        pipeline-yielded messages (sub-agent transcripts, hook
        attachments, system reminders) where those fields carry
        semantic payload.
        """
        if len(self.messages) >= self.max_history:
            self.messages.pop(0)
        self.messages.append(message)

    def get_messages(self) -> list[dict[str, Any]]:
        return normalize_messages_for_api(self.messages)

    def clear(self):
        self.messages.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [message_to_dict(message) for message in self.messages],
            "max_history": self.max_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conversation":
        conv = cls(max_history=data.get("max_history", 100))
        for msg_data in data.get("messages", []):
            if isinstance(msg_data, dict):
                conv.messages.append(message_from_dict(msg_data))
        return conv


def _normalize_message_content(content: Any) -> MessageContent:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return normalize_content_blocks(content)
    return str(content)
