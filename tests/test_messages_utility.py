"""Tests for R2-WS-9: Message utilities."""

from __future__ import annotations

import pytest

from src.types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock, RedactedThinkingBlock
from src.types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
    create_assistant_message,
    create_system_message,
    create_user_message,
    SYNTHETIC_MODEL,
)
from src.utils.messages import (
    count_tool_calls,
    create_assistant_compact_boundary_message,
    create_system_compact_boundary_message,
    create_system_local_command_message,
    create_system_max_turns_message,
    create_user_command_input_message,
    create_user_tool_result_message,
    get_content_text,
    get_messages_after_compact_boundary,
    is_attachment,
    is_compact_boundary,
    is_synthetic,
    is_thinking_block,
    is_tool_result,
    normalize_messages_for_api_enhanced,
    preserve_thinking_blocks,
    strip_thinking_blocks,
)


class TestMessagePredicates:
    def test_is_synthetic_api_error(self):
        msg = AssistantMessage(content="error", isApiErrorMessage=True)
        assert is_synthetic(msg) is True

    def test_is_synthetic_model(self):
        msg = AssistantMessage(content="text", model=SYNTHETIC_MODEL)
        assert is_synthetic(msg) is True

    def test_is_not_synthetic(self):
        msg = create_assistant_message("hello")
        assert is_synthetic(msg) is False

    def test_is_synthetic_meta(self):
        msg = create_user_message("hello", isMeta=True)
        assert is_synthetic(msg) is True

    def test_is_attachment(self):
        msg = Message(role="user", content="", type="attachment")
        assert is_attachment(msg) is True

    def test_is_not_attachment(self):
        msg = create_user_message("hello")
        assert is_attachment(msg) is False

    def test_is_compact_boundary(self):
        msg = create_user_message("summary", isCompactSummary=True)
        assert is_compact_boundary(msg) is True

    def test_is_tool_result_true(self):
        msg = UserMessage(content=[ToolResultBlock(type="tool_result", tool_use_id="id1", content="ok")])
        assert is_tool_result(msg) is True

    def test_is_tool_result_false(self):
        msg = create_user_message("text")
        assert is_tool_result(msg) is False

    def test_is_thinking_block_true(self):
        assert is_thinking_block(ThinkingBlock(type="thinking", thinking="...", signature="sig")) is True
        assert is_thinking_block(RedactedThinkingBlock(type="redacted_thinking", data="...")) is True

    def test_is_thinking_block_false(self):
        assert is_thinking_block(TextBlock(text="hello")) is False


class TestContentHelpers:
    def test_get_content_text_string(self):
        msg = create_user_message("hello world")
        assert get_content_text(msg) == "hello world"

    def test_get_content_text_blocks(self):
        msg = AssistantMessage(content=[TextBlock(text="line1"), TextBlock(text="line2")])
        assert "line1" in get_content_text(msg)
        assert "line2" in get_content_text(msg)

    def test_count_tool_calls_zero(self):
        msg = create_user_message("hello")
        assert count_tool_calls(msg) == 0

    def test_count_tool_calls_multiple(self):
        msg = AssistantMessage(content=[
            TextBlock(text="thinking"),
            ToolUseBlock(type="tool_use", id="1", name="Read", input={}),
            ToolUseBlock(type="tool_use", id="2", name="Write", input={}),
        ])
        assert count_tool_calls(msg) == 2

    def test_get_messages_after_compact_boundary(self):
        msgs = [
            create_user_message("old"),
            create_user_message("boundary", isCompactSummary=True),
            create_user_message("new1"),
            create_user_message("new2"),
        ]
        after = get_messages_after_compact_boundary(msgs)
        assert len(after) == 2
        assert get_content_text(after[0]) == "new1"

    def test_get_messages_after_compact_boundary_none(self):
        msgs = [create_user_message("a"), create_user_message("b")]
        after = get_messages_after_compact_boundary(msgs)
        assert len(after) == 2


