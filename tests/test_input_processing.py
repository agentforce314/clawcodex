"""Tests for R2-WS-8: User input processing."""

from __future__ import annotations

import os

import pytest

from src.command_system.input_processing import (
    InputHistory,
    ParsedInput,
    is_multiline_complete,
    is_multiline_trigger,
    parse_user_input,
    suggest_commands,
    validate_input,
)
from src.command_system.registry import CommandRegistry
from src.command_system.types import LocalCommand, LocalCommandResult


class TestParseUserInput:
    def test_empty_input(self):
        result = parse_user_input("")
        assert result.input_type == "empty"

    def test_whitespace_only(self):
        result = parse_user_input("   \n  ")
        assert result.input_type == "empty"

    def test_command_no_args(self):
        result = parse_user_input("/help")
        assert result.input_type == "command"
        assert result.command_name == "help"
        assert result.command_args == ""

    def test_command_with_args(self):
        result = parse_user_input("/context verbose")
        assert result.input_type == "command"
        assert result.command_name == "context"
        assert result.command_args == "verbose"

    def test_command_with_multi_word_args(self):
        result = parse_user_input("/search foo bar baz")
        assert result.input_type == "command"
        assert result.command_name == "search"
        assert result.command_args == "foo bar baz"

    def test_regular_text(self):
        result = parse_user_input("Hello, please fix the bug")
        assert result.input_type == "text"
        assert result.text == "Hello, please fix the bug"

    def test_escaped_command(self):
        result = parse_user_input("\\/help")
        assert result.input_type == "text"
        assert result.text == "/help"
        assert result.is_escaped_command is True

    def test_file_mention(self):
        result = parse_user_input("Look at @./src/main.py")
        assert result.input_type == "text"
        assert len(result.file_mentions) == 1
        assert result.file_mentions[0].endswith("src/main.py")

    def test_file_mention_home(self):
        result = parse_user_input("Check @~/config.json")
        assert result.input_type == "text"
        assert len(result.file_mentions) == 1
        home = os.path.expanduser("~")
        assert result.file_mentions[0].startswith(home)

    def test_url_mention(self):
        result = parse_user_input("See https://example.com/docs")
        assert result.input_type == "text"
        assert len(result.url_mentions) == 1
        assert result.url_mentions[0] == "https://example.com/docs"

    def test_multiple_urls(self):
        result = parse_user_input("Check https://a.com and https://b.com")
        assert len(result.url_mentions) == 2

    def test_image_path(self):
        result = parse_user_input("See @./screenshot.png")
        assert len(result.image_paths) == 1

    def test_no_image_for_non_image(self):
        result = parse_user_input("See @./src/main.py")
        assert len(result.image_paths) == 0

    def test_command_with_leading_whitespace(self):
        result = parse_user_input("  /help  ")
        assert result.input_type == "command"
        assert result.command_name == "help"

    def test_command_name_with_dash(self):
        result = parse_user_input("/my-command arg1")
        assert result.input_type == "command"
        assert result.command_name == "my-command"
        assert result.command_args == "arg1"


class TestValidateInput:
    def test_empty_valid(self):
        valid, _ = validate_input("")
        assert valid is True

    def test_normal_valid(self):
        valid, _ = validate_input("hello world")
        assert valid is True

    def test_too_long(self):
        valid, msg = validate_input("x" * 2_000_000)
        assert valid is False
        assert "too long" in msg.lower()

    def test_custom_max_length(self):
        valid, _ = validate_input("hello", max_length=3)
        assert valid is False


class TestInputHistory:
    def test_add_and_entries(self):
        history = InputHistory()
        history.add("first")
        history.add("second")
        assert history.size == 2
        assert history.entries == ["first", "second"]

    def test_empty_not_added(self):
        history = InputHistory()
        history.add("")
        history.add("   ")
        assert history.size == 0

    def test_no_duplicate_last(self):
        history = InputHistory()
        history.add("same")
        history.add("same")
        assert history.size == 1

    def test_previous(self):
        history = InputHistory()
        history.add("first")
        history.add("second")
        assert history.previous() == "second"
        assert history.previous() == "first"

    def test_next(self):
        history = InputHistory()
        history.add("first")
        history.add("second")
        history.previous()  # second
        history.previous()  # first
        assert history.next() == "second"

    def test_next_past_end(self):
        history = InputHistory()
        history.add("first")
        # cursor at end
        result = history.next()
        assert result == ""  # Empty when past end

    def test_previous_empty(self):
        history = InputHistory()
        assert history.previous() is None

    def test_search(self):
        history = InputHistory()
        history.add("/help")
        history.add("hello world")
        history.add("/history clear")
        results = history.search("/h")
        assert len(results) == 2
        assert results[0] == "/history clear"  # Most recent first

    def test_max_entries(self):
        history = InputHistory(max_entries=3)
        for i in range(5):
            history.add(f"entry-{i}")
        assert history.size == 3
        assert history.entries[0] == "entry-2"

    def test_clear(self):
        history = InputHistory()
        history.add("data")
        history.clear()
        assert history.size == 0


class TestMultiline:
    def test_triple_backtick_trigger(self):
        assert is_multiline_trigger("```python") is True
        assert is_multiline_trigger("```") is True

    def test_heredoc_trigger(self):
        assert is_multiline_trigger("<<EOF") is True

    def test_backslash_trigger(self):
        assert is_multiline_trigger("hello \\") is True

    def test_no_trigger(self):
        assert is_multiline_trigger("hello world") is False
        assert is_multiline_trigger("/help") is False

    def test_triple_backtick_complete(self):
        assert is_multiline_complete("```python\nprint('hello')\n```") is True

    def test_triple_backtick_incomplete(self):
        assert is_multiline_complete("```python\nprint('hello')") is False

    def test_heredoc_complete(self):
        assert is_multiline_complete("<<EOF\nhello world\nEOF") is True

    def test_heredoc_incomplete(self):
        assert is_multiline_complete("<<EOF\nhello world") is False

    def test_backslash_complete(self):
        assert is_multiline_complete("hello \\\nworld") is True

    def test_backslash_incomplete(self):
        assert is_multiline_complete("hello \\\nworld \\") is False


class TestCommandSuggestion:
    def _make_registry(self) -> CommandRegistry:
        registry = CommandRegistry()
        for name in ("help", "history", "compact", "context", "cost", "clear", "exit"):
            cmd = LocalCommand(name=name, description=f"{name} command")
            registry.register(cmd)
        return registry

    def test_suggest_partial(self):
        registry = self._make_registry()
        suggestions = suggest_commands("/he", registry)
        assert "/help" in suggestions

    def test_suggest_all(self):
        registry = self._make_registry()
        suggestions = suggest_commands("/", registry)
        assert len(suggestions) == 7

    def test_suggest_no_match(self):
        registry = self._make_registry()
        suggestions = suggest_commands("/zzz", registry)
        assert len(suggestions) == 0

    def test_suggest_no_slash(self):
        registry = self._make_registry()
        suggestions = suggest_commands("help", registry)
        assert len(suggestions) == 0

    def test_suggest_limit(self):
        registry = self._make_registry()
        suggestions = suggest_commands("/", registry, limit=3)
        assert len(suggestions) <= 3

    def test_suggest_co_prefix(self):
        registry = self._make_registry()
        suggestions = suggest_commands("/co", registry)
        names = set(suggestions)
        assert "/compact" in names or "/context" in names or "/cost" in names
