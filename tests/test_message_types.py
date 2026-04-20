"""Tests for WS-1 typed message/content models."""

from __future__ import annotations

import unittest

from src.types.content_blocks import (
    DocumentBlock,
    ImageBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    content_block_from_dict,
    content_block_to_dict,
)
from src.types.messages import (
    AssistantMessage,
    AttachmentMessage,
    Message,
    ProgressMessage,
    SystemMessage,
    UserMessage,
    create_assistant_message,
    create_user_message,
    message_from_dict,
    message_to_dict,
)
from src.types.stream_events import (
    ContentBlockDelta,
    ContentBlockStart,
    ContentBlockStop,
    MessageDelta,
    MessageStart,
    MessageStop,
    stream_event_from_dict,
    stream_event_to_dict,
)


class TestContentBlockTypes(unittest.TestCase):
    def test_block_round_trip_serialization(self):
        blocks = [
            TextBlock(text="hello"),
            ToolUseBlock(id="toolu_1", name="Read", input={"file_path": "README.md"}),
            ToolResultBlock(tool_use_id="toolu_1", content="ok", is_error=False),
            ThinkingBlock(thinking="reasoning"),
            RedactedThinkingBlock(data="redacted-data"),
            ImageBlock(source={"type": "url", "url": "https://example.com/image.png"}),
            DocumentBlock(source={"type": "url", "url": "https://example.com/doc.pdf"}),
        ]

        for block in blocks:
            as_dict = content_block_to_dict(block)
            restored = content_block_from_dict(as_dict)
            self.assertEqual(content_block_to_dict(restored), as_dict)

    def test_literal_type_field(self):
        self.assertEqual(TextBlock().type, "text")
        self.assertEqual(ToolUseBlock().type, "tool_use")
        self.assertEqual(ToolResultBlock().type, "tool_result")
        self.assertEqual(ThinkingBlock().type, "thinking")
        self.assertEqual(RedactedThinkingBlock().type, "redacted_thinking")
        self.assertEqual(ImageBlock().type, "image")
        self.assertEqual(DocumentBlock().type, "document")


class TestMessageTypes(unittest.TestCase):
    def test_message_has_uuid_and_timestamp(self):
        msg = create_user_message("hello")
        self.assertTrue(len(msg.uuid) > 0)
        self.assertTrue(len(msg.timestamp) > 0)
        self.assertEqual(msg.type, "user")

    def test_message_round_trip_serialization(self):
        messages = [
            create_user_message("hello user"),
            AssistantMessage(
                content=[
                    TextBlock(text="I can help"),
                    ToolUseBlock(id="toolu_123", name="Read", input={"file_path": "README.md"}),
                    ThinkingBlock(thinking="considering options"),
                ],
                stop_reason="tool_use",
            ),
            SystemMessage(content="api retry", subtype="api_error"),
            ProgressMessage(content="Working...", progress="phase-1", toolUseID="t1", parentToolUseID="t0"),
            AttachmentMessage(
                content=[ImageBlock(source={"type": "url", "url": "https://example.com/diagram.png"})],
                attachments=[{"name": "diagram.png", "kind": "image"}],
            ),
            Message(role="custom", content="custom message"),
        ]

        restored_messages = [message_from_dict(message_to_dict(m)) for m in messages]

        self.assertIsInstance(restored_messages[0], UserMessage)
        self.assertIsInstance(restored_messages[1], AssistantMessage)
        self.assertIsInstance(restored_messages[2], SystemMessage)
        self.assertIsInstance(restored_messages[3], ProgressMessage)
        self.assertIsInstance(restored_messages[4], AttachmentMessage)
        self.assertIsInstance(restored_messages[5], Message)

        self.assertEqual(restored_messages[1].stop_reason, "tool_use")
        self.assertEqual(getattr(restored_messages[2], "subtype", None), "api_error")
        self.assertEqual(getattr(restored_messages[4], "attachments", [])[0]["name"], "diagram.png")

    def test_isMeta_field(self):
        msg = create_user_message("test", isMeta=True)
        self.assertTrue(msg.isMeta)
        restored = message_from_dict(message_to_dict(msg))
        self.assertTrue(restored.isMeta)

    def test_isVirtual_field(self):
        msg = create_user_message("test", isVirtual=True)
        self.assertTrue(msg.isVirtual)
        restored = message_from_dict(message_to_dict(msg))
        self.assertTrue(restored.isVirtual)

    def test_backward_compat_is_internal(self):
        data = {"role": "user", "content": "test", "_is_internal": True}
        restored = message_from_dict(data)
        self.assertTrue(restored.isMeta)

    def test_factory_functions(self):
        user = create_user_message("hello")
        self.assertEqual(user.role, "user")
        self.assertEqual(user.type, "user")

        assistant = create_assistant_message("response")
        self.assertEqual(assistant.role, "assistant")
        self.assertEqual(assistant.type, "assistant")
        self.assertIsInstance(assistant.content, list)


class TestStreamEventTypes(unittest.TestCase):
    def test_stream_event_serialization(self):
        events = [
            MessageStart(message={"id": "msg_1", "role": "assistant"}),
            ContentBlockStart(index=0, content_block=TextBlock(text="")),
            ContentBlockDelta(index=0, delta={"type": "text_delta", "text": "Hello"}),
            ContentBlockStop(index=0),
            MessageDelta(delta={"stop_reason": "end_turn"}, usage={"output_tokens": 10}),
            MessageStop(),
        ]

        serialized = [stream_event_to_dict(event) for event in events]

        self.assertEqual(serialized[0]["type"], "message_start")
        self.assertEqual(serialized[1]["content_block"]["type"], "text")
        self.assertEqual(serialized[2]["delta"]["type"], "text_delta")
        self.assertEqual(serialized[3]["type"], "content_block_stop")
        self.assertEqual(serialized[4]["usage"]["output_tokens"], 10)
        self.assertEqual(serialized[5]["type"], "message_stop")

    def test_stream_event_deserialization(self):
        data = {"type": "message_start", "message": {"id": "msg_1"}}
        event = stream_event_from_dict(data)
        self.assertIsInstance(event, MessageStart)
        self.assertEqual(event.message["id"], "msg_1")


if __name__ == "__main__":
    unittest.main()
