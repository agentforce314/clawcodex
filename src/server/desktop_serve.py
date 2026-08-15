"""Starlette app for ``clawcodex serve`` — the ClawCodex Desktop backend.

Route surface (all consumed by ``ui-desktop``):

- ``GET /api/health`` — unauthenticated liveness probe
  (``electron/backend-health.ts`` polls it before showing the shell).
- ``GET /api/status`` — token-gated backend facts
  (``electron/connection-config.ts`` reads ``auth_required`` to pick the
  auth mode; local mode is token auth, never OAuth).
- ``GET /`` — a page carrying ``window.__CLAWCODEX_SESSION_TOKEN__`` so
  ``electron/dashboard-token.ts`` can adopt an already-running backend's token
  when it recognizes the process as ours. When a built browser client is
  present (``ui-web/dist``), this route serves that app with the same global
  inlined, so one page answers both readers; without one it is the bare token
  page the desktop has always had. See :mod:`src.server.web_assets`.
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
import os
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
    # The base AgentServerConfig every session inherits. A session.create that
    # names a provider/model/effort (the composer's selection) is spawned from
    # a per-session copy of this — otherwise every session would boot the
    # default provider and a bad default (e.g. an expired Claude subscription)
    # would make the app unusable even after switching models.
    agent_config: Any = None
    # session_id -> live DesktopSession (created lazily by the gateway).
    sessions: dict[str, Any] = field(default_factory=dict)
    # Saved-transcript dir override (tests); default resolves per request.
    sessions_dir: Path | None = None

    def spawn_for(self, provider: str | None, model: str | None,
                  effort: str | None) -> Callable[..., Awaitable[Any]]:
        """A spawn closure honoring a session's provider/model/effort override.

        Falls back to the shared ``spawn_agent`` when nothing is overridden or
        no base config is available (tests inject a bare spawn).
        """
        if self.agent_config is None or not (provider or model or effort):
            return self.spawn_agent
        import dataclasses

        from src.server.agent_server import make_spawn_agent

        overrides: dict[str, Any] = {}
        if provider:
            overrides["provider_name"] = provider
        if model:
            overrides["model"] = model
        if effort:
            overrides["effort"] = effort
        return make_spawn_agent(dataclasses.replace(self.agent_config, **overrides))

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

        if request.method == "PUT":
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = None
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "invalid config body"},
                                    status_code=400)
            # The renderer wraps the payload as {"config": {...}}
            # (saveClawCodexConfig); accept the bare object too.
            return JSONResponse(_save_config_merged(unwrap_config_envelope(body)))

        # Strip a stale envelope on the way out too, so a config that already
        # picked one up can't be round-tripped straight back by the renderer's
        # whole-record autosave.
        return JSONResponse(redact_secrets(strip_config_envelope(load_config())))

    async def env_vars(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.config import load_config
        from src.providers import PROVIDER_INFO, provider_env_vars
        from src.secret_store import _config_env

        if request.method == "PUT":
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = None
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "invalid body"},
                                    status_code=400)
            key = body.get("key")
            value = body.get("value")
            if not isinstance(key, str) or not key.strip():
                return JSONResponse({"ok": False, "error": "key required"},
                                    status_code=400)
            if not isinstance(value, str):
                value = str(value) if value is not None else ""
            from src.secret_store import set_secret
            try:
                set_secret(key, value)
                return JSONResponse({"ok": True})
            except Exception as exc:  # noqa: BLE001
                logger.warning("desktop: env set failed", exc_info=True)
                return JSONResponse({"ok": False, "error": str(exc)},
                                    status_code=500)

        # GET - return all env vars in the EnvVarInfo format expected by the frontend
        config = load_config()
        stored_env = _config_env()
        providers = config.get("providers", {})

        # Build a map of provider -> canonical name for label lookup
        provider_labels = {pid: info.get("label", pid) for pid, info in PROVIDER_INFO.items()}

        # Map env var name -> provider info (from provider_env_vars)
        env_to_provider = {}
        for pid in PROVIDER_INFO:
            for env_name in provider_env_vars(pid):
                if env_name not in env_to_provider:
                    env_to_provider[env_name] = {"id": pid, "label": provider_labels[pid]}

        # Also include custom endpoint providers from config
        for provider_name, provider_cfg in providers.items():
            if isinstance(provider_cfg, dict):
                base_url = provider_cfg.get("base_url")
                if base_url and provider_name not in PROVIDER_INFO:
                    env_name = f"{provider_name.upper().replace('-', '_')}_API_KEY"
                    if env_name not in env_to_provider:
                        env_to_provider[env_name] = {"id": provider_name, "label": provider_name}

        # Build the response matching EnvVarInfo interface
        result: dict[str, Any] = {}
        all_keys = set(stored_env.keys()) | set(env_to_provider.keys()) | set(os.environ.keys())

        for key in sorted(all_keys):
            provider_info = env_to_provider.get(key)
            is_set = key in stored_env and bool(stored_env[key].strip())
            # Check if set in real env too
            if not is_set:
                env_val = os.environ.get(key)
                if env_val and env_val.strip():
                    is_set = True

            # Determine category
            category = "provider" if provider_info else "other"
            # Some known non-provider categories
            if key.startswith("TAVILY_") or key.startswith("BRAVE_") or key.startswith("SERP_"):
                category = "tools"
            elif key.startswith("GITHUB_") or key.startswith("GITLAB_") or key.startswith("BITBUCKET_"):
                category = "vcs"
            elif key.startswith("CLAWCODEX_"):
                category = "settings"

            result[key] = {
                "advanced": False,
                "category": category,
                "description": f"API key for {provider_info['label'] if provider_info else key}",
                "is_password": True,
                "is_set": is_set,
                "provider": provider_info["id"] if provider_info else None,
                "provider_label": provider_info["label"] if provider_info else None,
                "redacted_value": "••••••••" if is_set else None,
                "tools": [],
                "url": None,
            }

        return JSONResponse(result)

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

    async def model_options_rest(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.server.desktop_gateway_methods import _catalog_from_config

        import asyncio as _asyncio
        return JSONResponse(await _asyncio.to_thread(_catalog_from_config))

    async def model_auxiliary(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # Auxiliary task-model assignments aren't wired to a control yet; the
        # panel reads `main` and an (empty) task list and renders "using the
        # main model for everything", which is the true state.
        from src.config import get_default_provider, get_provider_config

        try:
            provider = get_default_provider()
            model = (get_provider_config(provider) or {}).get("default_model")
        except Exception:  # noqa: BLE001
            provider, model = None, None
        return JSONResponse({"main": {"model": model, "provider": provider}, "tasks": []})

    async def config_schema(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # There's no generated dataclass schema in this backend. Ship a focused
        # schema for the voice fields the user configures — most importantly the
        # transcription model, so Settings → Voice can pick it when STT fails.
        # Other panels render from their own known fields.
        return JSONResponse({
            "fields": {
                "stt.enabled": {
                    "type": "boolean",
                    "description": "Enable voice input (speech-to-text).",
                },
                "stt.provider": {
                    "type": "select",
                    "options": ["openai", "groq"],
                    "description": "Which provider transcribes your recordings. "
                    "OpenAI (whisper-1) uses api.openai.com; Groq hosts Whisper too.",
                },
                "stt.openai.model": {
                    "type": "string",
                    "description": "OpenAI transcription model — whisper-1, or a "
                    "newer id like gpt-4o-transcribe / gpt-4o-mini-transcribe.",
                },
                "stt.groq.model": {
                    "type": "string",
                    "description": "Groq transcription model — e.g. whisper-large-v3.",
                },
                "voice.auto_tts": {
                    "type": "boolean",
                    "description": "Automatically speak assistant replies.",
                },
            },
            "category_order": [],
        })

    async def cron_jobs(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"jobs": []})

    async def elevenlabs_voices(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"voices": []})

    async def session_detail(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        sid = request.path_params["session_id"]

        if request.method == "DELETE":
            from src.server.desktop_sessions import delete_session

            ok = delete_session(state.saved_sessions_dir(), sid)
            return JSONResponse({"ok": ok})

        if request.method == "PATCH":
            from src.server.desktop_sessions import update_session_meta

            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
            fields: dict[str, Any] = {}
            # The renderer sends whichever it's changing: name / archived / pinned.
            if "name" in body:
                fields["name"] = body["name"]
            if "title" in body:
                fields["name"] = body["title"]
            if "archived" in body:
                fields["archived"] = bool(body["archived"])
            if "pinned" in body:
                fields["pinned"] = bool(body["pinned"])
            ok = update_session_meta(state.saved_sessions_dir(), sid, **fields) if fields else False
            return JSONResponse({"ok": ok})

        from src.server.desktop_sessions import load_session_messages

        found = load_session_messages(state.saved_sessions_dir(), sid)
        if found is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        return JSONResponse({"session_id": found["session_id"],
                             "messages": found["messages"],
                             "message_count": found["message_count"]})

    async def sessions_search(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        from src.server.desktop_sessions import search_sessions

        return JSONResponse(
            search_sessions(state.saved_sessions_dir(), request.query_params.get("q", ""))
        )

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
        from src.skills.loader import get_all_skills

        model = provider = None
        has_env = False
        skill_count = 0
        try:
            provider = get_default_provider()
            model = (get_provider_config(provider) or {}).get("default_model")
            has_env = bool((load_config() or {}).get("env"))
            skill_count = len(get_all_skills())
        except Exception:  # noqa: BLE001 — profile card degrades soft
            pass
        return {
            "name": "default",
            "is_default": True,
            "path": str(get_user_config_dir()),
            "model": model,
            "provider": provider,
            "has_env": has_env,
            "skill_count": skill_count,
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

    # ---- Custom Endpoints ----
    # Stored in config.json under "custom_endpoints" key (list of endpoint objects)
    def _load_custom_endpoints() -> list[dict[str, Any]]:
        from src.config import load_config
        config = load_config()
        return config.get("custom_endpoints", [])

    def _save_custom_endpoints(endpoints: list[dict[str, Any]]) -> None:
        from src.config import _get_default_manager
        mgr = _get_default_manager()
        config = mgr.load_global_for_write()
        config["custom_endpoints"] = endpoints
        mgr.save_global(config)

    def _validate_endpoint_data(data: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate required fields for a custom endpoint."""
        required = ["name", "base_url", "model"]
        for field in required:
            if not data.get(field) or not str(data[field]).strip():
                return False, f"Missing required field: {field}"
        return True, None

    async def custom_endpoints_list(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        endpoints = _load_custom_endpoints()
        return JSONResponse({"endpoints": endpoints})

    async def custom_endpoints_create(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)

        # Validate required fields
        ok, msg = _validate_endpoint_data(body)
        if not ok:
            return JSONResponse({"ok": False, "error": msg}, status_code=400)

        endpoints = _load_custom_endpoints()
        endpoint_id = body.get("id") or body["name"].lower().replace(" ", "-").replace("_", "-")
        # Ensure unique ID
        base_id = endpoint_id
        counter = 1
        while any(e["id"] == endpoint_id for e in endpoints):
            endpoint_id = f"{base_id}-{counter}"
            counter += 1

        new_endpoint = {
            "id": endpoint_id,
            "name": body["name"].strip(),
            "base_url": body["base_url"].strip(),
            "model": body["model"].strip(),
            "api_key": body.get("api_key", "").strip() or None,
            "context_length": body.get("context_length"),
            "discover_models": body.get("discover_models", True),
            "is_current": body.get("make_default", False),
            "source": "api",
            "models": body.get("models", []),
            "api_key_preview": body.get("api_key") and "••••••••" or None,
            "has_api_key": bool(body.get("api_key", "").strip()),
        }

        # If this is the default, unset others
        if new_endpoint["is_current"]:
            for e in endpoints:
                e["is_current"] = False

        endpoints.append(new_endpoint)
        _save_custom_endpoints(endpoints)

        return JSONResponse({"ok": True, "id": endpoint_id, "endpoints": endpoints})

    async def custom_endpoints_validate(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)

        # Validate required fields
        ok, msg = _validate_endpoint_data(body)
        if not ok:
            return JSONResponse({"ok": False, "error": msg}, status_code=400)

        # Try to connect and fetch models
        from openai import OpenAI
        import os

        base_url = body["base_url"].strip()
        api_key = body.get("api_key", "").strip() or "EMPTY"
        model = body["model"].strip()

        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            models_response = client.models.list()
            models = [m.id for m in models_response.data]
        except Exception as exc:  # noqa: BLE001
            logger.warning("custom endpoint validation failed", exc_info=True)
            return JSONResponse({
                "ok": False,
                "reachable": False,
                "message": f"Connection failed: {exc}",
                "models": []
            })

        # If no model specified in form but we got models, use first one
        suggested_model = model if model in models else (models[0] if models else model)

        return JSONResponse({
            "ok": True,
            "reachable": True,
            "message": f"Endpoint reachable. Found {len(models)} models.",
            "models": models,
            "suggested_model": suggested_model
        })

    async def custom_endpoints_activate(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        endpoint_id = request.path_params["endpoint_id"]
        endpoints = _load_custom_endpoints()

        endpoint = next((e for e in endpoints if e["id"] == endpoint_id), None)
        if not endpoint:
            return JSONResponse({"ok": False, "error": "endpoint not found"}, status_code=404)

        # Set as current
        for e in endpoints:
            e["is_current"] = e["id"] == endpoint_id
        _save_custom_endpoints(endpoints)

        return JSONResponse({
            "ok": True,
            "provider": endpoint_id,
            "model": endpoint["model"]
        })

    async def custom_endpoints_delete(request: Request) -> Response:
        if not _token_ok(state, _rest_token(request)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        endpoint_id = request.path_params["endpoint_id"]
        endpoints = _load_custom_endpoints()

        endpoints = [e for e in endpoints if e["id"] != endpoint_id]
        _save_custom_endpoints(endpoints)

        return JSONResponse({"ok": True, "endpoints": endpoints})

    async def index(_: Request) -> Response:
        # Two readers, one page. dashboard-token.ts scrapes
        # __CLAWCODEX_SESSION_TOKEN__ to adopt a running backend's token; the
        # browser client (ui-web) reads the same global to open its socket. So
        # when a built web bundle is present this route serves that app with
        # the token inlined, and otherwise the bare token page the desktop has
        # always had — the desktop's contract is unchanged either way.
        from src.server.web_assets import web_index_html

        page = web_index_html(state.token)
        if page is not None:
            # No-store: the page carries a per-process token, so a cached copy
            # would point a later run's browser at a dead session.
            return HTMLResponse(page, headers={"Cache-Control": "no-store"})

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
        Route("/api/config", config, methods=["GET", "PUT"]),
        Route("/api/config/defaults", config_defaults),
        Route("/api/env", env_vars, methods=["GET", "PUT"]),
        Route("/api/providers/custom-endpoints", custom_endpoints_list, methods=["GET"]),
        Route("/api/providers/custom-endpoints", custom_endpoints_create, methods=["POST"]),
        Route("/api/providers/custom-endpoints/validate", custom_endpoints_validate, methods=["POST"]),
        Route("/api/providers/custom-endpoints/{endpoint_id}/activate", custom_endpoints_activate, methods=["POST"]),
        Route("/api/providers/custom-endpoints/{endpoint_id}", custom_endpoints_delete, methods=["DELETE"]),
        Route("/api/sessions", sessions_list),
        Route("/api/sessions/{session_id}/messages", session_messages),
        Route("/api/profiles", profiles),
        Route("/api/profiles/active", profiles_active),
        Route("/api/profiles/sessions", profile_sessions),
        Route("/api/profiles/sessions/sidebar", sidebar_sessions),
        Route("/api/model/info", model_info),
        Route("/api/model/options", model_options_rest),
        Route("/api/model/auxiliary", model_auxiliary),
        Route("/api/config/schema", config_schema),
        Route("/api/cron/jobs", cron_jobs),
        Route("/api/audio/elevenlabs/voices", elevenlabs_voices),
        Route("/api/sessions/search", sessions_search),
        Route("/api/sessions/{session_id}", session_detail,
              methods=["GET", "PATCH", "DELETE"]),
        Route("/api/audio/transcribe", audio_transcribe, methods=["POST"]),
        # ui-web's hashed chunks + icons. Empty (and this server unchanged)
        # when no bundle is built; declared after every /api route and before
        # the catch-all, since Starlette matches in order.
        *_web_routes(),
        Route("/", index),
        WebSocketRoute("/api/ws", gateway_ws),
        Route("/{rest:path}", not_found,
              methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
    ]
    return Starlette(routes=routes)


def _web_routes() -> list[Any]:
    """Static routes for the browser client, or none when it is not built."""
    from src.server.web_assets import web_routes

    return list(web_routes())


# ``config`` is not a key in the config schema — the file holds
# default_provider / providers / session / settings / env / projects / … at the
# top level. So a ``config`` key can only be a transport envelope that got
# stored by mistake, and treating it as data is what let one escape into
# ~/.clawcodex/config.json: the renderer autosaves the WHOLE record it last
# read, so a single mis-nested write is copied forward on every later save.
# It then shadows real settings — a voice/STT block written into the envelope
# is invisible to the backend, which reads the top level.


def unwrap_config_envelope(body: dict[str, Any]) -> dict[str, Any]:
    """``{"config": {...}}`` → the record. Idempotent, and unwraps repeats.

    A double wrap is the shape that does the damage: unwrapping once leaves a
    ``config`` key that then merges in as data.
    """
    record = body
    while isinstance(record, dict) and isinstance(record.get("config"), dict):
        record = record["config"]
    return record if isinstance(record, dict) else {}


def strip_config_envelope(record: dict[str, Any]) -> dict[str, Any]:
    """Drop a stored envelope key. Returns the same object when there is none."""
    if not isinstance(record, dict) or "config" not in record:
        return record
    return {k: v for k, v in record.items() if k != "config"}


def _save_config_merged(incoming: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge the (secret-redacted) incoming config over the stored global
    config and persist it.

    GET /api/config redacts secrets, so the renderer's config draft has no
    api_keys or ``env`` block. A REPLACE would wipe them — deep-merge layers
    the user's edits (voice.stt.*, display, …) on top of the stored config so
    credentials survive. Mirrors ``saveClawCodexConfig``'s documented merge
    semantics (not the REPLACE variant).
    """
    from src.config import _deep_merge, _get_default_manager

    try:
        manager = _get_default_manager()
        current = manager.load_global()
        merged = _deep_merge(current, strip_config_envelope(incoming))
        # `_deep_merge` only ever adds, so an envelope already on disk would
        # outlive every future save. Dropping it here repairs a config that
        # picked one up, on the next write, without a migration step.
        manager.save_global(strip_config_envelope(merged))
        manager.invalidate()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("desktop: config save failed", exc_info=True)
        return {"ok": False, "error": str(exc)}


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
    """Serialize ``value`` as a safe JS string literal for the inline page.

    Delegates to the shared escaper, which also neutralizes ``<``/``>``/``&``
    — ``json.dumps`` leaves those alone, so a value containing ``</script>``
    would close the element and everything after it would parse as markup.
    """
    from src.server.web_assets import js_string

    return js_string(value)


__all__ = ["DesktopServeState", "build_app", "ws_token"]
