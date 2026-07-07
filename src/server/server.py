"""Direct Connect local server (cc:// + cc+unix:// URL schemes).

Reverse-engineered from ``main.tsx:3965`` which imports
``./server/server.js``. The TS source isn't in this snapshot; the
contract is deduced from the client side
(``createDirectConnectSession.ts`` and ``directConnectManager.ts``):

  - ``POST /sessions`` accepts ``{cwd, dangerously_skip_permissions?}``
    and returns ``{session_id, ws_url, work_dir?, auth_token}``.
  - WS endpoint accepts NDJSON-over-WS, with ``Authorization: Bearer``
    on the upgrade request OR ``?token=<token>`` query param.
  - 5-state ``SessionState`` lifecycle persisted to
    ``~/.clawcodex/server-sessions.json`` (via ``SessionManager``).

**Architecture** — for robustness, the server uses **two separate
listeners**: one HTTP (``asyncio.start_server`` + minimal HTTP/1.1
parser) for ``POST /sessions``, and one WS (``websockets.asyncio.server``)
for ``/ws/<session_id>``. The ``ws_url`` returned by ``POST /sessions``
points at the WS port. This avoids depending on websockets internal
``Protocol`` APIs to wrap an HTTP-upgraded socket.

WI-1.9 (RESERVED) refines the spec once integration tests reveal gaps
in the reverse-engineered protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import websockets
from websockets.asyncio.server import ServerConnection, serve as ws_serve

from .session_manager import SessionManager
from .types import ServerConfig, SessionState

logger = logging.getLogger(__name__)


#: Type for the callback that spawns an agent subprocess for a session.
#: The implementation is out of scope for this minimal cut — tests
#: provide a fake that returns an in-process pipe; the real CLI wiring
#: lands in a follow-up.
SpawnAgent = Callable[
    [str, str, str | None],  # session_id, cwd, permission_mode
    Awaitable['AgentHandle'],
]


@dataclass
class AgentHandle:
    """Handle to a spawned agent subprocess (or test double).

    ``send_to_agent``: write a JSON-encoded SDK message into the agent's
    stdin (caller adds a trailing newline if needed).
    ``messages_from_agent``: async iterator yielding SDK message dicts
    parsed from the agent's stdout.
    ``shutdown``: terminate the agent and clean up.
    """

    send_to_agent: Callable[[dict], Awaitable[None]]
    messages_from_agent: Callable[[], AsyncIterator[dict]]
    shutdown: Callable[[], Awaitable[None]]


# ─── HTTP/1.1 minimal parser for POST /sessions ────────────────────────────


@dataclass
class _ParsedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


async def _read_http_request(
    reader: asyncio.StreamReader,
    *,
    max_header_bytes: int = 64 * 1024,
    max_body_bytes: int = 1 * 1024 * 1024,
) -> _ParsedRequest | None:
    """Read one HTTP/1.1 request from ``reader``. Returns None on malformed input."""
    head_buf = bytearray()
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            return None
        head_buf.extend(chunk)
        end = head_buf.find(b'\r\n\r\n')
        if end != -1:
            break
        if len(head_buf) > max_header_bytes:
            return None
    head_text = head_buf[:end].decode('utf-8', errors='replace')
    leftover = bytes(head_buf[end + 4 :])
    lines = head_text.split('\r\n')
    if not lines:
        return None
    request_line = lines[0].split()
    if len(request_line) != 3:
        return None
    method, path, version = request_line
    if not version.upper().startswith('HTTP/1.'):
        return None

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        headers[key.strip().lower()] = value.strip()

    content_length_str = headers.get('content-length', '0') or '0'
    try:
        content_length = int(content_length_str)
    except ValueError:
        return None
    if content_length < 0 or content_length > max_body_bytes:
        return None
    body = bytearray(leftover[:content_length])
    while len(body) < content_length:
        chunk = await reader.read(content_length - len(body))
        if not chunk:
            return None
        body.extend(chunk)

    return _ParsedRequest(
        method=method.upper(),
        path=path,
        headers=headers,
        body=bytes(body),
    )


def _build_http_response(
    *,
    status: int,
    reason: str,
    body: bytes = b'',
    content_type: str = 'application/json',
) -> bytes:
    return (
        f'HTTP/1.1 {status} {reason}\r\n'
        f'Content-Type: {content_type}\r\n'
        f'Content-Length: {len(body)}\r\n'
        f'Connection: close\r\n'
        f'\r\n'
    ).encode('ascii') + body


# ─── Server ────────────────────────────────────────────────────────────────


@dataclass
class DirectConnectServer:
    """Direct Connect server — HTTP ``/sessions`` + WS ``/ws/<sid>``.

    Lifecycle:
        srv = DirectConnectServer(config, manager, spawn_agent)
        await srv.start()
        await srv.serve_forever()  # or use srv.stop() to shut down
        await srv.stop()

    Two listeners: HTTP on ``config.port`` (or ephemeral) and WS on a
    separate ephemeral port. ``ws_url`` in the create-session response
    points at the WS port. Auth is per-session: ``POST /sessions``
    returns ``auth_token``; the WS upgrade requires it via
    ``Authorization: Bearer <token>`` or ``?token=<token>``.
    """

    config: ServerConfig
    manager: SessionManager
    spawn_agent: SpawnAgent
    _http_server: asyncio.AbstractServer | None = None
    _ws_server: asyncio.Server | None = None
    _session_tokens: dict[str, str] = field(default_factory=dict)
    _ws_port: int | None = None

    # ─── Public lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Bind both listeners; ``serve_forever`` to actually run."""
        # WS listener first so we know its port for the ``ws_url`` we
        # hand out from the HTTP route.
        self._ws_server = await ws_serve(
            self._handle_ws_connection,
            host=self.config.host or '127.0.0.1',
            port=0,  # ephemeral
        )
        ws_sockets = list(self._ws_server.sockets or [])
        if not ws_sockets:
            raise RuntimeError('DirectConnectServer: WS listener has no socket')
        self._ws_port = ws_sockets[0].getsockname()[1]

        # HTTP listener.
        if self.config.unix:
            self._http_server = await asyncio.start_unix_server(
                self._handle_http_connection,
                path=self.config.unix,
            )
        else:
            self._http_server = await asyncio.start_server(
                self._handle_http_connection,
                host=self.config.host or '127.0.0.1',
                port=self.config.port,
            )

    async def serve_forever(self) -> None:
        if self._http_server is None or self._ws_server is None:
            raise RuntimeError('start() must be called before serve_forever()')
        # Both servers run concurrently; cancellation of either tears
        # down both atomically via the gather.
        await asyncio.gather(
            self._http_server.serve_forever(),
            self._ws_server.serve_forever(),
            return_exceptions=True,
        )

    async def stop(self) -> None:
        if self._http_server is not None:
            self._http_server.close()
            await self._http_server.wait_closed()
            self._http_server = None
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        for sid in list(self.manager.active_session_ids()):
            await self.manager.stop_session(sid)

    @property
    def bound_http_port(self) -> int | None:
        """Bound HTTP port (None if not started or Unix-only)."""
        if self._http_server is None:
            return None
        sockets = self._http_server.sockets
        if not sockets:
            return None
        sock = sockets[0]
        if sock.family.name == 'AF_UNIX':
            return None
        return sock.getsockname()[1]

    @property
    def bound_ws_port(self) -> int | None:
        return self._ws_port

    # ─── HTTP listener handler ───────────────────────────────────────

    async def _handle_http_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """One accepted HTTP connection: parse request, dispatch route."""
        try:
            request = await _read_http_request(reader)
            if request is None:
                writer.write(_build_http_response(
                    status=400, reason='Bad Request', body=b'malformed request',
                ))
                await writer.drain()
                return

            if request.method == 'POST' and request.path == '/sessions':
                await self._handle_post_sessions(request, writer)
            else:
                writer.write(_build_http_response(
                    status=404, reason='Not Found', body=b'no such route',
                ))
                await writer.drain()
        except (ConnectionError, OSError) as exc:
            logger.debug('[server] HTTP connection error: %s', exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _handle_post_sessions(
        self,
        request: _ParsedRequest,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Create a new session and return ``{session_id, ws_url, work_dir, auth_token}``."""
        if self.config.auth_token:
            authz = request.headers.get('authorization', '')
            expected = f'Bearer {self.config.auth_token}'
            if authz != expected:
                writer.write(_build_http_response(
                    status=401, reason='Unauthorized',
                    body=b'{"error":"invalid auth token"}',
                ))
                await writer.drain()
                return

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            writer.write(_build_http_response(
                status=400, reason='Bad Request',
                body=b'{"error":"invalid JSON body"}',
            ))
            await writer.drain()
            return

        if not isinstance(payload, dict):
            writer.write(_build_http_response(
                status=400, reason='Bad Request',
                body=b'{"error":"body must be a JSON object"}',
            ))
            await writer.drain()
            return

        cwd = payload.get('cwd')
        if not isinstance(cwd, str) or not cwd:
            writer.write(_build_http_response(
                status=400, reason='Bad Request',
                body=b'{"error":"cwd is required (non-empty string)"}',
            ))
            await writer.drain()
            return

        try:
            info = self.manager.create_session(cwd=cwd)
        except RuntimeError as exc:
            writer.write(_build_http_response(
                status=503, reason='Service Unavailable',
                body=json.dumps({'error': str(exc)}).encode('utf-8'),
            ))
            await writer.drain()
            return

        token = secrets.token_urlsafe(32)
        self._session_tokens[info.id] = token

        host = self.config.host or '127.0.0.1'
        ws_port = self._ws_port
        ws_url = f'ws://{host}:{ws_port}/ws/{info.id}?token={token}'

        body = json.dumps({
            'session_id': info.id,
            'ws_url': ws_url,
            'work_dir': info.work_dir,
            'auth_token': token,
        }).encode('utf-8')
        writer.write(_build_http_response(
            status=201, reason='Created', body=body,
        ))
        await writer.drain()

    # ─── WS listener handler ─────────────────────────────────────────

    async def _handle_ws_connection(self, ws: ServerConnection) -> None:
        """One accepted WS connection: validate session/token, then pump."""
        # The ``websockets`` library exposes the request path on
        # ``ws.request``. URL: ``/ws/<session_id>[?token=<token>]``.
        request = ws.request
        if request is None:
            await ws.close(code=1008, reason='no request')
            return
        path = request.path
        prefix = '/ws/'
        if not path.startswith(prefix):
            await ws.close(code=1008, reason='no such route')
            return
        sid_and_query = path[len(prefix):]
        sid, _, query_str = sid_and_query.partition('?')
        if not sid:
            await ws.close(code=1008, reason='missing session id')
            return

        expected_token = self._session_tokens.get(sid)
        if expected_token is None:
            await ws.close(code=1008, reason='no such session')
            return

        # Accept the per-session token T_s (minted above, carried in the ws_url
        # query param) OR the launcher's global token T_g (required on POST
        # /sessions). The REAL openclaude TS client keeps the global token and
        # sends it as the WS ``Authorization: Bearer`` header — it ignores the
        # per-session ``auth_token`` in the POST response — so a strict
        # per-session-only check rejects it (client sends T_g, server expected
        # T_s; see critic B1a / migration plan). Both tokens are per-launch
        # secrets behind the loopback bind and the POST /sessions gate, so
        # honouring either on the WS is no weaker than POST, which already
        # accepts T_g. Check BOTH the Bearer header and the query param so a
        # transport quirk (the real client's header is bun-only) can't lock a
        # client out.
        authz = request.headers.get('authorization', '') if request.headers else ''
        header_token = authz[len('Bearer '):] if authz.startswith('Bearer ') else ''
        query_token = (parse_qs(query_str).get('token') or [''])[0]
        accepted = [t for t in (expected_token, self.config.auth_token) if t]

        def _token_ok(provided: str) -> bool:
            return bool(provided) and any(
                secrets.compare_digest(provided, valid) for valid in accepted
            )

        if not (_token_ok(header_token) or _token_ok(query_token)):
            await ws.close(code=1008, reason='invalid token')
            return

        info = self.manager.get(sid)
        if info is None:
            await ws.close(code=1008, reason='session not found')
            return

        try:
            agent = await self.spawn_agent(sid, info.work_dir, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning('[server] agent spawn failed for %s: %s', sid, exc)
            await ws.close(code=1011, reason='agent spawn failed')
            return
        self.manager.mark_running(sid)

        try:
            await self._run_ws_session(ws, sid, agent)
        finally:
            # Drop the per-session token so it can't be reused after
            # this WS connection ends.
            self._session_tokens.pop(sid, None)
            await agent.shutdown()
            await self.manager.stop_session(sid)

    async def _run_ws_session(
        self,
        ws: ServerConnection,
        session_id: str,
        agent: AgentHandle,
    ) -> None:
        """Pump frames between the WS and the agent subprocess."""

        async def outbound() -> None:
            try:
                async for msg in agent.messages_from_agent():
                    await ws.send(json.dumps(msg))
            except (
                websockets.exceptions.ConnectionClosed,
                ConnectionError,
                OSError,
            ):
                return

        async def inbound() -> None:
            try:
                async for raw in ws:
                    text = raw if isinstance(raw, str) else raw.decode('utf-8', errors='replace')
                    for line in text.split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(parsed, dict):
                            try:
                                await agent.send_to_agent(parsed)
                            except (ConnectionError, OSError):
                                return
            except websockets.exceptions.ConnectionClosed:
                return

        out_task = asyncio.get_running_loop().create_task(outbound())
        in_task = asyncio.get_running_loop().create_task(inbound())
        try:
            await asyncio.wait(
                [out_task, in_task], return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (out_task, in_task):
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (
                    asyncio.CancelledError,
                    websockets.exceptions.ConnectionClosed,
                    ConnectionError,
                    OSError,
                ):
                    pass


__all__ = ['AgentHandle', 'DirectConnectServer', 'SpawnAgent']
