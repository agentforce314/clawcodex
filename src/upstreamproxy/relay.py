"""CONNECT-over-WebSocket relay for the CCR upstream proxy.

Ports ``typescript/src/upstreamproxy/relay.ts``.

Listens on a localhost TCP port, accepts HTTP CONNECT from
``curl``/``gh``/``kubectl``/etc., and tunnels bytes over a WebSocket to
the CCR upstream proxy endpoint. The CCR server-side terminates the
tunnel, MITMs TLS, injects org-configured credentials, and forwards to
the real upstream.

Why WebSocket and not raw CONNECT: CCR ingress is GKE L7 with
path-prefix routing; there's no ``connect_matcher``. The session-ingress
tunnel already uses this pattern.

Wire protocol: bytes are wrapped in ``UpstreamProxyChunk`` protobuf
messages (see ``protobuf_codec.py``) for compatibility with
``gateway.NewWebSocketStreamAdapter`` on the server side.

Implementation differences from TS:
  - **Single asyncio implementation** — no Bun/Node fork (Python only
    has one event loop family).
  - ``asyncio.start_server`` provides the TCP listen surface.
  - ``websockets.asyncio.client.connect`` provides the WS upgrade.
  - ``StreamWriter.drain()`` provides the backpressure primitive (no
    explicit per-socket queueing needed — Python streams buffer
    internally and ``await drain()`` blocks the producer when full).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.client import connect as ws_connect

from .protobuf_codec import decode_chunk, encode_chunk

logger = logging.getLogger(__name__)

#: Envoy per-request buffer cap. CONNECT payloads larger than this are
#: split into multiple chunks. Week-1 Datadog payloads won't hit this,
#: but design for it so e.g. ``git push`` doesn't need a relay rewrite.
MAX_CHUNK_BYTES = 512 * 1024

#: WebSocket application-level keepalive interval. Sidecar idle-timeout
#: is 50 s; 30 s gives a comfortable margin.
PING_INTERVAL_SECONDS = 30.0

#: HTTP CONNECT request header buffer cap. Beyond this, a malformed
#: client (or an attacker probing the relay) gets 400 Bad Request and
#: the connection closes.
MAX_CONNECT_HEADER_BYTES = 8 * 1024


@dataclass
class UpstreamProxyRelay:
    """Handle to a running relay; ``stop()`` shuts the listener down."""

    port: int
    server: asyncio.AbstractServer = field(repr=False)

    async def stop(self) -> None:
        """Close the listener and wait for inflight handlers to drain."""
        self.server.close()
        await self.server.wait_closed()


# ─── Phase 1: parse the HTTP CONNECT request ───────────────────────────────


@dataclass
class _ConnectRequest:
    """Parsed CONNECT request header."""

    raw_line: str  # full request-line, e.g. "CONNECT example.com:443 HTTP/1.1"
    target: str  # the host:port part
    trailing_bytes: bytes  # bytes that arrived AFTER the CRLFCRLF header end


async def _read_connect_request(
    reader: asyncio.StreamReader,
) -> _ConnectRequest | str:
    """Read until CRLFCRLF; return parsed request OR an error string for a 4xx.

    The string variant is the response body to send to the client before
    closing (e.g. ``'HTTP/1.1 400 Bad Request\r\n\r\n'``).

    Mirrors ``relay.ts:295-333`` ``handleData`` Phase 1.
    """
    buf = bytearray()
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            return 'HTTP/1.1 400 Bad Request\r\n\r\n'
        buf.extend(chunk)
        end = buf.find(b'\r\n\r\n')
        if end != -1:
            break
        if len(buf) > MAX_CONNECT_HEADER_BYTES:
            return 'HTTP/1.1 400 Bad Request\r\n\r\n'

    head = buf[:end].decode('utf-8', errors='replace')
    trailing = bytes(buf[end + 4 :])
    first_line = head.split('\r\n', 1)[0]
    parts = first_line.split()
    if (
        len(parts) != 3
        or parts[0].upper() != 'CONNECT'
        or not parts[2].upper().startswith('HTTP/1.')
    ):
        return 'HTTP/1.1 405 Method Not Allowed\r\n\r\n'
    target = parts[1]
    return _ConnectRequest(raw_line=first_line, target=target, trailing_bytes=trailing)


# ─── Phase 2: tunnel bytes between the client TCP socket and the WS ────────


async def _pump_client_to_ws(
    reader: asyncio.StreamReader,
    ws: websockets.asyncio.client.ClientConnection,
    initial: bytes,
) -> None:
    """Read from the local TCP client; encode each chunk; send over the WS.

    Splits chunks larger than ``MAX_CHUNK_BYTES``. Exits on EOF or any
    socket error; the caller's ``handle_connection`` task-group catches
    the resulting cancellation.
    """
    async def _send(data: bytes) -> None:
        for off in range(0, len(data), MAX_CHUNK_BYTES):
            slice_ = data[off : off + MAX_CHUNK_BYTES]
            await ws.send(encode_chunk(slice_))

    if initial:
        await _send(initial)

    while True:
        try:
            chunk = await reader.read(MAX_CHUNK_BYTES)
        except (ConnectionError, OSError):
            return
        if not chunk:
            return
        try:
            await _send(chunk)
        except (websockets.exceptions.ConnectionClosed, ConnectionError, OSError):
            return


async def _pump_ws_to_client(
    ws: websockets.asyncio.client.ClientConnection,
    writer: asyncio.StreamWriter,
    on_first_payload: Callable[[], None],
) -> None:
    """Read encoded chunks from the WS; decode; write to the TCP client.

    ``on_first_payload`` is fired exactly once when the first non-empty
    decoded chunk arrives. The relay uses this to flip the
    ``established`` flag: after the first byte the TCP stream is
    carrying TLS, and writing a plaintext 502 would corrupt it.
    """
    fired = False
    try:
        async for raw in ws:
            if isinstance(raw, str):
                # CCR upstream sends only binary frames; ignore text.
                continue
            payload = decode_chunk(raw)
            if payload is None or not payload:
                # Malformed or zero-length keepalive — drop silently.
                continue
            if not fired:
                fired = True
                on_first_payload()
            try:
                writer.write(payload)
                await writer.drain()
            except (ConnectionError, OSError):
                return
    except websockets.exceptions.ConnectionClosed:
        return


async def _keepalive(
    ws: websockets.asyncio.client.ClientConnection,
    interval: float = PING_INTERVAL_SECONDS,
) -> None:
    """Application-level keepalive — empty chunks the server can ignore.

    The ``websockets`` library has its own protocol-level ping
    (``ping_interval`` constructor kwarg) but we send the empty-chunk
    keepalive too, matching TS. Either is enough on its own; both
    together give belt-and-suspenders coverage.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(encode_chunk(b''))
        except (websockets.exceptions.ConnectionClosed, OSError):
            return


