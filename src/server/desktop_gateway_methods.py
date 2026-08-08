"""Gateway sessions + JSON-RPC method handlers for ``clawcodex serve``.

The desktop renderer speaks the same RPC vocabulary the TUI app does; the
TUI's local adapter (``ui-tui/src/gatewayClient.ts``) is the reference for
how each method maps onto the agent protocol — this module is its
server-side, multi-session counterpart:

- ``prompt.submit`` → inbound ``{"type":"user"}`` frame, acked immediately
  (turn progress is event-driven),
- ``session.interrupt`` → fire-and-forget ``interrupt`` control,
- settings-ish methods → ``control_request``/``control_response`` round-trips
  into the session's agent (``_control_query``),
- permission asks ← server-initiated ``can_use_tool`` control_requests, parked
  per session and resolved by ``approval.respond {choice, session_id}``.

Every live session is one in-process agent (``make_spawn_agent``) plus a pump
task that translates its outbound frames into gateway events and broadcasts
them to every connected socket.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from starlette.websockets import WebSocket

from src.server.desktop_gateway import send_event
from src.server.desktop_gateway_translate import (
    approval_request_payload,
    translate_frame,
    usage_payload,
)
from src.server.desktop_serve import DesktopServeState

logger = logging.getLogger(__name__)

CONTROL_TIMEOUT_S = 30.0

# GUI↔backend contract version. The ClawCodex ladder restarts at 1 (the
# reference implementation was at 5); bump when the desktop starts requiring
# a capability this server ships (the renderer's REQUIRED_BACKEND_CONTRACT in
# ui-desktop/src/store/updates.ts must match).
DESKTOP_CONTRACT = 1


def _init_session_info(init: dict[str, Any]) -> dict[str, Any]:
    """system/init frame → the ``session.info`` payload the renderer reads."""
    payload: dict[str, Any] = {"running": False, "desktop_contract": DESKTOP_CONTRACT}
    cwd = init.get("cwd")
    if cwd:
        payload["cwd"] = cwd
    mode = init.get("permissionMode") or init.get("permission_mode")
    if mode:
        payload["approval_mode"] = mode
    model = init.get("model")
    if model:
        payload["model"] = model
    # The renderer's picker only prefers the session's selection when BOTH
    # model and provider are set (currentPickerSelection); omitting provider
    # made the picker fall back to the catalog while the composer chip kept
    # showing the session's model — the two disagreed.
    provider = init.get("provider")
    if provider:
        payload["provider"] = provider
    session_id = init.get("session_id")
    if session_id:
        payload["stored_session_id"] = session_id
    return payload


class DesktopSession:
    """One live agent session, pumped to the gateway sockets."""

    def __init__(self, session_id: str, state: DesktopServeState) -> None:
        self.session_id = session_id
        self.state = state
        self.agent: Any = None
        self.pump_task: asyncio.Task | None = None
        self.init_info: dict[str, Any] = {}
        self.init_seen = asyncio.Event()
        # My queries INTO the agent (control_request → control_response).
        self._pending_control: dict[str, asyncio.Future] = {}
        # The agent's asks OF the user (can_use_tool …), keyed by request_id;
        # the newest is what approval.respond resolves (the renderer parks one
        # approval per session).
        self._pending_asks: dict[str, dict[str, Any]] = {}
        self._last_ask_id: str | None = None
        self.sockets: set[WebSocket] = set()
        # Scheduled session.info refreshes; held so they aren't GC'd mid-flight.
        self._background: set[asyncio.Task] = set()

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self, cwd: str, spawn: Any = None) -> None:
        # spawn's third arg is a permission-mode override, NOT a resume id;
        # resuming a stored session is a post-init `resume` control request.
        spawn = spawn or self.state.spawn_agent
        self.agent = await spawn(self.session_id, cwd, None)
        self.pump_task = asyncio.create_task(
            self._pump(), name=f"desktop-session-{self.session_id}"
        )

    async def shutdown(self) -> None:
        for task in list(self._background):
            task.cancel()
        self._background.clear()
        if self.pump_task is not None:
            self.pump_task.cancel()
        if self.agent is not None:
            try:
                await self.agent.shutdown()
            except Exception:  # noqa: BLE001
                pass
        for fut in self._pending_control.values():
            if not fut.done():
                fut.cancel()

    # ── broadcast ────────────────────────────────────────────────────────────

    async def _broadcast(self, type_: str, payload: Any = None) -> None:
        for ws in list(self.sockets):
            await send_event(ws, type_, payload, session_id=self.session_id)

    async def publish_session_info(self, **extra: Any) -> None:
        """Re-read live settings and broadcast a full ``session.info``.

        The renderer reconciles model/provider/effort from session.info and
        expects them stamped on every one — a switch that doesn't publish
        leaves the composer chip showing the session's spawn-time model
        forever (it reads the session state, not the composer draft).

        NEVER await this from inside ``_pump``/``_route``: it issues a control
        query whose response is routed BY the pump, so awaiting there would
        deadlock until the timeout. Schedule it with ``refresh_session_info``.
        """
        settings = await self.control_query("get_settings", {})
        payload: dict[str, Any] = {"running": False, "desktop_contract": DESKTOP_CONTRACT}
        if isinstance(settings, dict):
            model = settings.get("fusion") or settings.get("model")
            if model:
                payload["model"] = str(model)
            if settings.get("provider"):
                payload["provider"] = str(settings["provider"])
            if settings.get("permission_mode"):
                payload["approval_mode"] = settings["permission_mode"]
            effort = settings.get("reasoning_effort") or settings.get("effort")
            if effort:
                payload["reasoning_effort"] = str(effort)
        payload.update(extra)
        await self._broadcast("session.info", payload)

    def refresh_session_info(self) -> None:
        """Schedule a session.info republish (safe to call from the pump)."""
        task = asyncio.create_task(self._safe_publish_session_info())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _safe_publish_session_info(self) -> None:
        try:
            await self.publish_session_info()
        except Exception:  # noqa: BLE001 — a refresh must never kill a session
            logger.debug("desktop session %s: session.info refresh failed",
                         self.session_id, exc_info=True)

    # ── the pump: agent frames → gateway events ─────────────────────────────

    async def _pump(self) -> None:
        try:
            async for frame in self.agent.messages_from_agent():
                if not isinstance(frame, dict):
                    continue
                await self._route(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("desktop session %s pump died", self.session_id)
            await self._broadcast(
                "message.complete",
                {"text": "", "status": "error", "error": "backend session ended unexpectedly"},
            )

    async def _route(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")

        if kind == "control_response":
            body = frame.get("response") or {}
            rid = body.get("request_id")
            fut = self._pending_control.pop(str(rid), None)
            if fut and not fut.done():
                fut.set_result(body.get("response"))
            return

        if kind == "control_request":
            await self._route_ask(frame)
            return

        if kind == "system":
            subtype = frame.get("subtype")
            if subtype == "init":
                self.init_info = dict(frame)
                self.init_seen.set()
                await self._broadcast("session.info", _init_session_info(frame))
            return

        for type_, payload in translate_frame(frame):
            await self._broadcast(type_, payload)
        if kind == "result":
            # The renderer clears busy from message.complete; refresh the info
            # line too (permission mode may have flipped server-side).
            mode = frame.get("permission_mode")
            if mode:
                await self._broadcast("session.info", {"approval_mode": mode, "running": False})
            # …and republish the full line (model/provider/effort) — a turn can
            # change them server-side (fallback model, plan-mode flip).
            # Scheduled, never awaited: this control response routes through
            # THIS pump.
            self.refresh_session_info()
            # Turn end persisted the transcript — nudge sidebars to refresh.
            await self._broadcast("sessions.changed", {})

    async def _route_ask(self, frame: dict[str, Any]) -> None:
        rid = str(frame.get("request_id") or "")
        request = frame.get("request") or {}
        subtype = request.get("subtype")
        if not rid:
            return
        self._pending_asks[rid] = request
        self._last_ask_id = rid

        if subtype == "can_use_tool":
            await self._broadcast("approval.request", approval_request_payload(request))
            return
        # Unknown ask: deny rather than hang the agent behind an invisible
        # prompt (the desktop has no surface for it yet).
        logger.warning("desktop session %s: unsupported ask %s — denying", self.session_id, subtype)
        self._pending_asks.pop(rid, None)
        self._last_ask_id = None
        await self._reply_ask(rid, {"behavior": "deny", "message": f"unsupported prompt {subtype}"})

    async def _reply_ask(self, rid: str, response: Any) -> None:
        await self.agent.send_to_agent(
            {"type": "control_response", "response": {"request_id": rid, "response": response}}
        )

    # ── client-facing operations ────────────────────────────────────────────

    async def submit_prompt(self, text: str) -> None:
        await self._broadcast("message.start", {})
        await self.agent.send_to_agent(
            {"type": "user", "message": {"role": "user", "content": text}}
        )

    async def interrupt(self) -> None:
        await self.agent.send_to_agent(
            {
                "type": "control_request",
                "request_id": f"srv-{uuid.uuid4().hex[:12]}",
                "request": {"subtype": "interrupt"},
            }
        )

    async def control_query(self, subtype: str, params: dict[str, Any],
                            timeout: float = CONTROL_TIMEOUT_S) -> Any:
        rid = f"srv-{uuid.uuid4().hex[:12]}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_control[rid] = fut
        await self.agent.send_to_agent(
            {"type": "control_request", "request_id": rid,
             "request": {"subtype": subtype, **params}}
        )
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending_control.pop(rid, None)
            return None

    async def apply_model(self, model: str, provider: str | None,
                          allow_switch: bool = True) -> dict[str, Any]:
        """set_model with the picker's cross-provider retry.

        set_model refuses to point the live provider at another provider's
        model id (that needs set_provider's registry rebuild). The picker
        selects provider-then-model, so on ``provider_mismatch`` switch the
        provider first, then re-apply the model — mirroring the TUI client.
        """
        params: dict[str, Any] = {"model": model}
        if provider:
            params["provider"] = provider
        result = await self.control_query("set_model", params)
        if not isinstance(result, dict):
            return {"ok": True, "value": model, "indeterminate": True}
        if result.get("ok") is False:
            if result.get("provider_mismatch") and provider and allow_switch:
                switched = await self.control_query("set_provider", {"provider": provider})
                if isinstance(switched, dict) and switched.get("ok") is not False:
                    return await self.apply_model(model, None, allow_switch=False)
                err = (switched or {}).get("error") if isinstance(switched, dict) else None
                return {"ok": False, "error": err or f"could not switch to provider '{provider}'"}
            return {"ok": False, "error": result.get("error") or "could not set model"}
        return {"ok": True, "value": result.get("model") or model,
                "warning": result.get("warning")}

    async def _config_set_model(self, value: Any) -> dict[str, Any]:
        """Parse the renderer's model string and apply it.

        The composer sends ``"<model> --provider <p> --session"`` (the TUI's
        ``/model`` grammar); scope flags are informational here — this
        transport applies to the live session either way.
        """
        tokens = str(value or "").split()
        provider: str | None = None
        parts: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--provider":
                i += 1
                provider = tokens[i] if i < len(tokens) else None
            elif tok in ("--global", "--session", "--tui-session"):
                pass
            else:
                parts.append(tok)
            i += 1
        return await self.apply_model(" ".join(parts), provider)

    async def config_set(self, key: str, value: Any, persist: bool = False) -> dict[str, Any]:
        """Route a settings write to the matching agent control.

        Display-only prefs the backend doesn't own (skin, statusbar, …) have
        no control and succeed locally in the renderer, so an unknown key is a
        silent ok here rather than an error.
        """
        if key == "permission_mode":
            reply = await self.control_query("set_permission_mode",
                                             {"mode": value, "persist": persist})
            res = reply if isinstance(reply, dict) else {}
            return {
                "error": res.get("error"),
                "mode": res.get("mode"),
                "ok": res.get("ok") is not False,
                "persisted": res.get("persisted"),
            }
        if key == "model":
            result = await self._config_set_model(value)
            # Publish the REAL post-switch state (a cross-provider switch can
            # land on a different model than requested), so the chip, picker
            # and settings all reconcile to the truth.
            await self.publish_session_info()
            return result
        if key == "logoColor":
            reply = await self.control_query("set_logo_color", {"name": value})
            ok = isinstance(reply, dict) and reply.get("ok") is True
            return {"ok": True, "value": str(value)} if ok else {"ok": False}
        if key in ("effort", "reasoning"):
            await self.send_control("set_effort", {"effort": value})
        elif key == "provider":
            await self.send_control("set_provider", {"provider": value})
        elif key == "thinking":
            await self.send_control("set_thinking", {"action": value})
        else:
            return {"ok": True}
        # A fire-and-forget control still changed the session — republish so
        # the composer/picker reconcile instead of showing the old value.
        self.refresh_session_info()
        return {"ok": True}

    async def send_control(self, subtype: str, params: dict[str, Any]) -> None:
        """Fire-and-forget control request (no reply awaited)."""
        import uuid as _uuid

        await self.agent.send_to_agent(
            {"type": "control_request", "request_id": f"srv-{_uuid.uuid4().hex[:12]}",
             "request": {"subtype": subtype, **params}}
        )

    async def respond_approval(self, choice: str) -> dict[str, Any]:
        rid = self._last_ask_id
        request = self._pending_asks.pop(rid, None) if rid else None
        self._last_ask_id = None
        if not rid or request is None:
            return {"resolved": False}

        if choice == "deny":
            await self._reply_ask(rid, {"behavior": "deny", "message": "Denied by user"})
            return {"resolved": True}

        reply: dict[str, Any] = {
            "behavior": "allow",
            "updatedInput": request.get("input") or {},
        }
        if choice in ("session", "always"):
            wanted = "session" if choice == "session" else "always"
            chosen = [
                s for s in (request.get("suggestions") or [])
                if isinstance(s, dict) and _suggestion_scope(s) == wanted
            ]
            if chosen:
                reply["chosen_updates"] = chosen
        await self._reply_ask(rid, reply)
        return {"resolved": True}


def configured_providers_only(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only providers the user has actually configured.

    The registry knows ~30 providers, but the desktop picker should show just
    the ones the user set up: a key-taking provider with credentials present
    (an ``api_key`` in config, or a subscription/OAuth — the catalog folds both
    into ``authenticated``), plus whichever the live session is running on.
    Everything else is disabled by not appearing.

    Local, no-key providers (Ollama / vLLM / SGLang, ``auth_type == "none"``)
    report as ``authenticated`` because they need no key — but the user didn't
    *configure* them, so they're excluded unless they're the active provider.
    """
    return [
        p for p in providers
        if p.get("is_current")
        or (p.get("authenticated") and p.get("auth_type") == "api_key")
    ]


def _catalog_from_config() -> dict[str, Any]:
    """Configured model catalog from config alone — no live session required.

    Mirrors the agent-server's ``list_model_providers`` control, but reads the
    default provider + its configured model list straight from config so the
    desktop model picker populates on the welcome screen (before any session
    exists), and filters to only configured providers. Sync (config + registry
    access); call via ``to_thread``.
    """
    from src.providers.catalog import provider_catalog

    provider = None
    models: list[str] = []
    try:
        from src.config import get_default_provider, get_provider_config

        provider = get_default_provider()
        cfg = get_provider_config(provider) or {}
        default_model = cfg.get("default_model")
        if default_model:
            models = [default_model]
    except Exception:  # noqa: BLE001 — degrade to an unmarked catalog
        provider = None

    try:
        # current_models is left unset so the active provider shows its FULL
        # registry model list (e.g. every deepseek model), not just the one
        # `default_model` from config — the picker is where you switch models.
        providers = provider_catalog(
            current=provider,
            current_models=None,
            current_ready=bool(provider),
        )
    except Exception:  # noqa: BLE001
        logger.exception("desktop: catalog_from_config failed")
        providers = []
    return {
        "model": models[0] if models else None,
        "provider": provider,
        "providers": configured_providers_only(providers),
    }


def _clean(value: Any) -> str | None:
    """A non-empty trimmed string, or None (empty selections → base config)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _suggestion_scope(suggestion: dict[str, Any]) -> str:
    """Bucket a permission suggestion as a session or always/persistent grant."""
    destination = str(suggestion.get("destination") or "").lower()
    if destination == "session":
        return "session"
    return "always"


class GatewayConnection:
    """One accepted gateway socket: method table + session subscription."""

    def __init__(self, websocket: WebSocket, state: DesktopServeState) -> None:
        self.websocket = websocket
        self.state = state
        self.method_handlers = {
            "session.create": self.session_create,
            "session.resume": self.session_resume,
            "session.activate": self.session_activate,
            "session.close": self.session_close,
            "session.active_list": self.session_active_list,
            "session.list": self.session_active_list,
            "session.interrupt": self.session_interrupt,
            "session.clear": self.session_clear,
            "session.title": self.session_title,
            "session.usage": self.session_usage,
            "prompt.submit": self.prompt_submit,
            "approval.respond": self.approval_respond,
            "permission.cycle": self.permission_cycle,
            "model.options": self.model_options,
            "config.get": self.config_get,
            "config.set": self.config_set_rpc,
            "commands.catalog": self.commands_catalog,
            "complete.slash": self.complete_slash,
            "complete.path": self.complete_empty,
            "slash.exec": self.slash_exec,
            "command.dispatch": self.command_dispatch,
            "setup.status": self.setup_status,
        }

    async def on_open(self) -> None:
        for session in self.state.sessions.values():
            session.sockets.add(self.websocket)
        # change_events: sessions.changed is pushed after every turn, so the
        # renderer can demote its sidebar polling.
        await send_event(self.websocket, "gateway.ready",
                         {"app": "clawcodex", "change_events": True})

    async def on_close(self) -> None:
        for session in self.state.sessions.values():
            session.sockets.discard(self.websocket)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _session(self, params: dict[str, Any]) -> DesktopSession:
        session_id = str(params.get("session_id") or "")
        session = self.state.sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id or '<missing>'}")
        return session

    async def _create(self, cwd: str | None, resume: str | None,
                      params: dict[str, Any] | None = None) -> DesktopSession:
        manager = self.state.manager
        workspace = cwd or self.state.workspace
        if resume and resume in self.state.sessions:
            return self.state.sessions[resume]
        # A resumed stored session still gets a fresh runtime session: spawn,
        # then load the stored conversation via the `resume` control below.
        info = manager.create_session(cwd=workspace)
        session_id = info.id
        session = DesktopSession(session_id, self.state)
        session.sockets.add(self.websocket)
        self.state.sessions[session_id] = session
        # Honor the composer's provider/model/effort selection at spawn time, so
        # a session can use a working provider even when the config default is
        # broken (expired subscription, missing key). Empty values → the base
        # config's default provider.
        params = params or {}
        spawn = self.state.spawn_for(
            _clean(params.get("provider")),
            _clean(params.get("model")),
            _clean(params.get("reasoning_effort") or params.get("effort")),
        )
        try:
            await session.start(workspace, spawn=spawn)
        except Exception:
            self.state.sessions.pop(session_id, None)
            raise
        try:
            manager.mark_running(session_id)
        except Exception:  # noqa: BLE001 — index upkeep is best-effort
            pass
        # The composer unlocks once the agent announced itself.
        try:
            await asyncio.wait_for(session.init_seen.wait(), CONTROL_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("session %s: no system/init within %ss", session_id, CONTROL_TIMEOUT_S)
        if resume:
            reply = await session.control_query("resume", {"session_id": resume})
            if not isinstance(reply, dict) or reply.get("ok") is False:
                logger.warning("session %s: resume of %s refused: %r",
                               session_id, resume, reply)
        return session

    # ── methods ──────────────────────────────────────────────────────────────

    async def session_create(self, params: dict[str, Any]) -> dict[str, Any]:
        session = await self._create(params.get("cwd"), None, params)
        return {
            "session_id": session.session_id,
            "stored_session_id": session.session_id,
            "info": _init_session_info(session.init_info),
        }

    async def session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = str(params.get("session_id") or "") or None
        session = await self._create(params.get("cwd"), wanted, params)
        response: dict[str, Any] = {
            "session_id": session.session_id,
            "stored_session_id": wanted or session.session_id,
            "resumed": wanted or session.session_id,
            "message_count": 0,
            "messages": [],
            "info": _init_session_info(session.init_info),
        }
        omit = bool(params.get("omit_messages") or params.get("lazy"))
        if wanted and not omit:
            from src.server.desktop_sessions import load_session_messages

            stored = load_session_messages(self.state.saved_sessions_dir(), wanted)
            if stored is not None:
                response["messages"] = stored["messages"]
                response["message_count"] = stored["message_count"]
        elif wanted and omit:
            response["messages_omitted"] = True
        return response

    async def session_activate(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("session_id"):
            return await self.session_resume(params)
        return await self.session_create(params)

    async def session_close(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("session_id") or "")
        session = self.state.sessions.pop(session_id, None)
        if session is not None:
            await session.shutdown()
            try:
                await self.state.manager.stop_session(session_id)
            except Exception:  # noqa: BLE001 — index upkeep is best-effort
                pass
        return {"ok": True}

    async def session_active_list(self, _: dict[str, Any]) -> dict[str, Any]:
        sessions = []
        for session in self.state.sessions.values():
            info = _init_session_info(session.init_info)
            sessions.append({"session_id": session.session_id, **info})
        return {"sessions": sessions}

    async def session_interrupt(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._session(params).interrupt()
        return {"ok": True}

    async def session_clear(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._session(params).control_query("clear", {})
        return {"ok": (result or {}).get("ok", True) is not False}

    async def session_title(self, params: dict[str, Any]) -> dict[str, Any]:
        title = str(params.get("title") or params.get("name") or "").strip()
        result = await self._session(params).control_query("rename", {"name": title})
        name = (result or {}).get("name") if isinstance(result, dict) else None
        # Also stamp the saved-session file so the sidebar row updates without a
        # live turn (rename control persists the runtime session; the sidebar
        # reads the file).
        try:
            from src.server.desktop_sessions import update_session_meta

            update_session_meta(self.state.saved_sessions_dir(),
                                str(params.get("session_id") or ""), name=name or None)
        except Exception:  # noqa: BLE001 — best-effort file sync
            pass
        return {"title": name or title, "ok": True}

    async def session_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._session(params).control_query("get_context_usage", {})
        return result if isinstance(result, dict) else {}

    async def prompt_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params)
        text = str(params.get("text") or "")
        await session.submit_prompt(text)
        return {"ok": True}

    async def approval_respond(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._session(params).respond_approval(str(params.get("choice") or "deny"))

    async def permission_cycle(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._session(params).control_query("cycle_permission_mode", {})
        return result or {}

    async def model_options(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._first_session(params)
        if session is not None:
            result = await session.control_query("list_model_providers", {})
            if isinstance(result, dict) and result.get("providers"):
                # Only configured providers (+ the running one) — same rule as
                # the config-only path, so the picker is consistent whether or
                # not a session is live.
                return {
                    "model": result.get("fusion") or result.get("model"),
                    "provider": result.get("provider"),
                    "providers": configured_providers_only(result["providers"]),
                }
        # No live session yet (the welcome screen opens the picker before the
        # first prompt), or the session couldn't answer — enumerate the whole
        # registry directly from config. The catalog is session-independent;
        # it only needs the default provider + its model list.
        return await asyncio.to_thread(_catalog_from_config)

    def _first_session(self, params: dict[str, Any]) -> DesktopSession | None:
        session_id = str(params.get("session_id") or "")
        if session_id and session_id in self.state.sessions:
            return self.state.sessions[session_id]
        for session in self.state.sessions.values():
            return session
        return None

    async def config_get(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._first_session(params)
        if session is None:
            return {}
        return await session.control_query("get_settings", {}) or {}

    async def config_set_rpc(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session(params)
        return await session.config_set(
            str(params.get("key") or ""),
            params.get("value"),
            persist=bool(params.get("persist")),
        )

    async def _live_catalog(self, params: dict[str, Any]) -> dict[str, Any]:
        from src.server.desktop_commands import build_catalog

        session = self._first_session(params)
        skills: list = []
        workflows: list = []
        if session is not None:
            skills_reply = await session.control_query("list_skills", {})
            if isinstance(skills_reply, dict):
                skills = skills_reply.get("skills") or []
            wf_reply = await session.control_query("list_workflow_commands", {})
            if isinstance(wf_reply, dict):
                workflows = wf_reply.get("commands") or wf_reply.get("workflows") or []
        return build_catalog(skills=skills, workflows=workflows)

    async def commands_catalog(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._live_catalog(params)

    async def complete_slash(self, params: dict[str, Any]) -> dict[str, Any]:
        from src.server.desktop_commands import complete

        catalog = await self._live_catalog(params)
        return complete(str(params.get("text") or "/"), catalog)

    async def complete_empty(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"items": []}

    async def slash_exec(self, params: dict[str, Any]) -> dict[str, Any]:
        from src.server.desktop_slash import dispatch_slash

        session = self._session(params)
        raw = str(params.get("command") or "").strip()
        name, _, arg = raw.partition(" ")
        return await dispatch_slash(session.control_query, name, arg or None)

    async def command_dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        from src.server.desktop_slash import dispatch_slash

        session = self._session(params)
        return await dispatch_slash(
            session.control_query,
            str(params.get("name") or ""),
            params.get("arg"),
        )

    async def setup_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"provider_configured": True}


__all__ = ["DesktopSession", "GatewayConnection"]
