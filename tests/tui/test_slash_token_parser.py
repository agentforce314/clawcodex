"""Lock in the Phase-1 slash-trigger semantics for the TUI prompt input.

The TUI has its own copy of the slash-token parser (``prompt_input._current_slash_token``)
to keep the ``tui`` package free of ``prompt_toolkit`` imports. Both parsers
must agree, or users will get inconsistent autocomplete behavior depending on
which REPL they're in.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from src.tui.widgets.prompt_input import _current_slash_token


def test_plain_text_returns_none():
    assert _current_slash_token("hello") == (None, 0)
    assert _current_slash_token("ex") == (None, 0)
    assert _current_slash_token("") == (None, 0)


def test_leading_slash_returns_token():
    assert _current_slash_token("/") == ("/", 0)
    assert _current_slash_token("/ex") == ("/ex", 0)
    assert _current_slash_token("/help") == ("/help", 0)


def test_leading_slash_after_space_is_no_longer_command_token():
    # Once the user has started arguments, we stop offering slash completions.
    assert _current_slash_token("/help ") == (None, 0)
    assert _current_slash_token("/tools foo") == (None, 0)


def test_midinput_slash_needs_whitespace_before():
    # Mid-input slash with preceding whitespace is a completion trigger.
    assert _current_slash_token("echo /ex") == ("/ex", 5)
    # Path-like text mid-line is NOT a completion trigger.
    assert _current_slash_token("src/re") == (None, 0)


def test_leading_absolute_path_is_tokenized_but_matches_nothing():
    # A leading path like ``/usr/bin/ex`` is parsed as a slash token but
    # will fail to match any real command, so the overlay stays empty.
    # This mirrors ``tests/test_slash_completer`` behavior.
    token, start = _current_slash_token("/usr/bin/ex")
    assert token == "/usr/bin/ex"
    assert start == 0
