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
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


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
    # Saved-transcript dir override (tests); default resolves per request.
    sessions_dir: Path | None = None

    def saved_sessions_dir(self) -> Path:
        if self.sessions_dir is not None:
            return self.sessions_dir
        from src.utils.clawcodex_dirs import get_sessions_dir

        return Path(get_sessions_dir())

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

    def _int_param(request: Request, name: str, default: int) -> int:
        try:
            return int(request.query_params.get(name, default))
        except (TypeError, ValueError):
            return default

    async def sessions_list(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.server.desktop_sessions import list_session_rows

        result = list_session_rows(
            state.saved_sessions_dir(),
            limit=_int_param(request, "limit", 20),
            offset=_int_param(request, "offset", 0),
            min_messages=_int_param(request, "min_messages", 0),
        )
        live = {
            getattr(s, "session_id", None) for s in state.sessions.values()
        }
        for row in result["sessions"]:
            if row["id"] in live:
                row["is_active"] = True
        return JSONResponse(result)

    async def session_messages(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.server.desktop_sessions import load_session_messages

        found = load_session_messages(
            state.saved_sessions_dir(), request.path_params["session_id"]
        )
        if found is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return JSONResponse(found)

    async def model_info(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.config import get_default_provider, get_provider_config

        try:
            provider = get_default_provider()
            cfg = get_provider_config(provider) or {}
            return JSONResponse(
                {"provider": provider, "model": cfg.get("default_model")}
            )
        except Exception:  # noqa: BLE001 — inspection endpoint, degrade soft
            return JSONResponse({"provider": None, "model": None})

    def _sessions_slice(request: Request, *, profile_tag: str = "default") -> dict[str, Any]:
        """One filtered slice of the saved-session list (shared by the
        profile-scoped route and the batched sidebar)."""
        from src.server.desktop_sessions import list_session_rows

        params = request.query_params
        source = params.get("source") or None
        exclude = {
            s for s in (params.get("exclude_sources") or "").split(",") if s
        }
        result = list_session_rows(
            state.saved_sessions_dir(),
            limit=_int_param(request, "limit", 40),
            offset=_int_param(request, "offset", 0),
            min_messages=_int_param(request, "min_messages", 0),
        )
        rows = [
            {**row, "profile": profile_tag}
            for row in result["sessions"]
            if (source is None or row.get("source") == source)
            and row.get("source") not in exclude
        ]
        return {**result, "sessions": rows}

    async def profile_sessions(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # Single-profile serve: every row belongs to "default"; the profile
        # query param only scopes recency windows, which is a no-op here.
        return JSONResponse(_sessions_slice(request))

    async def sidebar_sessions(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.server.desktop_sessions import list_session_rows

        params = request.query_params
        exclude = {
            s for s in (params.get("recents_exclude") or "").split(",") if s
        }
        try:
            recents_limit = max(1, int(params.get("recents_limit", 20)))
        except ValueError:
            recents_limit = 20
        listing = list_session_rows(
            state.saved_sessions_dir(), limit=recents_limit, min_messages=1
        )
        recents = [
            {**row, "profile": "default"}
            for row in listing["sessions"]
            if row.get("source") not in exclude
        ]
        # cron/messaging surfaces don't exist on this backend yet — empty
        # slices are the documented degrade shape.
        return JSONResponse(
            {
                "recents": {"sessions": recents},
                "cron": {"sessions": []},
                "messaging": {"sessions": []},
            }
        )

    def _default_profile_info() -> dict[str, Any]:
        from src.config import get_default_provider, get_provider_config, load_config
        from src.utils.clawcodex_dirs import get_user_config_dir

        model = provider = None
        has_env = False
        try:
            provider = get_default_provider()
            model = (get_provider_config(provider) or {}).get("default_model")
            has_env = bool((load_config() or {}).get("env"))
        except Exception:  # noqa: BLE001 — profile card degrades soft
            pass
        return {
            "name": "default",
            "is_default": True,
            "path": str(get_user_config_dir()),
            "model": model,
            "provider": provider,
            "has_env": has_env,
            "skill_count": 0,
        }

    async def profiles(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"profiles": [_default_profile_info()]})

    async def profiles_active(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"active": "default", "current": "default"})

    async def config_defaults(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.config import get_default_config

        return JSONResponse(redact_secrets(get_default_config()))

    async def audio_transcribe(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.server.desktop_audio import transcribe_data_url

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        result = await transcribe_data_url(
            str(body.get("data_url") or ""), body.get("mime_type")
        )
        payload: dict[str, Any] = {"ok": result.ok, "transcript": result.transcript}
        if result.provider:
            payload["provider"] = result.provider
        if result.error:
            payload["error"] = result.error
        # 200 with ok:false is the renderer's soft-fail shape; a hard status
        # would surface a generic "request failed" instead of our message.
        return JSONResponse(payload)

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

    async def not_found(request: Request) -> Response:
        # Named 404s: the remaining REST surface is being built stage by
        # stage — logging which paths the shell actually asks for is the
        # to-do list (and the fastest way to spot a wrong route).
        # warning: the default logging setup surfaces WARNING+ on stderr, and
        # an unimplemented route IS a warning during the staged port.
        logger.warning("serve: 404 %s %s", request.method, request.url.path)
        return JSONResponse({"error": "not found"}, status_code=404)

    routes = [
        Route("/api/health", health),
        Route("/api/status", status),
        Route("/api/config", config),
        Route("/api/config/defaults", config_defaults),
        Route("/api/sessions", sessions_list),
        Route("/api/sessions/{session_id}/messages", session_messages),
        Route("/api/profiles", profiles),
        Route("/api/profiles/active", profiles_active),
        Route("/api/profiles/sessions", profile_sessions),
        Route("/api/profiles/sessions/sidebar", sidebar_sessions),
        Route("/api/model/info", model_info),
        Route("/api/audio/transcribe", audio_transcribe, methods=["POST"]),
        Route("/", index),
        WebSocketRoute("/api/ws", gateway_ws),
        Route("/{rest:path}", not_found,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
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
