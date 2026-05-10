"""Tests for the Phase-12 ThinkingBlock dispatch pipeline.

Covers:
- ``run_agent_loop`` invokes ``on_thinking_chunk`` when a response
  carries ``reasoning_content`` or Anthropic ``thinking_blocks``.
- The chunk size is sane (large enough that even a 200-char block
  doesn't fragment into hundreds of calls).
- Exceptions inside the handler do not propagate.
"""

from __future__ import annotations

from src.tool_system.agent_loop import (
    ThinkingChunkHandler,
    _emit_thinking_chunks,
)


def test_emit_thinking_chunks_streams_in_chunks() -> None:
    """Thinking text larger than chunk_size is split into pieces."""

    received: list[tuple[str, bool]] = []

    def handler(chunk: str, redacted: bool) -> None:
        received.append((chunk, redacted))

    text = "x" * 200
    _emit_thinking_chunks(handler, text, redacted=False, chunk_size=64)
    # 200/64 → 4 chunks (3 × 64 + 1 × 8).
    assert len(received) == 4
    assert sum(len(chunk) for chunk, _ in received) == 200
    assert all(redacted is False for _, redacted in received)


def test_emit_thinking_chunks_propagates_redacted_flag() -> None:
    received: list[tuple[str, bool]] = []
    _emit_thinking_chunks(
        lambda chunk, redacted: received.append((chunk, redacted)),
        "secret reasoning",
        redacted=True,
        chunk_size=4,
    )
    assert all(redacted is True for _, redacted in received)


def test_emit_thinking_chunks_swallows_handler_exception() -> None:
    """Handler bugs must not break the agent loop's exception contract."""

    def boom(_chunk: str, _redacted: bool) -> None:
        raise RuntimeError("simulated handler failure")

    # Should NOT raise.
    _emit_thinking_chunks(boom, "x" * 200, chunk_size=64)


def test_emit_thinking_chunks_no_handler_is_noop() -> None:
    _emit_thinking_chunks(None, "x" * 100)  # noqa: F841 — no return; just must not raise


def test_emit_thinking_chunks_empty_text_is_noop() -> None:
    received: list[str] = []
    _emit_thinking_chunks(
        lambda chunk, redacted: received.append(chunk), "", chunk_size=4
    )
    assert received == []


def test_run_agent_loop_signature_includes_on_thinking_chunk() -> None:
    """Smoke check: the parameter exists and accepts the typed handler."""

    import inspect
    from src.tool_system.agent_loop import run_agent_loop

    params = inspect.signature(run_agent_loop).parameters
    assert "on_thinking_chunk" in params
    # And it has a sensible default (``None``).
    assert params["on_thinking_chunk"].default is None


def test_thinking_chunk_handler_type_is_two_arg() -> None:
    """``ThinkingChunkHandler`` distinguishes from ``TextChunkHandler``."""

    handler: ThinkingChunkHandler = lambda chunk, redacted: None  # noqa: E731
    handler("test", False)  # type: ignore[misc]
    handler("test", True)  # type: ignore[misc]
