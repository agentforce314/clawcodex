"""Tests for truncated tool-call argument recovery.

Tool-call arguments stream as string deltas, and an interrupted/late-truncated
stream can leave invalid JSON. Rather than discard it (``{}``), the
OpenAI-compatible provider best-effort closes the truncation so a
resumed/replayed turn keeps the partial arguments. Lives in the shared layer
(benefits every OpenAI-compatible provider) and only activates on
already-invalid input, so the happy path is untouched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.providers.base import ChatMessage
from src.providers.openai_compatible import (
    _close_truncated_json,
    _parse_tool_call_arguments,
)
from src.providers.openai_provider import OpenAIProvider


# --------------------------------------------------------------------------- #
# _parse_tool_call_arguments
# --------------------------------------------------------------------------- #

def test_valid_json_passes_through_unchanged():
    assert _parse_tool_call_arguments('{"file_path":"README.md"}') == {"file_path": "README.md"}


def test_empty_and_none_become_empty_object():
    assert _parse_tool_call_arguments("") == {}
    assert _parse_tool_call_arguments(None) == {}


def test_truncated_string_value_is_recovered():
    assert _parse_tool_call_arguments('{"file_path":"/foo/ba') == {"file_path": "/foo/ba"}


def test_dangling_comma_recovered():
    assert _parse_tool_call_arguments('{"a":1,') == {"a": 1}


def test_key_without_value_recovered_as_null():
    assert _parse_tool_call_arguments('{"a":') == {"a": None}


def test_nested_array_truncation_recovered():
    assert _parse_tool_call_arguments('{"items":[1,2') == {"items": [1, 2]}


def test_nested_object_truncation_recovered():
    assert _parse_tool_call_arguments('{"a":{"b":"c') == {"a": {"b": "c"}}


def test_trailing_escape_dropped():
    assert _parse_tool_call_arguments('{"a":"x\\') == {"a": "x"}


def test_unrecoverable_garbage_falls_back_to_empty():
    assert _parse_tool_call_arguments("not json at all <<<") == {}


def test_brace_inside_string_not_miscounted():
    # The ``{`` lives inside a string literal, so it must not be treated as an
    # open object that needs closing.
    assert _parse_tool_call_arguments('{"msg":"a { b') == {"msg": "a { b"}


# --------------------------------------------------------------------------- #
# _close_truncated_json (always returns valid JSON or "{}")
# --------------------------------------------------------------------------- #

def test_close_always_returns_valid_json_or_empty_object():
    for s in ['{"a":1,', '{"a":', '[1,2', '{"x":"y', "", "garbage", '{"ok":true}']:
        out = _close_truncated_json(s)
        json.loads(out)  # must not raise
    assert _close_truncated_json("garbage") == "{}"


# --------------------------------------------------------------------------- #
# End-to-end: streaming rebuild recovers truncated tool-call args
# --------------------------------------------------------------------------- #

@patch("src.providers.openai_provider.OpenAI")
def test_stream_response_recovers_truncated_tool_args(mock_openai):
    """A tool call whose streamed arguments are truncated keeps its partial
    input instead of being discarded to ``{}``."""
    mock_client = MagicMock()

    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = "call_1"
    tc_delta.function = MagicMock()
    tc_delta.function.name = "Read"
    tc_delta.function.arguments = '{"file_path":"/foo/ba'  # cut off mid-stream

    chunk = MagicMock()
    chunk.model = "deepseek-v4-pro"
    chunk.usage = None
    chunk.choices = [MagicMock()]
    chunk.choices[0].finish_reason = "tool_calls"
    chunk.choices[0].delta.content = None
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = [tc_delta]

    mock_client.chat.completions.create.return_value = iter([chunk])
    mock_client.with_options.return_value = mock_client  # see _apply_client_timeout
    mock_openai.return_value = mock_client

    provider = OpenAIProvider(api_key="test_key")
    resp = provider.chat_stream_response(
        [ChatMessage(role="user", content="Hi")],
        tools=[{"name": "Read", "description": "", "input_schema": {"type": "object"}}],
    )

    assert resp.tool_uses is not None
    assert resp.tool_uses[0]["name"] == "Read"
    # Recovered, not discarded to {}. NB: the recovery preserves a partial
    # value; the "no worse than {}" safety still rests on the downstream
    # schema gate (tool_system/registry validate_json_schema), which rejects a
    # call missing required fields exactly as it would for {}.
    assert resp.tool_uses[0]["input"] == {"file_path": "/foo/ba"}


def test_top_level_array_args_coerced_to_empty_object():
    # Tool args are JSON objects; an exotic top-level array coerces to {} so
    # downstream mapping access can't choke on a list.
    assert _parse_tool_call_arguments("[1,2,3]") == {}
    assert _parse_tool_call_arguments("[1,2") == {}
