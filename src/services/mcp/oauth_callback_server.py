"""OAuth callback HTTP listener for the loopback redirect URI.

Phase 4 WI-4.3 (gap #3). The OAuth 2.0 authorization-code flow redirects
the user-agent (browser) to a ``http://localhost:PORT/callback?code=...&
state=...`` URL after the user authorizes. This module spins up a short-
lived asyncio TCP listener on that port, parses the redirect's query
params, validates ``state`` against the value the client originally sent
(CSRF defense), and returns the code to the OAuth flow.

Stdlib-only (asyncio + http.server-style line parsing) — avoids a new
runtime dep. The listener handles exactly one request, returns a static
"you can close this window" HTML body, and stops.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 300.0  # 5 minutes — bounds the operator wait
_OK_BODY = (
    "<!DOCTYPE html><html><head><title>Authorization complete</title></head>"
    "<body><h1>Authorization complete</h1>"
    "<p>You can close this window and return to the CLI.</p></body></html>"
)
_ERROR_BODY = (
    "<!DOCTYPE html><html><head><title>Authorization failed</title></head>"
    "<body><h1>Authorization failed</h1>"
    "<p>{reason}</p></body></html>"
)


@dataclass
class CallbackResult:
    """Result of a successful OAuth redirect."""

    code: str
    state: str


class OAuthCallbackError(RuntimeError):
    """Raised when the callback arrives malformed, with an error, with a
    state mismatch, or when the wait times out."""


async def wait_for_callback(
    port: int,
    expected_state: str,
    *,
    path: str = "/callback",
    timeout: float = _DEFAULT_TIMEOUT_S,
    host: str = "127.0.0.1",
) -> CallbackResult:
    """Listen on ``http://host:port{path}`` for the OAuth redirect.

    Args:
        port: TCP port to bind on the loopback interface.
        expected_state: The CSRF token the client sent in the
            authorization URL. The redirect's ``state`` param MUST match.
        path: Expected URL path of the redirect (default ``/callback``).
        timeout: Maximum seconds to wait for the redirect (default 5min).
        host: Bind address. ``127.0.0.1`` by default; RFC 8252 §7.3
            prefers this over ``localhost`` to avoid DNS rebinding.

    Returns:
        ``CallbackResult(code, state)`` on success.

    Raises:
        OAuthCallbackError: malformed callback, state mismatch, OAuth
            error response, or timeout.
    """
    loop = asyncio.get_event_loop()
    result_future: asyncio.Future[CallbackResult] = loop.create_future()

    async def handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line_b = await reader.readline()
            request_line = request_line_b.decode("latin-1", errors="replace").strip()
            # Drain the rest of the request headers (don't care about them).
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
            await _process_request(
                request_line, expected_state, path, result_future, writer
            )
        except Exception as exc:
            logger.debug("OAuth callback handler error: %s", exc)
            if not result_future.done():
                result_future.set_exception(OAuthCallbackError(str(exc)))
        finally:
            try:
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:  # pragma: no cover - socket teardown variance
                pass

    server = await asyncio.start_server(handle_client, host=host, port=port)
    try:
        return await asyncio.wait_for(result_future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise OAuthCallbackError(
            f"OAuth callback timed out after {timeout:.0f}s"
        ) from exc
    finally:
        server.close()
        try:
            await server.wait_closed()
        except Exception:  # pragma: no cover
            pass


async def _process_request(
    request_line: str,
    expected_state: str,
    expected_path: str,
    result_future: asyncio.Future[CallbackResult],
    writer: asyncio.StreamWriter,
) -> None:
    """Parse the GET request line, validate, resolve the future."""
    parts = request_line.split(" ", 2)
    if len(parts) < 2 or parts[0] != "GET":
        _write_response(writer, 405, "Method Not Allowed", _ERROR_BODY.format(
            reason="Only GET is supported on the callback endpoint."
        ))
        if not result_future.done():
            result_future.set_exception(OAuthCallbackError("non-GET callback"))
        return

    request_target = parts[1]
    parsed = urlparse(request_target)
    if parsed.path != expected_path:
        _write_response(writer, 404, "Not Found", _ERROR_BODY.format(
            reason=f"Unexpected path: {parsed.path}",
        ))
        # Don't resolve — wait for the real callback.
        return

    params = parse_qs(parsed.query, keep_blank_values=True)

    error = (params.get("error") or [None])[0]
    if error:
        description = (params.get("error_description") or [""])[0]
        msg = f"OAuth error: {error}" + (f" — {description}" if description else "")
        _write_response(writer, 400, "Bad Request", _ERROR_BODY.format(reason=msg))
        if not result_future.done():
            result_future.set_exception(OAuthCallbackError(msg))
        return

    state = (params.get("state") or [""])[0]
    if state != expected_state:
        msg = "State mismatch (CSRF defense)"
        _write_response(writer, 400, "Bad Request", _ERROR_BODY.format(reason=msg))
        if not result_future.done():
            result_future.set_exception(OAuthCallbackError(msg))
        return

    code = (params.get("code") or [""])[0]
    if not code:
        msg = "Missing authorization code"
        _write_response(writer, 400, "Bad Request", _ERROR_BODY.format(reason=msg))
        if not result_future.done():
            result_future.set_exception(OAuthCallbackError(msg))
        return

    _write_response(writer, 200, "OK", _OK_BODY)
    if not result_future.done():
        result_future.set_result(CallbackResult(code=code, state=state))


def _write_response(
    writer: asyncio.StreamWriter, status: int, reason: str, body: str
) -> None:
    body_bytes = body.encode("utf-8")
    lines = [
        f"HTTP/1.1 {status} {reason}",
        f"Content-Length: {len(body_bytes)}",
        "Content-Type: text/html; charset=utf-8",
        "Connection: close",
        "",
        "",
    ]
    head = "\r\n".join(lines).encode("ascii")
    writer.write(head + body_bytes)
