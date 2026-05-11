"""MCP transport layer — adapters wrapping the official ``mcp`` PyPI SDK.

Path A of the ch15-mcp refactoring plan (see ``my-docs/ch15-mcp-sdk-survey.md``):
adopt the SDK for the four spec transports (stdio / Streamable HTTP / SSE /
WebSocket) and expose them through our existing ``McpTransport`` ABC so the
rest of the Python MCP layer (``client.py``, ``manager.py``) does not change.

This mirrors TypeScript's choice of using ``@modelcontextprotocol/sdk`` for the
same transports while keeping its own ``Transport`` abstraction at the call
site (typescript/src/services/mcp/client.ts:7-21).

Local additions sit alongside the SDK adapters: the linked-pair
``InProcessTransport`` (Phase 3 WI-3.1), the SDK control bridge, the
Claude.ai-proxy fetch wrapper. Those are out of scope for this file.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.websocket import websocket_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"


@dataclass
class JsonRpcMessage:
    """Internal JSON-RPC message wrapper.

    We keep this thin DTO so the ``McpClient`` code path stays library-agnostic
    even when transports are adapted to the SDK. ``to_dict`` / ``from_dict`` are
    used to round-trip through the SDK's pydantic ``JSONRPCMessage`` model.
    """

    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    id: int | str | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.method is not None:
            d["method"] = self.method
        if self.params is not None:
            d["params"] = self.params
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        if self.id is not None:
            d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JsonRpcMessage:
        return cls(
            method=d.get("method"),
            params=d.get("params"),
            result=d.get("result"),
            error=d.get("error"),
            id=d.get("id"),
            jsonrpc=d.get("jsonrpc", JSONRPC_VERSION),
        )


class McpTransport(ABC):
    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def send(self, message: JsonRpcMessage) -> None:
        ...

    @abstractmethod
    async def receive(self) -> JsonRpcMessage | None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...


class _SdkTransportAdapter(McpTransport):
    """Common bridge from the SDK's async-context-manager transports to our
    start/send/receive/close lifecycle.

    Subclasses override ``_open()`` to return the SDK's ``@asynccontextmanager``
    transport (e.g. ``stdio_client(params)``). We enter it through an
    ``AsyncExitStack`` in ``start()`` and exit in ``close()``. The streams
    yielded by the SDK are anyio memory object streams; anyio runs on top of
    asyncio when called from an asyncio loop, so they integrate transparently.
    """

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._read_stream: Any = None  # anyio MemoryObjectReceiveStream
        self._write_stream: Any = None  # anyio MemoryObjectSendStream
        self._closed = False

    @abstractmethod
    def _open(self) -> Any:
        """Return the SDK's ``@asynccontextmanager`` transport to enter.

        The CM yields a ``(read_stream, write_stream)`` pair (or
        ``(read_stream, write_stream, *extras)`` for transports like
        Streamable HTTP that emit additional handles like a session-ID hook).
        Subclasses are responsible for slicing extras off in ``_unpack``.
        """

    def _unpack(self, yielded: Any) -> tuple[Any, Any]:
        """Default: SDK yields a 2-tuple ``(read_stream, write_stream)``.

        Streamable HTTP yields a 3-tuple; ``StreamableHttpTransport`` overrides.
        """
        return yielded[0], yielded[1]

    async def start(self) -> None:
        if self._stack is not None:
            return  # idempotent
        if self._closed:
            raise RuntimeError("Transport closed; create a new instance to reconnect")
        self._stack = AsyncExitStack()
        try:
            cm = self._open()
            yielded = await self._stack.enter_async_context(cm)
            self._read_stream, self._write_stream = self._unpack(yielded)
        except BaseException:
            # Catch BaseException (not just Exception) so an asyncio.CancelledError
            # from a wrapping ``wait_for(..., timeout=...)`` still tears the
            # partially-entered stack down deterministically. Without this, the
            # transport would be left in a half-open state (``_stack`` non-None,
            # ``_closed`` False, ``is_connected`` True) after a connection-timeout
            # cancellation — a subprocess leak waiting to happen.
            try:
                await self._stack.aclose()
            except BaseException:  # pragma: no cover - defensive teardown
                pass
            self._stack = None
            raise

    async def send(self, message: JsonRpcMessage) -> None:
        if self._write_stream is None or self._closed:
            raise RuntimeError("Transport not started or already closed")
        sdk_msg = JSONRPCMessage.model_validate(message.to_dict())
        await self._write_stream.send(SessionMessage(sdk_msg))

    async def receive(self) -> JsonRpcMessage | None:
        """Read one valid message from the SDK stream.

        Loops past transient parse failures (the SDK injects ``Exception``
        items into the read stream on framing/validation failures, but the
        stream itself is still healthy — those messages must be skipped, not
        treated as a transport close). Returns None ONLY on real close
        (``EndOfStream`` / ``ClosedResourceError``).

        The single-bad-message-kills-connection behavior the previous
        implementation had silently abandoned every pending request to a
        full 5-min timeout.
        """
        if self._read_stream is None or self._closed:
            return None
        while True:
            try:
                item = await self._read_stream.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return None
            if isinstance(item, Exception):
                # Transient: SDK framing/validation failure. Log and skip; the
                # next ``receive()`` call (or the next iteration of the receive
                # loop) will read the next message off the still-healthy stream.
                logger.warning("MCP transport: skipping invalid SDK message: %s", item)
                continue
            try:
                data = item.message.model_dump(by_alias=True, exclude_none=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("MCP transport: failed to dump SDK message: %s", exc)
                continue
            return JsonRpcMessage.from_dict(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:  # pragma: no cover - SDK shutdown variance
                logger.debug("MCP transport: shutdown raised %s", exc)
            self._stack = None
        self._read_stream = None
        self._write_stream = None

    @property
    def is_connected(self) -> bool:
        return not self._closed and self._stack is not None


class StdioTransport(_SdkTransportAdapter):
    """Spec-compliant stdio transport (newline-delimited JSON-RPC).

    Wraps ``mcp.client.stdio.stdio_client``. The SDK handles the framing,
    process spawning, env merging, and the SIGTERM→SIGKILL graceful-shutdown
    sequence (``mcp/client/stdio/__init__.py:191-216``).
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._command = command
        self._args = list(args or [])
        self._env = dict(env) if env else None

    def _open(self) -> Any:
        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._env,
        )
        return stdio_client(params)

