"""Tests for token estimation."""

from __future__ import annotations

from src.token_estimation import (
    bytes_per_token_for_file_type,
    count_messages_tokens,
    count_tokens,
    rough_token_count_estimation,
    rough_token_count_estimation_for_block,
    rough_token_count_estimation_for_content,
    rough_token_count_estimation_for_file_type,
    rough_token_count_estimation_for_message,
    rough_token_count_estimation_for_messages,
)


class TestRoughTokenCountEstimation:
    def test_empty_string(self):
        assert rough_token_count_estimation("") == 0

    def test_short_string(self):
        result = rough_token_count_estimation("hello world")
        assert result > 0
        assert result == round(11 / 4)

    def test_custom_bytes_per_token(self):
        result = rough_token_count_estimation("hello world", 2)
        assert result == round(11 / 2)


class TestBytesPerTokenForFileType:
    def test_json(self):
        assert bytes_per_token_for_file_type("json") == 2

    def test_jsonl(self):
        assert bytes_per_token_for_file_type("jsonl") == 2

    def test_jsonc(self):
        assert bytes_per_token_for_file_type("jsonc") == 2

    def test_default(self):
        assert bytes_per_token_for_file_type("py") == 4
        assert bytes_per_token_for_file_type("ts") == 4
        assert bytes_per_token_for_file_type("txt") == 4


class TestRoughTokenCountForFileType:
    def test_json_file(self):
        content = '{"key": "value"}'
        assert rough_token_count_estimation_for_file_type(content, "json") == round(len(content) / 2)

    def test_python_file(self):
        content = "def foo(): pass"
        assert rough_token_count_estimation_for_file_type(content, "py") == round(len(content) / 4)


class TestRoughTokenCountForBlock:
    def test_text_block(self):
        block = {"type": "text", "text": "hello world"}
        assert rough_token_count_estimation_for_block(block) == round(11 / 4)

    def test_image_block(self):
        block = {"type": "image", "source": {"data": "base64data"}}
        assert rough_token_count_estimation_for_block(block) == 2000

    def test_document_block(self):
        block = {"type": "document", "source": {"data": "pdfdata"}}
        assert rough_token_count_estimation_for_block(block) == 2000

    def test_tool_use_block(self):
        block = {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
        result = rough_token_count_estimation_for_block(block)
        assert result > 0

    def test_tool_result_block(self):
        block = {"type": "tool_result", "content": "result text here"}
        result = rough_token_count_estimation_for_block(block)
        assert result > 0

    def test_thinking_block(self):
        block = {"type": "thinking", "thinking": "Let me think about this..."}
        result = rough_token_count_estimation_for_block(block)
        assert result > 0

    def test_redacted_thinking_block(self):
        block = {"type": "redacted_thinking", "data": "base64redacted"}
        result = rough_token_count_estimation_for_block(block)
        assert result > 0

    def test_string_block(self):
        result = rough_token_count_estimation_for_block("hello")
        assert result == round(5 / 4)


class TestRoughTokenCountForContent:
    def test_string_content(self):
        result = rough_token_count_estimation_for_content("hello world")
        assert result == round(11 / 4)

    def test_list_content(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        result = rough_token_count_estimation_for_content(content)
        assert result == round(5 / 4) + round(5 / 4)

    def test_none_content(self):
        assert rough_token_count_estimation_for_content(None) == 0


class TestRoughTokenCountForMessages:
    def test_assistant_message(self):
        messages = [{"type": "assistant", "message": {"content": "hello"}}]
        result = rough_token_count_estimation_for_messages(messages)
        assert result == round(5 / 4)

    def test_user_message(self):
        messages = [{"type": "user", "message": {"content": "hello"}}]
        result = rough_token_count_estimation_for_messages(messages)
        assert result == round(5 / 4)

    def test_empty_messages(self):
        assert rough_token_count_estimation_for_messages([]) == 0


class TestCountTokens:
    def test_empty(self):
        assert count_tokens("") == 0

    def test_basic_text(self):
        result = count_tokens("hello world")
        assert result >= 1


class TestCountMessagesTokens:
    def test_simple_message(self):
        messages = [{"role": "user", "content": "hello"}]
        result = count_messages_tokens(messages)
        assert result > 0

    def test_tool_use_message(self):
        messages = [{
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
        }]
        result = count_messages_tokens(messages)
        assert result > 0

    def test_image_message(self):
        messages = [{
            "role": "user",
            "content": [{"type": "image", "source": {}}],
        }]
        result = count_messages_tokens(messages)
        assert result >= 2000

    def test_thinking_message(self):
        messages = [{
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "reasoning here"}],
        }]
        result = count_messages_tokens(messages)
        assert result > 0