# ─── Connection handler ────────────────────────────────────────────────────


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ws_url: str,
    auth_header: str,
    ws_auth_header: str,
) -> None:
    """One accepted TCP connection: parse CONNECT, open WS, pump bytes.

    Mirrors ``relay.ts:344-428`` ``openTunnel``.
    """
    parsed = await _read_connect_request(reader)
    if isinstance(parsed, str):
        try:
            writer.write(parsed.encode('ascii'))
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        return

    # Open the WebSocket. The server upgrade requires:
    #   - Authorization: Bearer <token>            (auth on the WS itself)
    #   - Content-Type: application/proto          (so the server picks
    #     binary-proto framing, not the JSON default)
    # Bytes that arrived after the CONNECT header (TCP coalesces CONNECT
    # + ClientHello into one packet under heavy load) are flushed first.
    headers = {
        'Authorization': ws_auth_header,
        'Content-Type': 'application/proto',
    }
    try:
        ws = await ws_connect(
            ws_url,
            additional_headers=headers,
            ping_interval=None,  # we do app-level keepalive ourselves
        )
    except (websockets.exceptions.WebSocketException, ConnectionError, OSError) as exc:
        logger.warning('[upstreamproxy] ws upgrade failed: %s', exc)
        try:
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        return

    # First chunk over the tunnel is the CONNECT request line plus
    # ``Proxy-Authorization: Basic <session_id>:<token>`` so the server
    # can authenticate the tunnel target. The server responds with its
    # own ``HTTP/1.1 200 ...`` over the tunnel; we just pipe it through
    # to the local client.
    head = (
        f'{parsed.raw_line}\r\n'
        f'Proxy-Authorization: {auth_header}\r\n'
        f'\r\n'
    ).encode('ascii')

    established = False

    def _mark_established() -> None:
        nonlocal established
        established = True

    c2s_task: asyncio.Task[None] | None = None
    s2c_task: asyncio.Task[None] | None = None
    ka_task: asyncio.Task[None] | None = None
    try:
        # Send the head-chunk first, then any trailing bytes from the
        # CONNECT packet, then start pumping live data.
        await ws.send(encode_chunk(head))

        # Pumps run in parallel; whichever exits first triggers teardown
        # of the others. ``TaskGroup`` would wait for ALL tasks to
        # complete — fine for the happy-path race-free case, but s2c
        # never returns until the WS closes, and the WS closes only when
        # we close it. So we use ``asyncio.wait(FIRST_COMPLETED)`` to
        # detect the first pump's exit and cancel the rest.
        loop = asyncio.get_running_loop()
        c2s_task = loop.create_task(
            _pump_client_to_ws(reader, ws, parsed.trailing_bytes),
            name='upstreamproxy-c2s',
        )
        s2c_task = loop.create_task(
            _pump_ws_to_client(ws, writer, _mark_established),
            name='upstreamproxy-s2c',
        )
        ka_task = loop.create_task(_keepalive(ws), name='upstreamproxy-ka')

        await asyncio.wait(
            [c2s_task, s2c_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except (
        websockets.exceptions.ConnectionClosed,
        ConnectionError,
        OSError,
        asyncio.CancelledError,
    ) as exc:
        logger.debug('[upstreamproxy] connection setup exited: %s', exc)
    finally:
        # Cancel any pump that's still running so we don't leak tasks.
        for task in (c2s_task, s2c_task, ka_task):
            if task is not None and not task.done():
                task.cancel()
        # Wait for cancellations to propagate; suppress noise.
        for task in (c2s_task, s2c_task, ka_task):
            if task is None:
                continue
            try:
                await task
            except (
                websockets.exceptions.ConnectionClosed,
                ConnectionError,
                OSError,
                asyncio.CancelledError,
            ):
                pass
            except Exception as exc:  # noqa: BLE001 -- shutdown noise; never propagate
                logger.debug('[upstreamproxy] pump cleanup exception: %s', exc)
        # If we never received a server payload, the client is still
        # waiting for the CONNECT response — send 502 before closing.
        if not established:
            try:
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        try:
            await ws.close()
        except (ConnectionError, OSError):
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


# ─── Public entry point ────────────────────────────────────────────────────


async def start_upstream_proxy_relay(
    *,
    ws_url: str,
    session_id: str,
    token: str,
    host: str = '127.0.0.1',
    port: int = 0,
) -> UpstreamProxyRelay:
    """Start the CONNECT-over-WS relay listener.

    Returns an ``UpstreamProxyRelay`` whose ``port`` is the actual
    bound port (ephemeral when ``port=0``) and whose ``stop()`` shuts
    the listener down.

    The WS upgrade itself is auth-gated (the gateway wants the
    session-ingress JWT on the upgrade); the inner CONNECT carries
    ``Proxy-Authorization: Basic`` for the tunnel target. The two
    headers are different roles even though they share the same token.
    """
    auth_header = (
        'Basic '
        + base64.b64encode(f'{session_id}:{token}'.encode('utf-8')).decode('ascii')
    )
    ws_auth_header = f'Bearer {token}'

    server = await asyncio.start_server(
        lambda r, w: _handle_connection(r, w, ws_url, auth_header, ws_auth_header),
        host=host,
        port=port,
    )

    sockets = server.sockets or ()
    if not sockets:
        raise RuntimeError('upstreamproxy: server has no listening socket')
    bound_port = sockets[0].getsockname()[1]
    logger.debug(
        '[upstreamproxy] relay listening on %s:%d', host, bound_port
    )
    return UpstreamProxyRelay(port=bound_port, server=server)


__all__ = [
    'MAX_CHUNK_BYTES',
    'MAX_CONNECT_HEADER_BYTES',
    'PING_INTERVAL_SECONDS',
    'UpstreamProxyRelay',
    'start_upstream_proxy_relay',
]
