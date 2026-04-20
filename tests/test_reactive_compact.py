import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.compact.reactive_compact import (
    ReactiveCompactResult,
    is_prompt_too_long_error,
    is_withheld_prompt_too_long,
    withhold_error,
    get_withheld_errors,
    clear_withheld_errors,
    _drop_oldest_messages,
    build_post_compact_messages,
    _ensure_alternating_roles,
)
from src.types.messages import UserMessage, AssistantMessage


class TestIsPromptTooLongError:
    def test_prompt_too_long(self):
        assert is_prompt_too_long_error(Exception("prompt_too_long")) is True

    def test_prompt_is_too_long(self):
        assert is_prompt_too_long_error(Exception("prompt is too long")) is True

    def test_context_length_exceeded(self):
        assert is_prompt_too_long_error(Exception("context_length_exceeded")) is True

    def test_other_error(self):
        assert is_prompt_too_long_error(Exception("network error")) is False

    def test_case_insensitive(self):
        assert is_prompt_too_long_error(Exception("PROMPT_TOO_LONG error")) is True


class TestWithheldErrors:
    def setup_method(self):
        clear_withheld_errors()

    def test_withhold_and_get(self):
        err = Exception("prompt_too_long")
        withhold_error(err)
        errors = get_withheld_errors()
        assert len(errors) == 1
        assert errors[0] is err

    def test_clear(self):
        withhold_error(Exception("test"))
        clear_withheld_errors()
        assert get_withheld_errors() == []


class TestDropOldestMessages:
    def test_drop_half(self):
        messages = [
            UserMessage(role="user", content="msg1"),
            AssistantMessage(role="assistant", content="resp1"),
            UserMessage(role="user", content="msg2"),
            AssistantMessage(role="assistant", content="resp2"),
            UserMessage(role="user", content="msg3"),
            AssistantMessage(role="assistant", content="resp3"),
        ]
        result = _drop_oldest_messages(messages, 0.5)
        assert len(result) < len(messages)

    def test_very_short(self):
        messages = [
            UserMessage(role="user", content="msg1"),
        ]
        result = _drop_oldest_messages(messages, 0.5)
        assert len(result) == 1

    def test_empty(self):
        result = _drop_oldest_messages([], 0.5)
        assert result == []


class TestBuildPostCompactMessages:
    def test_basic(self):
        messages = [
            UserMessage(role="user", content="hello"),
            AssistantMessage(role="assistant", content="hi"),
        ]
        result = build_post_compact_messages("Summary text", messages)
        assert len(result) >= 1
        assert result[0]["role"] == "user"
        assert "Summary text" in result[0]["content"]

    def test_empty_remaining(self):
        result = build_post_compact_messages("Summary", [])
        assert len(result) == 1
        assert result[0]["content"] == "Summary"


class TestEnsureAlternatingRoles:
    def test_already_alternating(self):
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        _ensure_alternating_roles(messages)
        assert len(messages) == 3

    def test_merge_consecutive_user(self):
        messages = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
        ]
        _ensure_alternating_roles(messages)
        assert len(messages) == 2
        assert "a" in messages[0]["content"]
        assert "b" in messages[0]["content"]

    def test_merge_consecutive_assistant(self):
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a1"},
            {"role": "assistant", "content": "a2"},
        ]
        _ensure_alternating_roles(messages)
        assert len(messages) == 2

    def test_single_message(self):
        messages = [{"role": "user", "content": "hello"}]
        _ensure_alternating_roles(messages)
        assert len(messages) == 1

    def test_empty(self):
        messages: list[dict] = []
        _ensure_alternating_roles(messages)
        assert messages == []


class TestReactiveCompactResult:
    def test_fields(self):
        result = ReactiveCompactResult(
            compacted=True,
            messages=[],
            tokens_before=10000,
            tokens_after=5000,
        )
        assert result.compacted is True
        assert result.tokens_before == 10000
        assert result.retried is False
