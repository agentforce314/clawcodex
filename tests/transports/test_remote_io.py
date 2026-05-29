"""Tests for ``src.transports.remote_io``.

Strategy
--------

Replace ``get_transport_for_url`` (the bound name inside
``src.transports.remote_io``) with a stub that records callback wiring
and exposes ``fire_on_data`` / ``fire_on_close`` helpers. No real
network I/O.

CCR v2 (SSE) write path is **out of scope for this PR**; a dedicated
test confirms the constructor raises ``NotImplementedError`` when the
stub transport omits ``write``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from src.transports import remote_io as remote_io_module
from src.transports.remote_io import RemoteIO


class _StubTransport:
    """Records callbacks; exposes test helpers. Has ``write`` so the
    SSE-write gate in RemoteIO.__init__ doesn't trip."""

    def __init__(self) -> None:
        self.on_data: Callable[[str], None] | None = None
        self.on_close: Callable[[int | None], None] | None = None
        self.connected = False
        self.closed = False
        self.written: list[dict[str, Any]] = []

    async def connect(self) -> None:
        self.connected = True

    async def write(self, message: dict[str, Any]) -> None:
        self.written.append(message)

    def close(self) -> None:
        self.closed = True

    def set_on_data(self, cb):
        self.on_data = cb

    def set_on_close(self, cb):
        self.on_close = cb

    # Test helpers (not part of the Transport contract)
    def fire_on_data(self, data: str) -> None:
        assert self.on_data is not None
        self.on_data(data)

    def fire_on_close(self, code: int | None = None) -> None:
        assert self.on_close is not None
        self.on_close(code)


class _SseLikeStub:
    """Stub WITHOUT a ``write`` method — simulates SSETransport."""

    def __init__(self) -> None:
        self.on_data: Callable[[str], None] | None = None
        self.on_close: Callable[[int | None], None] | None = None

    async def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_on_data(self, cb):
        self.on_data = cb

    def set_on_close(self, cb):
        self.on_close = cb


