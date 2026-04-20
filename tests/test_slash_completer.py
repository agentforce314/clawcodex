"""Unit tests for the REPL slash-only completer.

The reference Claude Code terminal only shows suggestions when the user is
typing a slash command. Typing plain text (``ex``, ``hello``, …) must not
trigger any completion popup. These tests lock that contract in so we don't
regress the fuzzy-match-everywhere behavior that shipped with the first
Phase 1 prototype.
"""

from __future__ import annotations

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.document import Document

from src.repl.core import _SlashOnlyCompleter


WORDS = ["/help", "/exit", "/clear", "/codex", "/context", "/tools"]


def _complete(text: str) -> list[str]:
    completer = _SlashOnlyCompleter(lambda: list(WORDS))
    doc = Document(text=text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, None)]


def test_plain_word_does_not_trigger_completion():
    # This is the bug the user reported: typing "ex" showed /exit /codex etc.
    assert _complete("ex") == []
    assert _complete("hello") == []
    assert _complete("write a function") == []


def test_empty_buffer_has_no_completions():
    assert _complete("") == []


def test_leading_slash_shows_prefix_matches_only():
    # Prefix match — must include /exit but NOT /context/codex (those do not
    # start with "ex").
    assert _complete("/ex") == ["/exit"]


def test_leading_slash_alone_lists_every_command():
    suggestions = _complete("/")
    # Order matches the words list (dedup preserved).
    assert set(suggestions) == set(WORDS)


def test_no_suggestions_after_slash_command_arguments():
    # Once the user starts typing arguments for a slash command, we stop
    # suggesting command names.
    assert _complete("/tools foo") == []
    assert _complete("/help ") == []


def test_midinput_slash_after_whitespace_triggers_completion():
    # Reference behavior: "echo /ex" offers /exit as completion because a
    # whitespace-prefixed slash is a mid-input slash command.
    assert _complete("echo /ex") == ["/exit"]


def test_midinput_slash_without_leading_whitespace_is_ignored():
    # Path-like text (e.g. "src/re") must not trigger command suggestions.
    assert _complete("src/re") == []
    assert _complete("/usr/bin/ex") == []


def test_completer_is_case_insensitive_for_prefix_match():
    assert _complete("/EX") == ["/exit"]
    assert _complete("/Co") == ["/codex", "/context"]


def test_completer_dynamic_word_refresh():
    words: list[str] = ["/help"]
    completer = _SlashOnlyCompleter(lambda: list(words))
    doc = Document(text="/h", cursor_position=2)
    assert [c.text for c in completer.get_completions(doc, None)] == ["/help"]

    words.append("/here")
    assert [c.text for c in completer.get_completions(doc, None)] == ["/help", "/here"]
