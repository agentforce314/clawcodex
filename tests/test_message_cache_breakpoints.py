"""Tests for ch04 round-2: message-level ``cache_control`` breakpoints.

Mirrors the load-bearing invariant of TS ``addCacheBreakpoints``
(``typescript/src/services/api/claude.ts:3107``): exactly one
``cache_control`` marker per request, attached to the last block of the
marker message; ``skip_cache_write`` shifts it to the second-to-last
message; the function never mutates its input.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.services.api import add_cache_breakpoints
from src.services.api.claude import CallModelOptions, call_model


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


def _count_cache_control(messages: list[dict[str, Any]]) -> int:
    """Count cache_control markers across every block of every message."""
    count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    count += 1
        elif isinstance(content, dict) and "cache_control" in content:
            count += 1
    return count


def test_empty_messages_returns_empty_list() -> None:
    out = add_cache_breakpoints([])
    assert out == []
    # Defensive: caller mutating the result must not pollute the input.
    out.append({"role": "user", "content": "x"})
    assert add_cache_breakpoints([]) == []


def test_disabled_returns_input_unchanged() -> None:
    messages = [{"role": "user", "content": "hi"}]
    out = add_cache_breakpoints(messages, enable_prompt_caching=False)
    assert out is messages
    assert _count_cache_control(out) == 0


def test_single_string_content_message_wrapped_and_marked() -> None:
    messages = [{"role": "user", "content": "hi"}]
    out = add_cache_breakpoints(messages)
    assert len(out) == 1
    assert out[0]["role"] == "user"
    content = out[0]["content"]
    assert isinstance(content, list)
    assert content == [
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}
    ]


def test_single_list_content_marker_on_last_block_only() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "middle"},
                {"type": "text", "text": "last"},
            ],
        }
    ]
    out = add_cache_breakpoints(messages)
    content = out[0]["content"]
    assert "cache_control" not in content[0]
    assert "cache_control" not in content[1]
    assert content[2]["cache_control"] == {"type": "ephemeral"}
    assert _count_cache_control(out) == 1


def test_multi_message_marker_on_last_only() -> None:
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
        {"role": "user", "content": "five"},
    ]
    out = add_cache_breakpoints(messages)
    # Only the last message carries the marker.
    assert _count_cache_control(out) == 1
    assert "cache_control" in out[-1]["content"][-1]
    # All other messages still in their original (unmarked) string form
    # because they were passed through by reference.
    for i in range(4):
        assert out[i] is messages[i]
        assert isinstance(out[i]["content"], str)


def test_skip_cache_write_shifts_marker_to_second_to_last() -> None:
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    out = add_cache_breakpoints(messages, skip_cache_write=True)
    assert _count_cache_control(out) == 1
    # Marker on index 1 (second-to-last), not index 2 (last).
    assert isinstance(out[1]["content"], list)
    assert out[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # Last message untouched.
    assert out[2] is messages[2]
    assert isinstance(out[2]["content"], str)


def test_skip_cache_write_single_message_is_noop() -> None:
    messages = [{"role": "user", "content": "only"}]
    out = add_cache_breakpoints(messages, skip_cache_write=True)
    # Graceful no-op: no negative indexing, no markers.
    assert _count_cache_control(out) == 0
    # Returned list is a shallow copy but messages pass through unchanged.
    assert out[0] is messages[0]


def test_skip_cache_write_empty_list_is_noop() -> None:
    assert add_cache_breakpoints([], skip_cache_write=True) == []


def test_does_not_mutate_input_list() -> None:
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
    ]
    snapshot = copy.deepcopy(messages)
    _ = add_cache_breakpoints(messages)
    # Input completely unchanged.
    assert messages == snapshot
    assert _count_cache_control(messages) == 0


def test_does_not_mutate_input_content_blocks() -> None:
    last_block = {"type": "text", "text": "tail"}
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "head"}, last_block],
        }
    ]
    out = add_cache_breakpoints(messages)
    # Returned last block is a clone (different object) and unmarked
    # original remains pristine.
    assert out[0]["content"][-1] is not last_block
    assert "cache_control" not in last_block
    assert "cache_control" in out[0]["content"][-1]
    # Earlier block reused by reference (no spurious clone cost).
    assert out[0]["content"][0] is messages[0]["content"][0]


def test_empty_block_list_gets_wrapper_marker_block() -> None:
    messages = [{"role": "user", "content": []}]
    out = add_cache_breakpoints(messages)
    # Empty list now contains a single empty text block carrying the marker.
    assert out[0]["content"] == [
        {"type": "text", "text": "", "cache_control": {"type": "ephemeral"}}
    ]


def test_non_dict_last_block_is_wrapped_into_text_block() -> None:
    # Pathological input — list whose last element isn't a block dict.
    messages = [{"role": "user", "content": ["bare-string"]}]
    out = add_cache_breakpoints(messages)
    assert out[0]["content"] == [
        {"type": "text", "text": "bare-string", "cache_control": {"type": "ephemeral"}}
    ]


def test_none_content_coerced_to_empty_text_block_with_marker() -> None:
    messages = [{"role": "user", "content": None}]
    out = add_cache_breakpoints(messages)
    assert out[0]["content"] == [
        {"type": "text", "text": "", "cache_control": {"type": "ephemeral"}}
    ]


def test_unknown_scalar_content_coerced_via_str() -> None:
    messages = [{"role": "user", "content": 42}]
    out = add_cache_breakpoints(messages)
    assert out[0]["content"] == [
        {"type": "text", "text": "42", "cache_control": {"type": "ephemeral"}}
    ]


def test_marker_message_role_preserved() -> None:
    messages = [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
    ]
    out = add_cache_breakpoints(messages)
    assert out[-1]["role"] == "assistant"
    assert "cache_control" in out[-1]["content"][-1]


# ---------------------------------------------------------------------------
# Integration: cache_control wiring through ``call_model``
# ---------------------------------------------------------------------------


class _StubStream:
    """Minimal async-iterable stub for ``messages.create(stream=True)``."""

    def __aiter__(self) -> "_StubStream":
        return self

    async def __anext__(self) -> Any:  # noqa: D401 - protocol method
        raise StopAsyncIteration


class _StubMessages:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _StubStream:
        self.last_kwargs = kwargs
        return _StubStream()


class _StubClient:
    def __init__(self) -> None:
        self.messages = _StubMessages()


@pytest.mark.asyncio
async def test_call_model_passes_marked_messages_to_client() -> None:
    client = _StubClient()
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]
    # Drain the generator so the create call runs.
    events = [ev async for ev in call_model(history, CallModelOptions(), client)]
    # Generator may emit just the trailing UsageEvent — that's fine.
    assert events  # at minimum the trailing usage event

    assert client.messages.last_kwargs is not None
    sent = client.messages.last_kwargs["messages"]
    # Exactly one cache_control marker, on the last message's last block.
    assert _count_cache_control(sent) == 1
    last = sent[-1]
    assert isinstance(last["content"], list)
    assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # Input not mutated.
    assert _count_cache_control(history) == 0
    assert isinstance(history[-1]["content"], str)


@pytest.mark.asyncio
async def test_call_model_disabled_caching_skips_marker() -> None:
    client = _StubClient()
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]
    opts = CallModelOptions(enable_prompt_caching=False)
    _ = [ev async for ev in call_model(history, opts, client)]

    assert client.messages.last_kwargs is not None
    sent = client.messages.last_kwargs["messages"]
    assert _count_cache_control(sent) == 0
    # When caching is off the function pass-throughs the original list.
    assert sent is history


@pytest.mark.asyncio
async def test_call_model_skip_cache_write_shifts_marker() -> None:
    client = _StubClient()
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]
    opts = CallModelOptions(skip_cache_write=True)
    _ = [ev async for ev in call_model(history, opts, client)]

    assert client.messages.last_kwargs is not None
    sent = client.messages.last_kwargs["messages"]
    assert _count_cache_control(sent) == 1
    # Marker on index 1, not index 2.
    assert isinstance(sent[1]["content"], list)
    assert sent[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
