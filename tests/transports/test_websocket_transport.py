"""Tests for ``src.transports.websocket_transport.WebSocketTransport``.

Strategy
--------
Runs an in-process WebSocket server via
``websockets.asyncio.server.serve``. Per-connection behavior is
controlled by a ``_ScriptedServer`` fixture: scripted close codes,
optional initial frames, captures inbound messages.

Timing-dependent tests monkeypatch ``websocket_transport._monotonic_ms``
to make the reconnect state machine deterministic (avoids real-time
sleeps in the test process).
"""

from __future__ import annotations

import asyncio
import socket

import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from src.transports import websocket_transport as wst
from src.transports.websocket_transport import (
    DEFAULT_RECONNECT_GIVE_UP_MS,
    SLEEP_DETECTION_THRESHOLD_MS,
    WebSocketTransport,
    WebSocketTransportOptions,
)


pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _ScriptedServer:
    """In-process WS server with per-connection control.

    Each new connection consumes the next item from ``close_codes``
    (None = stay open) and ``initial_frames`` (None = nothing extra).
    All inbound text frames are appended to ``received`` until close.
    """

    def __init__(
        self,
        *,
        close_codes: list[int | None] | None = None,
        initial_frames: list[list[str] | None] | None = None,
    ) -> None:
        self.close_codes: list[int | None] = list(close_codes or [])
        self.initial_frames: list[list[str] | None] = list(
            initial_frames or [],
        )
        self.connection_count = 0
        self.received: list[str] = []
        self.connections_received: list[list[str]] = []
        # Per-connection observed request headers for assertions.
        self.connection_headers: list[dict[str, str]] = []
        # Lifecycle event: connection-opened. Tests await this to know
        # the handshake completed server-side.
        self.connection_opened_count = 0

    async def handler(self, ws) -> None:
        self.connection_count += 1
        self.connection_opened_count += 1
        # Capture this connection's request headers.
        try:
            self.connection_headers.append(dict(ws.request.headers))
        except AttributeError:
            self.connection_headers.append({})
        per_conn: list[str] = []
        self.connections_received.append(per_conn)

        # Send any initial frames for this connection.
        idx = self.connection_count - 1
        if idx < len(self.initial_frames) and self.initial_frames[idx]:
            for frame in self.initial_frames[idx]:
                try:
                    await ws.send(frame)
                except (
                    websockets.exceptions.ConnectionClosed, OSError,
                ):
                    return

        # If a close code is scheduled for this connection, fire it.
        if idx < len(self.close_codes) and self.close_codes[idx] is not None:
            code = self.close_codes[idx]
            assert code is not None  # narrow for type-checker
            try:
                await ws.close(code=code, reason='scripted close')
            except (websockets.exceptions.ConnectionClosed, OSError):
                pass
            return

        # Otherwise: receive until the client closes.
        try:
            async for raw in ws:
                text = raw if isinstance(raw, str) else raw.decode('utf-8')
                self.received.append(text)
                per_conn.append(text)
        except (websockets.exceptions.ConnectionClosed, OSError):
            return


async def _start_server(
    server: _ScriptedServer, port: int,
):
    return await ws_serve(server.handler, '127.0.0.1', port)


