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


def _init_session_info(init: dict[str, Any]) -> dict[str, Any]:
    """system/init frame → the ``session.info`` payload the renderer reads."""
    payload: dict[str, Any] = {"running": False}
    cwd = init.get("cwd")
    if cwd:
        payload["cwd"] = cwd
    mode = init.get("permissionMode") or init.get("permission_mode")
    if mode:
        payload["approval_mode"] = mode
    model = init.get("model")
    if model:
        payload["model"] = model
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

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self, cwd: str) -> None:
        # spawn's third arg is a permission-mode override, NOT a resume id;
        # resuming a stored session is a post-init `resume` control request.
        self.agent = await self.state.spawn_agent(self.session_id, cwd, None)
        self.pump_task = asyncio.create_task(
            self._pump(), name=f"desktop-session-{self.session_id}"
        )

    async def shutdown(self) -> None:
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
            "prompt.submit": self.prompt_submit,
            "approval.respond": self.approval_respond,
            "permission.cycle": self.permission_cycle,
            "model.options": self.model_options,
            "config.get": self.config_get,
            "commands.catalog": self.commands_catalog,
            "complete.slash": self.complete_empty,
            "complete.path": self.complete_empty,
            "setup.status": self.setup_status,
        }

    async def on_open(self) -> None:
        for session in self.state.sessions.values():
            session.sockets.add(self.websocket)
        await send_event(self.websocket, "gateway.ready", {"app": "clawcodex"})

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

    async def _create(self, cwd: str | None, resume: str | None) -> DesktopSession:
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
        try:
            await session.start(workspace)
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
        session = await self._create(params.get("cwd"), None)
        return {
            "session_id": session.session_id,
            "stored_session_id": session.session_id,
            "info": _init_session_info(session.init_info),
        }

    async def session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = str(params.get("session_id") or "") or None
        session = await self._create(params.get("cwd"), wanted)
        return {
            "session_id": session.session_id,
            "stored_session_id": wanted or session.session_id,
            "resumed": wanted or session.session_id,
            # Transcript hydration ships with the sessions REST stage; the
            # agent's context IS restored (resume control), history paints
            # lazily once /api/sessions lands.
            "message_count": 0,
            "messages": [],
            "messages_omitted": True,
            "info": _init_session_info(session.init_info),
        }

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
        if session is None:
            return {"providers": []}
        result = await session.control_query("list_model_providers", {})
        if isinstance(result, dict) and result.get("providers"):
            return {
                "model": result.get("fusion") or result.get("model"),
                "provider": result.get("provider"),
                "providers": result.get("providers"),
            }
        settings = await session.control_query("get_settings", {}) or {}
        models = settings.get("available_models") or []
        provider = str(settings.get("provider") or "clawcodex")
        return {
            "model": settings.get("fusion") or settings.get("model"),
            "provider": provider,
            "providers": [
                {
                    "authenticated": True,
                    "is_current": True,
                    "models": models,
                    "name": provider,
                    "slug": provider,
                    "total_models": len(models),
                }
            ],
        }

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

    async def commands_catalog(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"commands": []}

    async def complete_empty(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"items": []}

    async def setup_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"provider_configured": True}


__all__ = ["DesktopSession", "GatewayConnection"]