class HttpTransport(_SdkTransportAdapter):
    """Streamable HTTP transport (POST + optional SSE response channel).

    Wraps ``mcp.client.streamable_http.streamable_http_client``. The SDK
    implements both the request channel and the GET-subscribed SSE
    notification channel for server-initiated messages
    (``notifications/tools/list_changed`` etc.), addressing the WI-2.1
    ``TODO`` flagged in the refactoring plan.

    Headers are propagated by constructing a pre-configured ``httpx.AsyncClient``
    and passing it as the SDK's ``http_client`` parameter. We own the client's
    lifecycle (the SDK does not close caller-provided clients per its
    ``client_provided = http_client is not None`` check) — so the client is
    entered into our exit stack alongside the SDK transport.

    The SDK yields a 3-tuple ``(read_stream, write_stream, get_session_id)``;
    we discard the third element here. Future work (Phase 4 OAuth wiring)
    can capture the session-ID accessor for cache-clear-on-expiry semantics.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        super().__init__()
        self._url = url
        self._headers = dict(headers) if headers else None
        self._http_client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._stack is not None:
            return
        if self._closed:
            raise RuntimeError("Transport closed; create a new instance to reconnect")
        self._stack = AsyncExitStack()
        try:
            # Pre-create an httpx.AsyncClient so we can attach headers; the SDK
            # will use it but won't close it (caller-provided convention per
            # mcp/client/streamable_http.py:638 ``client_provided`` gate), so
            # register an aclose() callback on the stack to release it.
            self._http_client = httpx.AsyncClient(headers=self._headers)
            self._stack.push_async_callback(self._http_client.aclose)
            cm = streamable_http_client(url=self._url, http_client=self._http_client)
            yielded = await self._stack.enter_async_context(cm)
            self._read_stream, self._write_stream = self._unpack(yielded)
        except BaseException:
            # See _SdkTransportAdapter.start() for the BaseException rationale:
            # a wrapping wait_for() cancellation must tear down deterministically.
            try:
                await self._stack.aclose()
            except BaseException:  # pragma: no cover - defensive teardown
                pass
            self._stack = None
            self._http_client = None
            raise

    def _open(self) -> Any:  # not used; HttpTransport overrides start()
        raise NotImplementedError

    def _unpack(self, yielded: Any) -> tuple[Any, Any]:
        # streamable_http_client yields (read_stream, write_stream, get_session_id_fn)
        read_stream, write_stream, *_ = yielded
        return read_stream, write_stream

    async def close(self) -> None:
        await super().close()
        self._http_client = None


class SseTransport(_SdkTransportAdapter):
    """Legacy SSE transport (pre-Streamable-HTTP MCP servers).

    Wraps ``mcp.client.sse.sse_client``. Two-endpoint pattern: POST for
    client→server, GET with ``text/event-stream`` for server→client.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        super().__init__()
        self._url = url
        self._headers = dict(headers) if headers else None

    def _open(self) -> Any:
        return sse_client(url=self._url, headers=self._headers)


class WebSocketTransport(_SdkTransportAdapter):
    """WebSocket transport.

    Wraps ``mcp.client.websocket.websocket_client``. Headers are not currently
    supported by the SDK's WebSocket client signature; if the upstream SDK adds
    a ``headers`` kwarg in a future release, plumb it through here.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        super().__init__()
        self._url = url
        # SDK's websocket_client(url) doesn't accept headers as of v1.27.x;
        # we accept the kwarg for config-schema parity but warn loudly so
        # users debugging an auth failure don't have to chase a silently-
        # dropped Authorization header. If the SDK adds header support in a
        # future release, plumb it through and drop this warning.
        self._headers = dict(headers) if headers else None
        if self._headers:
            logger.warning(
                "WebSocketTransport received %d header(s) but the mcp SDK's "
                "websocket_client does not accept headers; they will NOT be "
                "sent on the WebSocket handshake. Affected URL: %s",
                len(self._headers), self._url,
            )

    def _open(self) -> Any:
        return websocket_client(url=self._url)