class TestThinkingBlockHandling:
    def test_strip_thinking_blocks(self):
        blocks = [
            TextBlock(text="hello"),
            ThinkingBlock(type="thinking", thinking="...", signature="sig"),
            TextBlock(text="world"),
        ]
        result = strip_thinking_blocks(blocks)
        assert len(result) == 2
        assert all(isinstance(b, TextBlock) for b in result)

    def test_preserve_thinking_blocks(self):
        blocks = [
            TextBlock(text="hello"),
            ThinkingBlock(type="thinking", thinking="deep thought", signature="sig"),
        ]
        result = preserve_thinking_blocks(blocks)
        assert len(result) == 1
        assert isinstance(result[0], ThinkingBlock)


class TestNormalizeEnhanced:
    def test_basic_normalization(self):
        msgs = [
            create_user_message("hello"),
            create_assistant_message("world"),
        ]
        result = normalize_messages_for_api_enhanced(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_strips_progress_messages(self):
        from src.types.messages import ProgressMessage
        msgs = [
            create_user_message("hello"),
            ProgressMessage(toolUseID="t1", parentToolUseID="p1", data=None),
            create_assistant_message("world"),
        ]
        result = normalize_messages_for_api_enhanced(msgs)
        assert len(result) == 2

    def test_strips_virtual_messages(self):
        msgs = [
            create_user_message("visible"),
            create_user_message("hidden", isVirtual=True),
            create_assistant_message("reply"),
        ]
        result = normalize_messages_for_api_enhanced(msgs)
        assert len(result) == 2

    def test_strips_thinking_blocks(self):
        msgs = [AssistantMessage(content=[
            ThinkingBlock(type="thinking", thinking="thought", signature="sig"),
            TextBlock(text="visible"),
        ])]
        result = normalize_messages_for_api_enhanced(
            [create_user_message("q")] + msgs,
            strip_thinking=True,
        )
        assistant_content = result[1]["content"]
        assert len(assistant_content) == 1
        assert assistant_content[0]["type"] == "text"

    def test_preserves_thinking_blocks_when_disabled(self):
        msgs = [
            create_user_message("q"),
            AssistantMessage(content=[
                ThinkingBlock(type="thinking", thinking="thought", signature="sig"),
                TextBlock(text="visible"),
            ]),
        ]
        result = normalize_messages_for_api_enhanced(msgs, strip_thinking=False)
        assistant_content = result[1]["content"]
        assert len(assistant_content) == 2

    def test_merges_consecutive_user_messages(self):
        msgs = [
            create_user_message("first"),
            create_user_message("second"),
            create_assistant_message("reply"),
        ]
        result = normalize_messages_for_api_enhanced(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "user"


class TestCreateVariants:
    def test_create_user_tool_result_message(self):
        msg = create_user_tool_result_message("tool-1", "result text")
        assert msg.role == "user"
        assert isinstance(msg.content, list)
        assert len(msg.content) == 1
        block = msg.content[0]
        assert isinstance(block, ToolResultBlock)
        assert block.tool_use_id == "tool-1"

    def test_create_user_command_input_message(self):
        msg = create_user_command_input_message("/help")
        assert msg.isMeta is True
        assert msg.content == "/help"

    def test_create_system_local_command_message(self):
        msg = create_system_local_command_message("output text")
        assert isinstance(msg, SystemMessage)
        assert msg.subtype == "local_command"

    def test_create_system_max_turns_message(self):
        msg = create_system_max_turns_message(10)
        assert isinstance(msg, SystemMessage)
        assert "10" in msg.content
        assert msg.preventContinuation is True

    def test_create_system_compact_boundary(self):
        msg = create_system_compact_boundary_message("summary")
        assert msg.subtype == "compact_boundary"

    def test_create_assistant_compact_boundary(self):
        msg = create_assistant_compact_boundary_message("summary text")
        assert isinstance(msg, AssistantMessage)