@pytest.fixture
def stub_transport(monkeypatch) -> _StubTransport:
    """Replace get_transport_for_url with a stub-returning factory.

    IMPORTANT: monkeypatch the **bound name** in remote_io.py, NOT in
    transport_utils.py — Python's `from X import Y` binds Y as a local
    reference at import time.
    """
    stub = _StubTransport()
    stub.factory_calls: list[tuple[tuple, dict]] = []  # type: ignore[attr-defined]

    def factory(*args, **kwargs):
        stub.factory_calls.append((args, kwargs))  # type: ignore[attr-defined]
        return stub

    monkeypatch.setattr(
        "src.transports.remote_io.get_transport_for_url", factory
    )
    return stub


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure tests don't accidentally pick up the user's bridge env."""
    monkeypatch.delenv("CLAUDE_CODE_ENVIRONMENT_KIND", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENVIRONMENT_RUNNER_VERSION", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("DEBUG_SDK", raising=False)


# ---------------------------------------------------------------------------
# Constructor wiring


async def test_constructor_uses_get_transport_for_url(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        # Callbacks were wired.
        assert stub_transport.on_data is not None
        assert stub_transport.on_close is not None
        # Connect was scheduled.
        assert io._connect_task is not None
        await asyncio.sleep(0)
        assert stub_transport.connected is True
        # Factory got the URL + a refresh_headers callable forwarded.
        assert len(stub_transport.factory_calls) == 1  # type: ignore[attr-defined]
        args, kwargs = stub_transport.factory_calls[0]  # type: ignore[attr-defined]
        assert args == ("ws://example.com/x",)
        assert callable(kwargs.get("refresh_headers"))
        # session_id is currently None (TODO marker in remote_io.py).
        assert kwargs.get("session_id") is None
    finally:
        io.close()


async def test_constructor_raises_for_transport_without_write(monkeypatch):
    """SSE-equivalent stub: no `write` method → NotImplementedError."""
    sse_stub = _SseLikeStub()
    monkeypatch.setattr(
        "src.transports.remote_io.get_transport_for_url",
        lambda *a, **kw: sse_stub,
    )
    with pytest.raises(NotImplementedError, match="CCR v2"):
        RemoteIO("https://example.com/x/worker/events/stream")


# ---------------------------------------------------------------------------
# Input side


async def test_on_data_pushes_to_input_queue(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        stub_transport.fire_on_data("hello\n")
        stub_transport.fire_on_data("world\n")
        # Iterate the stream and collect a couple of items.
        out: list[str] = []
        stream = io.input_stream
        # Use a single-step iterator via anext + close.
        iterator = stream.__aiter__()
        out.append(await iterator.__anext__())
        out.append(await iterator.__anext__())
        assert out == ["hello\n", "world\n"]
    finally:
        io.close()


async def test_on_close_ends_input_stream(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        stub_transport.fire_on_data("a\n")
        stub_transport.fire_on_close(code=1000)
        out: list[str] = []
        async for item in io.input_stream:
            out.append(item)
        assert out == ["a\n"]
    finally:
        io.close()


async def test_initial_prompt_chunks_landed_in_input_queue(stub_transport):
    async def chunks():
        yield "first"
        yield "second\n"

    io = RemoteIO("ws://example.com/x", initial_prompt=chunks())
    try:
        # Let the initial_prompt task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert io._initial_prompt_task is not None
        await io._initial_prompt_task
        # Sentinel will end iteration after close.
        stub_transport.fire_on_close(code=None)
        out: list[str] = []
        async for item in io.input_stream:
            out.append(item)
        assert out == ["first\n", "second\n"]
    finally:
        io.close()


async def test_initial_prompt_preserves_internal_newlines(stub_transport):
    """Verify the TS-parity `.replace(/\\n$/, '') + '\\n'` semantics:
    only the SINGLE trailing newline is stripped, so `"abc\\n\\n"`
    becomes `"abc\\n\\n"` (paragraph break preserved)."""

    async def chunks():
        yield "abc\n\n"
        yield "no-trailing"

    io = RemoteIO("ws://example.com/x", initial_prompt=chunks())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert io._initial_prompt_task is not None
        await io._initial_prompt_task
        stub_transport.fire_on_close(code=None)
        out: list[str] = []
        async for item in io.input_stream:
            out.append(item)
        # First chunk: one newline stripped, one re-added → "abc\n\n"
        # Second chunk: no newline to strip, one added → "no-trailing\n"
        assert out == ["abc\n\n", "no-trailing\n"]
    finally:
        io.close()


async def test_input_stream_is_cached(stub_transport):
    """Two property accesses return the same iterator object — TS
    exposes a singleton PassThrough; we match that semantics."""
    io = RemoteIO("ws://example.com/x")
    try:
        s1 = io.input_stream
        s2 = io.input_stream
        assert s1 is s2
    finally:
        io.close()


async def test_iter_input_rejects_non_str_non_sentinel(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        # Poison the queue directly.
        io._input_queue.put_nowait(42)  # type: ignore[arg-type]
        iterator = io.input_stream.__aiter__()
        with pytest.raises(TypeError, match="non-str non-sentinel"):
            await iterator.__anext__()
    finally:
        io.close()


# ---------------------------------------------------------------------------
# Output side


async def test_write_calls_transport_write(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        await io.write({"type": "user", "message": "hi"})
        assert stub_transport.written == [{"type": "user", "message": "hi"}]
    finally:
        io.close()


async def test_bridge_mode_echoes_control_request_to_stdout(
    stub_transport, monkeypatch, capsys
):
    monkeypatch.setenv("CLAUDE_CODE_ENVIRONMENT_KIND", "bridge")
    io = RemoteIO("ws://example.com/x")
    try:
        await io.write({"type": "control_request", "request_id": "r1"})
        captured = capsys.readouterr()
        line = captured.out.strip()
        # Parses as JSON and matches the message.
        assert json.loads(line) == {"type": "control_request", "request_id": "r1"}
    finally:
        io.close()


async def test_non_bridge_mode_does_not_echo(stub_transport, capsys):
    io = RemoteIO("ws://example.com/x")
    try:
        await io.write({"type": "control_request", "request_id": "r1"})
        captured = capsys.readouterr()
        assert captured.out == ""
    finally:
        io.close()


async def test_bridge_mode_non_control_request_not_echoed_without_debug(
    stub_transport, monkeypatch, capsys
):
    monkeypatch.setenv("CLAUDE_CODE_ENVIRONMENT_KIND", "bridge")
    io = RemoteIO("ws://example.com/x")
    try:
        await io.write({"type": "user", "message": "hi"})
        captured = capsys.readouterr()
        assert captured.out == ""
    finally:
        io.close()


async def test_bridge_mode_with_debug_echoes_any_message(
    stub_transport, monkeypatch, capsys
):
    monkeypatch.setenv("CLAUDE_CODE_ENVIRONMENT_KIND", "bridge")
    monkeypatch.setenv("DEBUG", "1")
    io = RemoteIO("ws://example.com/x")
    try:
        await io.write({"type": "user", "message": "hi"})
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {
            "type": "user",
            "message": "hi",
        }
    finally:
        io.close()


# ---------------------------------------------------------------------------
# Keep-alive


async def test_bridge_keep_alive_writes_periodically(
    stub_transport, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CODE_ENVIRONMENT_KIND", "bridge")
    # Force a very small interval so the test budget stays tight.

    class _StubConfig:
        session_keepalive_interval_v2_ms = 10

    monkeypatch.setattr(
        "src.transports.remote_io.get_poll_interval_config",
        lambda: _StubConfig(),
    )
    io = RemoteIO("ws://example.com/x")
    try:
        # Wait long enough for at least 2 ticks.
        await asyncio.sleep(0.05)
        keep_alives = [
            m for m in stub_transport.written if m.get("type") == "keep_alive"
        ]
        assert len(keep_alives) >= 2
    finally:
        io.close()


async def test_keep_alive_disabled_when_interval_zero(
    stub_transport, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CODE_ENVIRONMENT_KIND", "bridge")

    class _StubConfig:
        session_keepalive_interval_v2_ms = 0

    monkeypatch.setattr(
        "src.transports.remote_io.get_poll_interval_config",
        lambda: _StubConfig(),
    )
    io = RemoteIO("ws://example.com/x")
    try:
        assert io._keep_alive_task is None
        await asyncio.sleep(0.02)
        assert all(
            m.get("type") != "keep_alive" for m in stub_transport.written
        )
    finally:
        io.close()


async def test_keep_alive_disabled_when_not_bridge(
    stub_transport, monkeypatch
):
    # Even with a positive interval, non-bridge mode skips keep-alive.
    class _StubConfig:
        session_keepalive_interval_v2_ms = 10

    monkeypatch.setattr(
        "src.transports.remote_io.get_poll_interval_config",
        lambda: _StubConfig(),
    )
    io = RemoteIO("ws://example.com/x")
    try:
        assert io._keep_alive_task is None
    finally:
        io.close()


# ---------------------------------------------------------------------------
# Lifecycle


async def test_close_cancels_keep_alive_initial_prompt_and_connect_tasks(
    stub_transport, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CODE_ENVIRONMENT_KIND", "bridge")

    class _StubConfig:
        session_keepalive_interval_v2_ms = 1000  # long enough that close races

    monkeypatch.setattr(
        "src.transports.remote_io.get_poll_interval_config",
        lambda: _StubConfig(),
    )

    # Slow-down connect so close() catches it pre-completion.
    connect_gate = asyncio.Event()

    async def slow_connect(*args, **kwargs):
        await connect_gate.wait()
        stub_transport.connected = True

    stub_transport.connect = slow_connect  # type: ignore[method-assign]

    async def chunks():
        # Never yields — keeps the initial_prompt task alive.
        while True:
            await asyncio.sleep(10)
            yield "never"

    io = RemoteIO("ws://example.com/x", initial_prompt=chunks())
    keep_alive_task = io._keep_alive_task
    initial_prompt_task = io._initial_prompt_task
    connect_task = io._connect_task
    assert keep_alive_task is not None
    assert initial_prompt_task is not None
    assert connect_task is not None

    io.close()
    # Let cancellations propagate.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert keep_alive_task.cancelled() or keep_alive_task.done()
    assert initial_prompt_task.cancelled() or initial_prompt_task.done()
    assert connect_task.cancelled() or connect_task.done()
    assert stub_transport.closed is True
    # Sanity: io's task refs cleared.
    assert io._keep_alive_task is None
    assert io._initial_prompt_task is None
    assert io._connect_task is None


async def test_close_idempotent(stub_transport):
    io = RemoteIO("ws://example.com/x")
    io.close()
    io.close()  # second close must not raise


async def test_flush_internal_events_default_returns_none(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        result = await io.flush_internal_events()
        assert result is None
    finally:
        io.close()


async def test_internal_events_pending_default_zero(stub_transport):
    io = RemoteIO("ws://example.com/x")
    try:
        assert io.internal_events_pending == 0
    finally:
        io.close()
