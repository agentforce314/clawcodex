"""End-to-end test for the CONNECT-over-WS relay.

Runs an in-process echo WS server, starts the relay pointing at it,
opens a TCP CONNECT to the relay, sends bytes, asserts they round-trip
back through both legs (encoded → WS → echoed → decoded → TCP client).

Marked ``@pytest.mark.integration`` because it spins up real TCP +
real WS listeners on ephemeral ports.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
import websockets
from websockets.asyncio.server import serve as ws_serve

from src.upstreamproxy.protobuf_codec import decode_chunk, encode_chunk
from src.upstreamproxy.relay import start_upstream_proxy_relay


pytestmark = pytest.mark.integration


async def _echo_handler(ws: websockets.asyncio.server.ServerConnection) -> None:
    """Echo handler: every chunk in → same chunk out.

    Plus we synthesize the ``HTTP/1.1 200 Connection Established`` reply
    that the real CCR server sends after parsing the first inner chunk
    (the CONNECT request line + Proxy-Authorization). This satisfies the
    relay's ``established`` gate so subsequent traffic flows through.
    """
    saw_connect = False
    try:
        async for raw in ws:
            assert isinstance(raw, (bytes, bytearray))
            payload = decode_chunk(bytes(raw))
            if payload is None:
                continue
            if not saw_connect:
                # First inner chunk is the CONNECT line + Proxy-Auth.
                assert payload.startswith(b'CONNECT '), payload[:32]
                saw_connect = True
                # Send the synthesized 200 response back over the tunnel.
                await ws.send(encode_chunk(b'HTTP/1.1 200 Connection Established\r\n\r\n'))
                continue
            # Plain echo for everything else.
            await ws.send(encode_chunk(payload))
    except websockets.exceptions.ConnectionClosed:
        return


def _free_port() -> int:
    """Bind ephemeral port, immediately release for the WS server to take."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_relay_round_trip_echo() -> None:
    """CONNECT to the relay, exchange bytes, verify echo through both legs."""
    ws_port = _free_port()

    # Start the in-process echo WS server.
    ws_server = await ws_serve(_echo_handler, '127.0.0.1', ws_port)
    try:
        ws_url = f'ws://127.0.0.1:{ws_port}/v1/code/upstreamproxy/ws'
        relay = await start_upstream_proxy_relay(
            ws_url=ws_url, session_id='cse_test', token='secret-token'
        )
        try:
            # Open a TCP connection to the relay and send a CONNECT.
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.port)
            try:
                writer.write(b'CONNECT example.com:443 HTTP/1.1\r\n\r\n')
                await writer.drain()

                # Expect the 200-Connection-Established response back.
                resp = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=2.0)
                assert resp.startswith(b'HTTP/1.1 200'), resp

                # Send some "TLS payload" bytes; expect them echoed back.
                payload = b'\x16\x03\x01' + b'X' * 200  # not real TLS, but an opaque blob
                writer.write(payload)
                await writer.drain()

                # Read the echo. Use a small read so we don't block forever.
                received = b''
                while len(received) < len(payload):
                    chunk = await asyncio.wait_for(
                        reader.read(len(payload) - len(received)), timeout=2.0
                    )
                    if not chunk:
                        break
                    received += chunk
                assert received == payload
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        finally:
            await relay.stop()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_relay_rejects_non_connect_method() -> None:
    """An HTTP GET to the relay returns 405 Method Not Allowed."""
    ws_port = _free_port()
    ws_server = await ws_serve(_echo_handler, '127.0.0.1', ws_port)
    try:
        relay = await start_upstream_proxy_relay(
            ws_url=f'ws://127.0.0.1:{ws_port}/v1/code/upstreamproxy/ws',
            session_id='cse_test',
            token='tok',
        )
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.port)
            try:
                writer.write(b'GET /foo HTTP/1.1\r\nHost: x\r\n\r\n')
                await writer.drain()
                resp = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=2.0)
                assert resp.startswith(b'HTTP/1.1 405'), resp
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        finally:
            await relay.stop()
    finally:
        ws_server.close()
        await ws_server.wait_closed()


@pytest.mark.asyncio
async def test_relay_oversized_header_rejected() -> None:
    """An over-8KB CONNECT header gets 400 Bad Request."""
    ws_port = _free_port()
    ws_server = await ws_serve(_echo_handler, '127.0.0.1', ws_port)
    try:
        relay = await start_upstream_proxy_relay(
            ws_url=f'ws://127.0.0.1:{ws_port}/v1/code/upstreamproxy/ws',
            session_id='cse_test',
            token='tok',
        )
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', relay.port)
            try:
                # Send 9KB of garbage with no CRLFCRLF — should hit the
                # 8KB cap and return 400.
                writer.write(b'X' * (9 * 1024))
                await writer.drain()
                resp = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=2.0)
                assert resp.startswith(b'HTTP/1.1 400'), resp
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        finally:
            await relay.stop()
    finally:
        ws_server.close()
        await ws_server.wait_closed()
