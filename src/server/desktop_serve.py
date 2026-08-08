"""Starlette app for ``clawcodex serve`` — the ClawCodex Desktop backend.

Route surface (all consumed by ``ui-desktop``):

- ``GET /api/health`` — unauthenticated liveness probe
  (``electron/backend-health.ts`` polls it before showing the shell).
- ``GET /api/status`` — token-gated backend facts
  (``electron/connection-config.ts`` reads ``auth_required`` to pick the
  auth mode; local mode is token auth, never OAuth).
- ``GET /`` — one inline page carrying ``window.__CLAWCODEX_SESSION_TOKEN__``
  so ``electron/dashboard-token.ts`` can adopt an already-running backend's
  token when it recognizes the process as ours.
- ``WS /api/ws`` — the JSON-RPC gateway socket (chat surface); handled by
  :mod:`src.server.desktop_gateway`.

Auth: REST accepts the ``X-ClawCodex-Session-Token`` header or a Bearer
token; the WebSocket accepts ``?token=``. One constant-time comparison,
loopback binding, no cookies — this is the local token mode of the desktop's
connection config.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket


@dataclass
class DesktopServeState:
    """Process-wide state shared by the routes and the gateway sockets."""

    token: str
    workspace: str
    manager: Any
    spawn_agent: Callable[..., Awaitable[Any]]
    protocol_version: str
    # session_id -> live DesktopSession (created lazily by the gateway).
    sessions: dict[str, Any] = field(default_factory=dict)

    async def shutdown(self) -> None:
        """Best-effort shutdown of every live agent session."""
        for session in list(self.sessions.values()):
            try:
                await session.shutdown()
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
        self.sessions.clear()


def _token_ok(state: DesktopServeState, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(state.token, presented)


def _rest_token(request: Request) -> str | None:
    header = request.headers.get("x-clawcodex-session-token")
    if header:
        return header
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token")


def ws_token(websocket: WebSocket) -> str | None:
    """Token presented on a gateway socket (query param, header fallback)."""
    return (
        websocket.query_params.get("token")
        or websocket.headers.get("x-clawcodex-session-token")
    )


def build_app(state: DesktopServeState) -> Starlette:
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def status(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(
            {
                "status": "ok",
                "auth_required": False,
                "protocol_version": state.protocol_version,
                "workspace": state.workspace,
                "app": "clawcodex",
            }
        )

    async def config(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.config import load_config

        return JSONResponse(redact_secrets(load_config()))

    async def index(_: Request) -> Response:
        # dashboard-token.ts scrapes this global from the served page to adopt
        # a running backend's token. Serve nothing else here — the desktop
        # renderer ships in the app, not from this server.
        html = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<title>ClawCodex</title></head><body>"
            "<script>window.__CLAWCODEX_SESSION_TOKEN__ = "
            f"{_js_string(state.token)};</script>"
            "ClawCodex backend</body></html>"
        )
        return HTMLResponse(html)

    async def gateway_ws(websocket: WebSocket) -> None:
        if not _token_ok(state, ws_token(websocket)):
            # Starlette closes with 403 by default on close before accept;
            # accept-then-close(4401) would leak a frame — just reject.
            await websocket.close(code=4401)
            return
        # Lazy: the gateway pulls in the agent stack; unauthorized probes
        # (and the REST-only tests) never pay for it.
        from src.server.desktop_gateway import handle_gateway_socket

        await handle_gateway_socket(websocket, state)

    routes = [
        Route("/api/health", health),
        Route("/api/status", status),
        Route("/api/config", config),
        Route("/", index),
        WebSocketRoute("/api/ws", gateway_ws),
    ]
    return Starlette(routes=routes)


_SECRET_KEY_MARKERS = ("api_key", "apikey", "token", "secret", "password")


def redact_secrets(value: Any) -> Any:
    """Deep-copy ``value`` with secret-bearing entries removed.

    The merged config is the single config file — it carries the ``env``
    block (user API keys) and provider ``api_key`` fields. The desktop only
    needs the behavioral sections (display/agent/terminal/…); secrets never
    cross this REST surface (env management gets its own guarded routes
    later, mirroring the reference's reveal flow).
    """
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered == "env":
                continue
            if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
                continue
            clean[key] = redact_secrets(item)
        return clean
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def _js_string(value: str) -> str:
    """Serialize ``value`` as a safe JS string literal for the inline page."""
    import json

    return json.dumps(value)


__all__ = ["DesktopServeState", "build_app", "ws_token"]
