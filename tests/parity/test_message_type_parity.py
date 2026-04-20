"""WS-10: Structural parity — all TS message types have Python equivalents.

Verifies:
- Every message type in ts_message_types.json has a Python class
- Content block types match
- Stream event types match
- Message constants match
- Field names on each message type match
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.types import (
    AssistantMessage,
    AttachmentMessage,
    ContentBlock,
    ContentBlockDelta,
    ContentBlockStart,
    ContentBlockStop,
    DocumentBlock,
    ImageBlock,
    Message,
    MessageDelta,
    MessageStart,
    MessageStop,
    ProgressMessage,
    RedactedThinkingBlock,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from src.types.messages import (
    CANCEL_MESSAGE,
    INTERRUPT_MESSAGE,
    INTERRUPT_MESSAGE_FOR_TOOL_USE,
    NO_CONTENT_MESSAGE,
    NO_RESPONSE_REQUESTED,
    REJECT_MESSAGE,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
)

REF_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "reference_data"


def _load_json(name: str) -> dict:
    return json.loads((REF_DIR / name).read_text())


class TestMessageTypeParity(unittest.TestCase):
    """Every TS message type has a Python equivalent."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_message_types.json")
        cls.py_classes = {
            "UserMessage": UserMessage,
            "AssistantMessage": AssistantMessage,
            "SystemMessage": SystemMessage,
            "ProgressMessage": ProgressMessage,
            "AttachmentMessage": AttachmentMessage,
        }

    def test_all_message_types_exist(self) -> None:
        for msg_type_info in self.snapshot["message_types"]:
            cls_name = msg_type_info["class"]
            self.assertIn(
                cls_name, self.py_classes,
                f"Missing Python class for TS message type '{cls_name}'",
            )

    def test_message_type_roles_match(self) -> None:
        for msg_type_info in self.snapshot["message_types"]:
            cls_name = msg_type_info["class"]
            expected_role = msg_type_info["role"]
            py_cls = self.py_classes.get(cls_name)
            if py_cls is None:
                continue
            instance = py_cls()
            self.assertEqual(
                instance.role, expected_role,
                f"{cls_name}.role = '{instance.role}', expected '{expected_role}'",
            )

    def test_all_message_types_inherit_from_message(self) -> None:
        for cls_name, py_cls in self.py_classes.items():
            self.assertTrue(
                issubclass(py_cls, Message),
                f"{cls_name} should inherit from Message",
            )

    def test_user_message_has_expected_fields(self) -> None:
        expected = self.snapshot["user_message_fields"]
        msg = UserMessage(content="test")
        for field_name in expected:
            self.assertTrue(
                hasattr(msg, field_name),
                f"UserMessage missing field '{field_name}'",
            )

    def test_assistant_message_has_expected_fields(self) -> None:
        expected = self.snapshot["assistant_message_fields"]
        msg = AssistantMessage()
        for field_name in expected:
            self.assertTrue(
                hasattr(msg, field_name),
                f"AssistantMessage missing field '{field_name}'",
            )

    def test_system_message_has_expected_fields(self) -> None:
        expected = self.snapshot["system_message_fields"]
        msg = SystemMessage(content="test")
        for field_name in expected:
            self.assertTrue(
                hasattr(msg, field_name),
                f"SystemMessage missing field '{field_name}'",
            )


class TestContentBlockParity(unittest.TestCase):
    """Every TS content block type has a Python equivalent."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_message_types.json")
        cls.py_block_classes = {
            "TextBlock": TextBlock,
            "ToolUseBlock": ToolUseBlock,
            "ToolResultBlock": ToolResultBlock,
            "ThinkingBlock": ThinkingBlock,
            "RedactedThinkingBlock": RedactedThinkingBlock,
            "ImageBlock": ImageBlock,
            "DocumentBlock": DocumentBlock,
        }

    def test_all_content_block_types_exist(self) -> None:
        for block_info in self.snapshot["content_block_types"]:
            cls_name = block_info["class"]
            self.assertIn(
                cls_name, self.py_block_classes,
                f"Missing Python class for TS content block type '{cls_name}'",
            )

    def test_content_block_type_field_matches(self) -> None:
        for block_info in self.snapshot["content_block_types"]:
            cls_name = block_info["class"]
            expected_type = block_info["type"]
            py_cls = self.py_block_classes.get(cls_name)
            if py_cls is None:
                continue
            instance = py_cls()
            self.assertEqual(
                instance.type, expected_type,
                f"{cls_name}.type = '{instance.type}', expected '{expected_type}'",
            )

    def test_content_block_has_required_fields(self) -> None:
        for block_info in self.snapshot["content_block_types"]:
            cls_name = block_info["class"]
            expected_fields = block_info["fields"]
            py_cls = self.py_block_classes.get(cls_name)
            if py_cls is None:
                continue
            instance = py_cls()
            for field_name in expected_fields:
                self.assertTrue(
                    hasattr(instance, field_name),
                    f"{cls_name} missing field '{field_name}'",
                )


class TestStreamEventParity(unittest.TestCase):
    """Every TS stream event type has a Python equivalent."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_message_types.json")
        cls.py_stream_classes = {
            "MessageStart": MessageStart,
            "ContentBlockStart": ContentBlockStart,
            "ContentBlockDelta": ContentBlockDelta,
            "ContentBlockStop": ContentBlockStop,
            "MessageDelta": MessageDelta,
            "MessageStop": MessageStop,
        }

    def test_all_stream_event_types_exist(self) -> None:
        for event_name in self.snapshot["stream_event_types"]:
            self.assertIn(
                event_name, self.py_stream_classes,
                f"Missing Python class for TS stream event type '{event_name}'",
            )

    def test_stream_events_have_type_field(self) -> None:
        for event_name, py_cls in self.py_stream_classes.items():
            instance = py_cls()
            self.assertTrue(
                hasattr(instance, "type"),
                f"{event_name} missing 'type' field",
            )


class TestMessageConstantsParity(unittest.TestCase):
    """All TS message constants have Python equivalents."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = _load_json("ts_message_types.json")

    def test_all_constants_exist(self) -> None:
        constant_map = {
            "INTERRUPT_MESSAGE": INTERRUPT_MESSAGE,
            "INTERRUPT_MESSAGE_FOR_TOOL_USE": INTERRUPT_MESSAGE_FOR_TOOL_USE,
            "CANCEL_MESSAGE": CANCEL_MESSAGE,
            "REJECT_MESSAGE": REJECT_MESSAGE,
            "NO_CONTENT_MESSAGE": NO_CONTENT_MESSAGE,
            "NO_RESPONSE_REQUESTED": NO_RESPONSE_REQUESTED,
            "SYNTHETIC_TOOL_RESULT_PLACEHOLDER": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
        }
        for const_name in self.snapshot["message_constants"]:
            self.assertIn(
                const_name, constant_map,
                f"Missing Python constant '{const_name}'",
            )
            val = constant_map[const_name]
            self.assertIsInstance(val, str)
            self.assertTrue(len(val) > 0, f"Constant '{const_name}' is empty")

    def test_interrupt_message_content(self) -> None:
        self.assertIn("interrupted", INTERRUPT_MESSAGE.lower())

    def test_reject_message_content(self) -> None:
        self.assertIn("rejected", REJECT_MESSAGE.lower())


if __name__ == "__main__":
    unittest.main()