async def _wait_for(predicate, timeout_s: float = 3.0, step_s: float = 0.02):
    """Poll a callable until it returns truthy or timeout. Returns last value."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last = None
    while asyncio.get_event_loop().time() < deadline:
        last = predicate()
        if last:
            return last
        await asyncio.sleep(step_s)
    return last


@pytest.mark.asyncio
async def test_connect_then_receive_data() -> None:
    server = _ScriptedServer(initial_frames=[['hello world']])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        received: list[str] = []
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        t.set_on_data(received.append)
        await t.connect()
        assert t.is_connected_status()
        await _wait_for(lambda: received == ['hello world'])
        assert received == ['hello world']
        assert t.get_state_label() == 'connected'
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_connect_fires_on_connect_callback() -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        fired = asyncio.Event()
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        t.set_on_connect(lambda: fired.set())
        await t.connect()
        await asyncio.wait_for(fired.wait(), timeout=2.0)
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_close_transitions_to_closed() -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        await t.connect()
        assert t.is_connected_status()
        t.close()
        # close() is synchronous and finalizes state to 'closed'.
        assert t.get_state_label() == 'closed'
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_write_sends_when_connected() -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        await t.connect()
        await t.write({'type': 'control_request', 'uuid': 'u1'})
        # The send is fire-and-forget — give the loop a tick.
        await _wait_for(lambda: bool(server.received))
        assert len(server.received) == 1
        # JSON-encoded + newline.
        assert server.received[0].endswith('\n')
        assert '"uuid": "u1"' in server.received[0]
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_write_buffers_when_disconnected_replays_on_connect() -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        # Buffer two messages while disconnected (state='idle').
        await t.write({'type': 'a', 'uuid': 'u1'})
        await t.write({'type': 'b', 'uuid': 'u2'})
        # Server has not seen anything yet.
        assert server.received == []
        await t.connect()
        # Both should now arrive on the server.
        await _wait_for(lambda: len(server.received) >= 2)
        assert len(server.received) == 2
        assert '"uuid": "u1"' in server.received[0]
        assert '"uuid": "u2"' in server.received[1]
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


def test_replay_evicts_messages_up_to_confirmed_id() -> None:
    """Direct unit test of ``_replay_buffered_messages`` eviction logic.

    The helper is exercised from ``_handle_open_event`` with
    ``None`` (replay-everything) today because Python doesn't yet
    plumb the server's ``X-Last-Request-Id`` response header through.
    But the eviction branch must work for future parity when that
    plumbing lands.
    """
    sent: list[str] = []
    t = WebSocketTransport(
        url='ws://127.0.0.1:1',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    # Patch _send_line to capture the replay frames without needing a
    # real WS attached.
    def _capture(line: str) -> bool:
        sent.append(line)
        return True
    t._send_line = _capture  # type: ignore[assignment]
    t._state = 'connected'  # type: ignore[assignment]
    t._message_buffer.append({'type': 'a', 'uuid': 'u1'})
    t._message_buffer.append({'type': 'b', 'uuid': 'u2'})
    t._message_buffer.append({'type': 'c', 'uuid': 'u3'})

    # Server reports it has received up through u2 → u1+u2 are evicted,
    # only u3 should be replayed.
    t._replay_buffered_messages('u2')

    assert len(sent) == 1
    assert '"uuid": "u3"' in sent[0]
    # Buffer was rewritten to hold only the unconfirmed tail.
    assert [m['uuid'] for m in t._message_buffer] == ['u3']


def test_replay_all_when_no_confirmed_id() -> None:
    """When ``last_id`` is None / empty, replay the entire buffer.

    Matches TS Bun behavior (``replayBufferedMessages('')`` at
    ``WebSocketTransport.ts:206``). Server-side UUID dedup is the
    safety net against re-delivering already-processed messages.
    """
    sent: list[str] = []
    t = WebSocketTransport(
        url='ws://127.0.0.1:1',
        options=WebSocketTransportOptions(auto_reconnect=False),
    )
    def _capture(line: str) -> bool:
        sent.append(line)
        return True
    t._send_line = _capture  # type: ignore[assignment]
    t._state = 'connected'  # type: ignore[assignment]
    t._message_buffer.append({'type': 'a', 'uuid': 'u1'})
    t._message_buffer.append({'type': 'b', 'uuid': 'u2'})

    t._replay_buffered_messages(None)

    assert len(sent) == 2
    assert '"uuid": "u1"' in sent[0]
    assert '"uuid": "u2"' in sent[1]


@pytest.mark.asyncio
async def test_buffered_messages_replay_on_reconnect_after_transient_close(
) -> None:
    """End-to-end: buffered writes survive a transient close + replay
    on the next successful connect.

    First server connection closes with 1011 (transient); the second
    accepts traffic. After the second open, the client should replay
    the buffered messages — server should observe them on the second
    connection (since they weren't sent on the first).
    """
    server = _ScriptedServer(close_codes=[1011, None])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(
                auto_reconnect=True,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=999.0,
            ),
        )
        # Pre-load the buffer.
        await t.write({'type': 'a', 'uuid': 'u1'})
        await t.write({'type': 'b', 'uuid': 'u2'})

        await t.connect()
        # Wait for the second connection to open + accept the replay.
        await _wait_for(
            lambda: t.is_connected_status()
            and len(server.connections_received) >= 2
            and len(server.connections_received[1]) >= 2,
            timeout_s=5.0,
        )
        # Both messages were observed on the second connection.
        assert len(server.connections_received[1]) >= 2
        joined = ''.join(server.connections_received[1])
        assert '"uuid": "u1"' in joined
        assert '"uuid": "u2"' in joined
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_permanent_close_4001_no_reconnect() -> None:
    server = _ScriptedServer(close_codes=[4001])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        on_close_codes: list[int | None] = []
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=True),
        )
        t.set_on_close(on_close_codes.append)
        await t.connect()
        await _wait_for(lambda: t.is_closed_status(), timeout_s=3.0)
        # Only one connection attempt — 4001 is permanent.
        await asyncio.sleep(0.2)  # ensure no second attempt sneaks in
        assert server.connection_count == 1
        assert on_close_codes == [4001]
        assert t.is_closed_status()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_permanent_close_4003_with_refresh_headers_retries() -> None:
    server = _ScriptedServer(close_codes=[4003, None])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        # On 4003, return a *new* Authorization → retry should fire.
        fresh_count = {'n': 0}
        def refresh() -> dict[str, str]:
            fresh_count['n'] += 1
            return {'Authorization': f'Bearer fresh-{fresh_count["n"]}'}

        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            headers={'Authorization': 'Bearer initial'},
            refresh_headers=refresh,
            options=WebSocketTransportOptions(
                auto_reconnect=True,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=999.0,
            ),
        )
        await t.connect()
        await _wait_for(
            lambda: server.connection_count >= 2, timeout_s=5.0,
        )
        # Second connection used the refreshed Authorization.
        auth2 = server.connection_headers[1].get('authorization')
        assert auth2 is not None
        assert 'fresh-' in auth2
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_permanent_close_4003_no_refresh_headers_stops() -> None:
    server = _ScriptedServer(close_codes=[4003])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        on_close_codes: list[int | None] = []
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            # No refresh_headers.
            options=WebSocketTransportOptions(auto_reconnect=True),
        )
        t.set_on_close(on_close_codes.append)
        await t.connect()
        await _wait_for(lambda: t.is_closed_status(), timeout_s=3.0)
        await asyncio.sleep(0.2)
        assert server.connection_count == 1
        assert on_close_codes == [4003]
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_auto_reconnect_false_goes_straight_to_closed() -> None:
    server = _ScriptedServer(close_codes=[1011])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        on_close_codes: list[int | None] = []
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        t.set_on_close(on_close_codes.append)
        await t.connect()
        await _wait_for(lambda: t.is_closed_status(), timeout_s=2.0)
        await asyncio.sleep(0.2)
        assert server.connection_count == 1
        assert on_close_codes == [1011]
        assert t.is_closed_status()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_transient_close_reconnects() -> None:
    # First connect: server closes immediately with 1011 (transient).
    # Second connect: server stays open.
    server = _ScriptedServer(close_codes=[1011, None])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(
                auto_reconnect=True,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=999.0,
            ),
        )
        # Use a very small backoff base for the test by patching the
        # base delay so the test finishes in <2s.
        await t.connect()
        await _wait_for(
            lambda: server.connection_count >= 2 and t.is_connected_status(),
            timeout_s=5.0,
        )
        assert server.connection_count >= 2
        assert t.is_connected_status()
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_reconnect_budget_exhaustion_fires_on_close(
    monkeypatch,
) -> None:
    """When cumulative reconnect time exceeds the give-up budget,
    transition straight to ``closed`` and fire ``on_close`` once.

    Direct state-machine test — primes the state to simulate several
    prior failed attempts under the budget, then jumps monotonic past
    the budget and fires another close. No real WS needed; tests would
    otherwise need 10+ minutes of wall-clock time."""
    now_ref = {'ms': 0}
    def fake_monotonic_ms() -> int:
        return now_ref['ms']
    monkeypatch.setattr(wst, '_monotonic_ms', fake_monotonic_ms)

    on_close_codes: list[int | None] = []
    t = WebSocketTransport(
        url='ws://127.0.0.1:1',  # never actually connects
        options=WebSocketTransportOptions(
            auto_reconnect=True,
            ping_tick_interval_s=999.0,
            keepalive_interval_s=999.0,
        ),
    )
    t.set_on_close(on_close_codes.append)

    # Prime: several attempts already accumulated under the budget,
    # state is mid-reconnect.
    t._reconnect_start_time_ms = 0
    t._last_reconnect_attempt_ms = DEFAULT_RECONNECT_GIVE_UP_MS - 1000
    t._reconnect_attempts = 5
    t._state = 'reconnecting'  # type: ignore[assignment]

    # Jump past the budget.
    now_ref['ms'] = DEFAULT_RECONNECT_GIVE_UP_MS + 1

    # Fire another close-event (transient code) — budget exhausted →
    # state='closed', on_close fires once with the code that arrived.
    t._handle_connection_error(close_code=1011)

    assert t.is_closed_status()
    assert on_close_codes == [1011]
    # No reconnect task scheduled past the budget exhaustion.
    assert t._reconnect_task is None or t._reconnect_task.done()


@pytest.mark.asyncio
async def test_sleep_detection_resets_reconnect_budget(monkeypatch) -> None:
    """A 70-second gap between attempts resets reconnect_attempts to 0."""
    now_ref = {'ms': 0}
    def fake_monotonic_ms() -> int:
        return now_ref['ms']
    monkeypatch.setattr(wst, '_monotonic_ms', fake_monotonic_ms)

    t = WebSocketTransport(
        url='ws://127.0.0.1:1',  # nothing listening; we won't connect()
        options=WebSocketTransportOptions(
            auto_reconnect=True,
            ping_tick_interval_s=999.0,
            keepalive_interval_s=999.0,
        ),
    )
    # Prime the state machine: simulate two prior reconnect attempts
    # already accumulated under the original budget window.
    t._reconnect_attempts = 2
    t._reconnect_start_time_ms = 0
    t._last_reconnect_attempt_ms = 0
    # Now jump forward past the sleep-detection threshold.
    jump_ms = SLEEP_DETECTION_THRESHOLD_MS + 10_000
    now_ref['ms'] = jump_ms

    # Stop the test before the actual reconnect coroutine tries to
    # connect to nowhere — we just want to observe the budget reset.
    # Cancel the reconnect task immediately after.
    t._handle_connection_error(close_code=None)
    if t._reconnect_task is not None:
        t._reconnect_task.cancel()

    # After the sleep-detection branch: attempts was reset to 0, then
    # the bottom of the function bumps it to 1.
    assert t._reconnect_attempts == 1
    # And reconnect_start_time_ms was reset to "now".
    assert t._reconnect_start_time_ms == jump_ms


@pytest.mark.asyncio
async def test_keepalive_frame_sent_periodically(monkeypatch) -> None:
    # Force CLAUDE_CODE_REMOTE off so keepalive runs; monkeypatch
    # restores the original value (if any) on teardown.
    monkeypatch.delenv('CLAUDE_CODE_REMOTE', raising=False)
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(
                auto_reconnect=False,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=0.1,
            ),
        )
        await t.connect()
        await _wait_for(
            lambda: any(
                '"type": "keep_alive"' in m for m in server.received
            ),
            timeout_s=2.0,
        )
        assert any(
            '"type": "keep_alive"' in m for m in server.received
        )
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_keepalive_skipped_when_claude_code_remote_truthy(
    monkeypatch,
) -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        monkeypatch.setenv('CLAUDE_CODE_REMOTE', '1')
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(
                auto_reconnect=False,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=0.1,
            ),
        )
        await t.connect()
        # Wait 0.5 s — plenty for 5 keepalive intervals; none should fire.
        await asyncio.sleep(0.5)
        assert all(
            '"type": "keep_alive"' not in m for m in server.received
        )
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_get_state_label_transitions() -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        assert t.get_state_label() == 'idle'
        await t.connect()
        assert t.get_state_label() == 'connected'
        t.close()
        # close() is sync and finalizes through 'closing' → 'closed'
        # in the same call. No 'closing' window is observable.
        assert t.get_state_label() == 'closed'
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_double_connect_is_noop_when_connected() -> None:
    server = _ScriptedServer()
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(auto_reconnect=False),
        )
        await t.connect()
        # Second connect while already connected: rejected.
        await t.connect()
        # Only one server-side connection.
        assert server.connection_count == 1
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_close_during_reconnect_cancels_pending_attempt() -> None:
    # Start with a closed-immediately server so the client schedules a
    # reconnect attempt; then close() before the timer fires.
    server = _ScriptedServer(close_codes=[1011])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(
                auto_reconnect=True,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=999.0,
            ),
        )
        await t.connect()
        # Wait for the first attempt to fail and a reconnect to be
        # scheduled (state goes to 'reconnecting').
        await _wait_for(
            lambda: t.get_state_label() == 'reconnecting', timeout_s=3.0,
        )
        # Close — must cancel the pending reconnect task.
        t.close()
        # Wait a beat to see if the reconnect fires anyway.
        await asyncio.sleep(0.3)
        # Connection count is exactly 1 — no second attempt.
        assert server.connection_count == 1
        # close() + orphan-WS finalizer should land the transport on
        # 'closed' (post-CRITIC fix — was previously stuck at 'closing').
        await _wait_for(
            lambda: t.get_state_label() == 'closed', timeout_s=1.0,
        )
        assert t.get_state_label() == 'closed'
        # Reconnect task either cancelled or completed (returned early
        # because state was 'closing').
        if t._reconnect_task is not None:
            assert t._reconnect_task.done() or t._reconnect_task.cancelled()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_send_failure_routes_to_handle_connection_error() -> None:
    """Phase-14a CRITIC: when ``ws.send`` raises mid-flight, the
    fire-and-forget task's done-callback must route to
    ``_handle_connection_error`` so the reconnect path fires.

    Without the ``add_done_callback`` wiring, send failures would
    vanish into asyncio's 'task exception was never retrieved'
    graveyard and the transport would silently report success.
    """
    class _FakeWs:
        """Stand-in for ``websockets`` client connection. ``send`` always
        raises ``ConnectionResetError`` — the canonical 'socket dead
        underneath us' failure mode."""

        async def send(self, _line: str) -> None:
            raise ConnectionResetError('socket gone')

        async def close(self) -> None:
            return

    on_close_codes: list[int | None] = []
    t = WebSocketTransport(
        url='ws://127.0.0.1:1',
        options=WebSocketTransportOptions(
            auto_reconnect=False,
            ping_tick_interval_s=999.0,
            keepalive_interval_s=999.0,
        ),
    )
    t.set_on_close(on_close_codes.append)
    # Manually wire the fake WS + flag 'connected' so _send_line proceeds.
    t._ws = _FakeWs()  # type: ignore[assignment]
    t._state = 'connected'  # type: ignore[assignment]

    # Schedule the send. _send_line returns True (the task is queued);
    # the failure surfaces via _on_send_done in the next loop tick.
    assert t._send_line('{"type":"x"}\n') is True

    # Yield to the loop so the send-task can run + the done-callback
    # can fire _handle_connection_error.
    await _wait_for(lambda: t.is_closed_status(), timeout_s=1.0)
    assert t.is_closed_status()
    # on_close fired once. ``close_code`` is ``None`` (send-failure path
    # doesn't carry a close code from the WS).
    assert on_close_codes == [None]


@pytest.mark.asyncio
async def test_reconnect_sends_x_last_request_id_header() -> None:
    """Phase-14a CRITIC: when reconnecting after a buffered write,
    the new connection's request headers must carry
    ``X-Last-Request-Id`` set to the local last-sent UUID. This is
    the wire-level signal to the server about how far we got, and is
    the bedrock for replay-eviction once the server's response
    header is plumbed through (future phase).
    """
    server = _ScriptedServer(close_codes=[1011, None])
    port = _free_port()
    ws_server = await _start_server(server, port)
    try:
        t = WebSocketTransport(
            url=f'ws://127.0.0.1:{port}',
            options=WebSocketTransportOptions(
                auto_reconnect=True,
                ping_tick_interval_s=999.0,
                keepalive_interval_s=999.0,
            ),
        )
        # Pre-buffer a write so _last_sent_id is set before connect.
        await t.write({'type': 'a', 'uuid': 'u-anchor'})

        await t.connect()
        await _wait_for(
            lambda: server.connection_count >= 2 and t.is_connected_status(),
            timeout_s=5.0,
        )
        # First connection's request also carried the header (because
        # we pre-buffered before connect).
        assert (
            server.connection_headers[0].get('x-last-request-id')
            == 'u-anchor'
        )
        # And the reconnect attempt carried it too.
        assert (
            server.connection_headers[1].get('x-last-request-id')
            == 'u-anchor'
        )
        t.close()
    finally:
        ws_server.close()
        await ws_server.wait_closed()
