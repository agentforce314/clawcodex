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


# The agent's permission modes vs the desktop's approval vocabulary. The
# renderer's normalizeApprovalMode only knows manual|smart|off and silently
# coerces anything else to "manual", so sending the raw mode made a Full
# Access session render as "ask every time".
_APPROVAL_MODE_FOR = {
    "bypassPermissions": "off",   # Full Access — nothing to approve
    "auto": "smart",              # the classifier lane
    "default": "manual",
    "acceptEdits": "manual",      # edits auto-approve, everything else asks
    "plan": "manual",
}
_PERMISSION_MODE_FOR = {
    "off": "bypassPermissions",
    "smart": "auto",
    "manual": "default",
}


def approval_mode_for(permission_mode: Any) -> str | None:
    """Agent permission mode → the desktop's manual|smart|off vocabulary."""
    if not isinstance(permission_mode, str) or not permission_mode:
        return None
    return _APPROVAL_MODE_FOR.get(permission_mode, "manual")


def permission_mode_for(approval_mode: Any) -> str | None:
    """The desktop's approval mode → an agent permission mode."""
    if not isinstance(approval_mode, str):
        return None
    return _PERMISSION_MODE_FOR.get(approval_mode.strip().lower())


def _vision_ok(model: Any) -> bool:
    """Whether ``model`` accepts image input, for the composer's attach path.

    Unknown ids stay permissive, which is `supports_vision`'s own rule: a model
    absent from the table is assumed capable, because a real API error beats a
    wrong refusal. Only an exact table hit of False takes the control away.
    """
    if not isinstance(model, str) or not model:
        return True
    try:
        from src.models.capabilities import supports_vision

        return supports_vision(model)
    except Exception:  # noqa: BLE001 — a capability lookup must not break init
        logger.debug("vision capability lookup failed for %r", model, exc_info=True)
        return True


def _init_session_info(init: dict[str, Any]) -> dict[str, Any]:
    """system/init frame → the ``session.info`` payload the renderer reads."""
    payload: dict[str, Any] = {"running": False, "desktop_contract": DESKTOP_CONTRACT}
    cwd = init.get("cwd")
    if cwd:
        payload["cwd"] = cwd
    mode = init.get("permissionMode") or init.get("permission_mode")
    if mode:
        payload["approval_mode"] = approval_mode_for(mode)
    model = init.get("model")
    if model:
        payload["model"] = model
        # The composer hides its attach control when this is False, so a
        # screenshot is never pasted into a model that would 400 on it.
        payload["vision"] = _vision_ok(model)
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
        # AskUserQuestion rides its own slot rather than sharing _last_ask_id:
        # an approval click and a question submit are different replies, and a
        # single slot would let either resolve the other's request.
        self._pending_question: str | None = None
        # Set only once a client has said it renders questions (see _create).
        self.asks_questions = False
        # Whether this session already has a name worth keeping. True for a
        # resumed session (it has the user's own title) and after any explicit
        # rename, so auto-titling only ever fills in a blank.
        self.titled = False
        self.sockets: set[WebSocket] = set()
        # Scheduled session.info refreshes; held so they aren't GC'd mid-flight.
        self._background: set[asyncio.Task] = set()
        # tool_use_id -> tool name, so a tool_result can label its row.
        self._tool_names: dict[str, str] = {}

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
                # Republished on every model switch, so the attach control
                # comes and goes with the model that would have to read it.
                payload["vision"] = _vision_ok(str(model))
            if settings.get("provider"):
                payload["provider"] = str(settings["provider"])
            if settings.get("permission_mode"):
                payload["approval_mode"] = approval_mode_for(settings["permission_mode"])
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

        for type_, payload in translate_frame(frame, self._tool_names):
            await self._broadcast(type_, payload)
        if kind == "result":
            # The renderer clears busy from message.complete; refresh the info
            # line too (permission mode may have flipped server-side).
            mode = frame.get("permission_mode")
            if mode:
                await self._broadcast(
                    "session.info",
                    {"approval_mode": approval_mode_for(mode), "running": False},
                )
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

        if subtype == "can_use_tool":
            # Claimed by the approval lane only; a question must not land in
            # this slot or an approval click would answer it.
            self._last_ask_id = rid
            await self._broadcast("approval.request", approval_request_payload(request))
            return

        if subtype == "ask_user_question" and self.asks_questions:
            payload = question_request_payload(rid, request)
            # A malformed question set has nothing to render; falling through
            # to the decline below is better than an empty modal the user
            # cannot dismiss.
            if payload is not None:
                self._pending_question = rid
                await self._broadcast("question.request", payload)
                return

        # Unknown ask: deny rather than hang the agent behind an invisible
        # prompt (the desktop has no surface for it yet).
        logger.warning("desktop session %s: unsupported ask %s — denying", self.session_id, subtype)
        self._pending_asks.pop(rid, None)
        await self._reply_ask(rid, {"behavior": "deny", "message": f"unsupported prompt {subtype}"})

    async def _reply_ask(self, rid: str, response: Any) -> None:
        await self.agent.send_to_agent(
            {"type": "control_response", "response": {"request_id": rid, "response": response}}
        )

    # ── client-facing operations ────────────────────────────────────────────

    def schedule_auto_title(self, text: str) -> None:
        """Fire-and-forget ``auto_title``; held so it is not GC'd mid-flight."""
        if self.titled:
            return

        task = asyncio.create_task(self._safe_auto_title(text))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _safe_auto_title(self, text: str) -> None:
        try:
            await self.auto_title(text)
        except Exception:  # noqa: BLE001 — a title is never worth killing a session
            logger.debug("desktop session %s: auto-title failed",
                         self.session_id, exc_info=True)

    async def auto_title(self, text: str) -> str | None:
        """Name an untitled session after its first prompt.

        Uses the heuristic, not ``generate_llm_title``: that one is hardcoded
        to Anthropic and a fixed Haiku model, so on a session running any other
        provider it would bill an account the user did not choose to use here —
        and silently return None wherever that provider is not configured.
        A first line with the throat-clearing stripped is a good title and
        costs nothing.
        """
        if self.titled:
            return None

        from src.services.session_title import auto_title_from_message

        title = auto_title_from_message(text)
        if not title or title == "Untitled session":
            return None

        # Set before the round-trip: a second prompt arriving while this one is
        # in flight must not start a second rename of the same session.
        self.titled = True
        await self.control_query("rename", {"name": title})
        try:
            from src.server.desktop_sessions import update_session_meta

            update_session_meta(self.state.saved_sessions_dir(), self.session_id, name=title)
        except Exception:  # noqa: BLE001 — best-effort file sync
            logger.debug("auto_title: could not stamp the saved session", exc_info=True)
        await self._broadcast("session.title", {"title": title})
        await self._broadcast("sessions.changed", {})
        return title

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
        if key == "approvals.mode":
            # Safety panel / `/approvals`: manual|smart|off → a permission mode.
            mode = permission_mode_for(value)
            if mode is None:
                return {"ok": False, "error": f"unknown approvals mode {value!r}"}
            reply = await self.control_query("set_permission_mode",
                                             {"mode": mode, "persist": True})
            res = reply if isinstance(reply, dict) else {}
            applied = approval_mode_for(res.get("mode") or mode)
            await self.publish_session_info()
            return {"ok": res.get("ok") is not False, "value": applied,
                    "error": res.get("error")}
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

    async def respond_approval(
        self, choice: str, *, updates: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
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
        if updates:
            # Explicit updates come from a dialog that composed them itself —
            # the plan review's "auto-accept edits" is a setMode the ask
            # carries no suggestion for, so it cannot be picked from the list
            # below.
            reply["chosen_updates"] = updates
        elif choice in ("session", "always"):
            wanted = "session" if choice == "session" else "always"
            chosen = [
                s for s in (request.get("suggestions") or [])
                if isinstance(s, dict) and _suggestion_scope(s) == wanted
            ]
            if chosen:
                reply["chosen_updates"] = chosen
        await self._reply_ask(rid, reply)
        return {"resolved": True}

    async def respond_question(
        self, action: str, answers: dict[str, str]
    ) -> dict[str, Any]:
        """Answer (or decline) the parked AskUserQuestion.

        The agent reads anything that is not ``action == "submit"`` as a
        decline, and filters the answers down to the questions it actually
        asked — so an unknown key here is dropped there, not an error.
        """
        rid = self._pending_question
        self._pending_question = None
        if not rid or self._pending_asks.pop(rid, None) is None:
            # Already answered, timed out, or never ours. Saying so lets the
            # client drop a composer that is addressing a question the agent
            # has since stopped waiting on.
            return {"resolved": False}

        if action != "submit":
            await self._reply_ask(rid, {"action": "decline"})
            return {"resolved": True}

        await self._reply_ask(
            rid,
            {
                "action": "submit",
                "answers": {
                    str(k): str(v)
                    for k, v in answers.items()
                    if isinstance(k, str) and isinstance(v, str)
                },
            },
        )
        return {"resolved": True}


# cwd -> (expires_at, files, truncated). A mention is typed one keystroke at a
# time and `rg --files` runs per call; the TTL is short enough that a file
# created while typing appears in the same sitting.
_FILE_CACHE_TTL_S = 5.0
_file_cache: dict[str, tuple[float, list[str], bool]] = {}


# Pruned by the fallback walker below. Not a .gitignore substitute — just the
# directories that are noise in every repo and would otherwise dominate a
# mention menu.
_WALK_SKIP = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next",
    ".turbo", "target", ".idea", ".tox",
}
_WALK_MAX_FILES = 5000


def _walk_workspace_files(cwd: str) -> tuple[list[str], bool]:
    """A file list without ripgrep, for machines that do not have it.

    Degraded on purpose and knowingly: this does NOT read .gitignore, so it can
    surface files ``rg --files`` would hide. That is a worse list than
    ripgrep's — but a mention picker that works everywhere beats one that is
    silently empty wherever ``rg`` is not installed, and the paths it offers
    are real files either way.

    BREADTH-first, which matters more than it looks. Depth-first spends the cap
    on whichever subtree sorts first: measured on this repo it produced 5000
    files that stopped at ``install.ps1`` and contained not one
    ``ui-web/src/...`` path, so the obvious query returned nothing. Level order
    gives every directory its shallow files before any subtree goes deep, so
    truncation drops the deepest paths — the ones least likely to be typed —
    rather than everything after the letter I.
    """
    import os
    from collections import deque

    found: list[str] = []
    queue: deque[str] = deque([cwd])

    while queue:
        current = queue.popleft()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            # One unreadable directory should not end the walk; the rest of
            # the tree is still a useful list.
            continue

        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue

            if directory:
                if entry.name not in _WALK_SKIP:
                    queue.append(entry.path)

                continue

            rel = os.path.relpath(entry.path, cwd)
            found.append(rel.replace(os.sep, "/") if os.sep != "/" else rel)

            if len(found) >= _WALK_MAX_FILES:
                return found, True

    return found, False


def _workspace_files_cached(cwd: str) -> tuple[list[str], bool]:
    """``list_workspace_files`` behind a short per-directory TTL.

    Falls back to a plain walk when ripgrep is missing. Any OTHER failure
    propagates: an unreadable directory is a real answer the caller reports,
    not something to paper over with a partial list.
    """
    import time

    from src.services.workspace_search import list_workspace_files

    now = time.monotonic()
    hit = _file_cache.get(cwd)
    if hit is not None and hit[0] > now:
        return hit[1], hit[2]

    try:
        files, truncated = list_workspace_files(cwd)
    except Exception as exc:  # noqa: BLE001 — narrowed by the message check
        if "ripgrep" not in str(exc).lower():
            raise
        logger.info("fs.search_files: ripgrep unavailable, walking %s instead", cwd)
        files, truncated = _walk_workspace_files(cwd)

    _file_cache[cwd] = (now + _FILE_CACHE_TTL_S, files, truncated)
    return files, truncated


def _wants_questions(params: dict[str, Any]) -> bool:
    """Whether this client declared it can render AskUserQuestion."""
    capabilities = params.get("capabilities")
    if isinstance(capabilities, dict):
        return capabilities.get("ask_user_question") is True
    if isinstance(capabilities, list):
        return "ask_user_question" in capabilities
    return False


def question_request_payload(
    request_id: str, request: dict[str, Any]
) -> dict[str, Any] | None:
    """Shape one ``ask_user_question`` for the client, or None if unrenderable.

    Question text is the agent's answer KEY (that is the contract the tool
    filters on), so it is carried through verbatim and echoed back rather than
    re-derived client-side from a label or an index.
    """
    questions: list[dict[str, Any]] = []
    for item in request.get("questions") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("question")
        if not isinstance(text, str) or not text.strip():
            continue
        options: list[dict[str, str]] = []
        for option in item.get("options") or []:
            if isinstance(option, dict) and isinstance(option.get("label"), str):
                entry = {"label": option["label"]}
                description = option.get("description")
                if isinstance(description, str) and description:
                    entry["description"] = description
                options.append(entry)
        question: dict[str, Any] = {"question": text, "options": options}
        header = item.get("header")
        if isinstance(header, str) and header:
            question["header"] = header
        if item.get("multiSelect") is True:
            question["multi_select"] = True
        questions.append(question)

    if not questions:
        return None
    return {"request_id": request_id, "questions": questions}


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
            "session.rewind": self.session_rewind,
            "session.clear": self.session_clear,
            "session.title": self.session_title,
            "session.usage": self.session_usage,
            "projects.tree": self.projects_tree,
            "prompt.submit": self.prompt_submit,
            "approval.respond": self.approval_respond,
            "question.respond": self.question_respond,
            "permission.cycle": self.permission_cycle,
            "model.options": self.model_options,
            "effort.options": self.effort_options,
            "config.get": self.config_get,
            "config.set": self.config_set_rpc,
            "commands.catalog": self.commands_catalog,
            "complete.slash": self.complete_slash,
            "complete.path": self.complete_empty,
            "fs.list_directory": self.fs_list_directory,
            "fs.search_files": self.fs_search_files,
            "image.attach": self.image_attach,
            "plan.get": self.plan_get,
            "provider.list": self.provider_list,
            "provider.save_key": self.provider_save_key,
            "provider.disconnect": self.provider_disconnect,
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
        # Capability negotiation, opt-in per client. Without it the agent
        # substitutes the non-interactive answer for AskUserQuestion, because
        # a client that ignored the question would park the session's worker
        # thread until the ask timeout. Clients that render questions ask for
        # the real thing here; the ones that don't are left exactly as before.
        if _wants_questions(params):
            reply = await session.control_query("set_ask_user_interactive", {"enabled": True})
            if isinstance(reply, dict) and reply.get("ok") is not False:
                session.asks_questions = True
            else:
                logger.warning(
                    "session %s: interactive questions refused: %r", session_id, reply
                )
        if resume:
            # A stored session brings its own name; auto-titling would rename
            # someone's saved conversation after whatever they type next.
            session.titled = True
            reply = await session.control_query("resume", {"session_id": resume})
            if not isinstance(reply, dict) or reply.get("ok") is False:
                logger.warning("session %s: resume of %s refused: %r",
                               session_id, resume, reply)
        return session

    # ── methods ──────────────────────────────────────────────────────────────

    async def projects_tree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Group saved + live sessions into the sidebar's project/repo/worktree
        tree. Runs the git probes off the event loop (they shell out)."""
        import asyncio as _asyncio

        preview_limit = int(params.get("preview_limit") or 3)
        return await _asyncio.to_thread(self._build_projects_tree, preview_limit)

    def _build_projects_tree(self, preview_limit: int) -> dict[str, Any]:
        from src.server.desktop_projects import build_project_tree
        from src.server.desktop_sessions import list_session_rows
        from src.utils.git import get_repo_root, list_worktrees

        # All saved rows (limit=0 → no cap; the sidebar wants the full tree).
        rows = list_session_rows(self.state.saved_sessions_dir(), limit=0)["sessions"]
        # Merge live sessions the saved index hasn't captured yet, so a
        # just-created session still places into its repo immediately.
        seen = {r.get("id") for r in rows}
        active_cwd: str | None = None
        for session in self.state.sessions.values():
            info = getattr(session, "init_info", None) or {}
            cwd = info.get("cwd") if isinstance(info, dict) else None
            if cwd:
                active_cwd = cwd  # last live session's cwd = the active one
            sid = getattr(session, "session_id", None)
            if sid and sid not in seen:
                rows.append({
                    "id": sid, "title": "", "preview": "", "source": "desktop",
                    "started_at": None, "last_active": None, "message_count": 0,
                    "model": None, "cwd": cwd, "is_active": True, "profile": "default",
                })
                seen.add(sid)

        # Per-cwd / per-repo memoized git probes: a tree can hold many sessions
        # in the same repo, so probe each distinct path once.
        repo_cache: dict[str, str | None] = {}
        wt_cache: dict[str, list[str]] = {}

        def worktrees_of(repo_root: str) -> list[str]:
            # ``git worktree list`` is repo-global and main-first from ANY
            # worktree in the repo, so this is correct whether keyed by the
            # main root or a linked-worktree path.
            if repo_root not in wt_cache:
                wt_cache[repo_root] = [w.path for w in list_worktrees(repo_root) if w.path]
            return wt_cache[repo_root]

        def repo_root_of(cwd: str) -> str | None:
            # ``rev-parse --show-toplevel`` inside a LINKED worktree returns the
            # worktree's own toplevel, not the repo — which would spray each
            # worktree into its own project. Resolve the MAIN worktree root
            # (the first ``git worktree list`` entry) so linked worktrees group
            # as lanes under their repo, matching the renderer's tree.
            if cwd not in repo_cache:
                top = get_repo_root(cwd)
                if top:
                    worktrees = worktrees_of(top)
                    repo_cache[cwd] = worktrees[0] if worktrees else top
                else:
                    repo_cache[cwd] = None
            return repo_cache[cwd]

        return build_project_tree(
            rows,
            repo_root_of=repo_root_of,
            worktrees_of=worktrees_of,
            active_cwd=active_cwd,
            preview_limit=preview_limit,
        )

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
                if stored.get("title"):
                    response["title"] = stored["title"]
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

    async def session_rewind(self, params: dict[str, Any]) -> dict[str, Any]:
        """Drop the last N prompt-turns from the conversation (the CLI's /rewind).

        Reports ``removed`` so the client can trim its own transcript by the
        same amount instead of guessing. The agent refuses this during an
        active turn — it mutates the conversation the worker is reading — and
        that refusal is passed through rather than smoothed over: a retry that
        silently did nothing mid-turn would look like the model ignoring it.
        """
        try:
            turns = max(1, int(params.get("turns") or 1))
        except (TypeError, ValueError):
            turns = 1
        result = await self._session(params).control_query("rewind", {"turns": turns})
        if not isinstance(result, dict):
            return {"ok": False, "error": "no response from the session"}
        return {
            "count": result.get("count", 0),
            "error": result.get("error", ""),
            "ok": result.get("ok") is not False,
            "removed": result.get("removed", 0),
        }

    async def session_clear(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._session(params).control_query("clear", {})
        return {"ok": (result or {}).get("ok", True) is not False}

    async def session_title(self, params: dict[str, Any]) -> dict[str, Any]:
        title = str(params.get("title") or params.get("name") or "").strip()
        session = self._session(params)
        session.titled = True
        result = await session.control_query("rename", {"name": title})
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
        # Scheduled, never awaited. Titling is a control round-trip, and this
        # RPC's ack is what unlocks the composer — a session slow to answer the
        # rename would otherwise hold the prompt ack for the control timeout.
        session.schedule_auto_title(text)
        return {"ok": True}

    async def approval_respond(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("updates")
        updates = [u for u in raw if isinstance(u, dict)] if isinstance(raw, list) else None
        return await self._session(params).respond_approval(
            str(params.get("choice") or "deny"), updates=updates
        )

    async def question_respond(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("answers")
        answers = raw if isinstance(raw, dict) else {}
        return await self._session(params).respond_question(
            str(params.get("action") or "decline"), answers
        )

    async def permission_cycle(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._session(params).control_query("cycle_permission_mode", {})
        return result or {}

    async def provider_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Every provider ClawCodex knows, configured or not.

        Deliberately NOT ``model.options``, which filters to the configured
        ones: that is right for a picker (you cannot run on a provider you have
        no key for) and wrong for settings, where the whole point is adding a
        key to one you have not set up yet.

        Carries no secrets — the catalog reports ``authenticated`` and the
        environment variable a key could come from, never the key itself.
        """
        session = self._first_session(params)
        if session is not None:
            result = await session.control_query("list_model_providers", {})
            if isinstance(result, dict) and result.get("providers"):
                return {
                    "current": result.get("provider") or "",
                    "providers": result["providers"],
                }
        catalog = await asyncio.to_thread(_catalog_from_config)
        return {
            "current": catalog.get("provider") or "",
            "providers": catalog.get("providers") or [],
        }

    async def provider_save_key(self, params: dict[str, Any]) -> dict[str, Any]:
        """Store an API key for a provider, where `clawcodex login` stores it.

        Needs a live session because the control lives on the agent: the reply
        is the provider's refreshed catalog row, so the client can show the new
        state without a second round trip — and without ever being handed the
        key back.
        """
        session = self._first_session(params)
        if session is None:
            return {"ok": False, "error": "start a session before changing providers"}
        result = await session.control_query(
            "save_provider_key",
            {"slug": _clean(params.get("slug")), "api_key": str(params.get("api_key") or "")},
        )
        if not isinstance(result, dict):
            return {"ok": False, "error": "no response from the session"}
        return {
            "error": str(result.get("error") or ""),
            "ok": result.get("ok") is not False,
            "provider": result.get("provider"),
        }

    async def provider_disconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        """Clear a provider's stored credentials.

        The agent refuses the ACTIVE provider — this session holds a
        constructed provider instance, and pulling its key would leave the next
        turn firing at an endpoint it can no longer authenticate against — and
        reports honestly when the key lives in the real shell environment,
        where nothing here can remove it. Both are passed through rather than
        smoothed into a generic failure.
        """
        session = self._first_session(params)
        if session is None:
            return {"ok": False, "error": "start a session before changing providers"}
        result = await session.control_query(
            "disconnect_provider", {"slug": _clean(params.get("slug"))}
        )
        if not isinstance(result, dict):
            return {"ok": False, "error": "no response from the session"}
        return {
            "error": str(result.get("error") or ""),
            "message": str(result.get("message") or ""),
            "ok": result.get("ok") is not False,
            "provider": result.get("provider"),
        }

    async def plan_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """The session's current plan text.

        ExitPlanMode is a V2 tool: the plan lives in the session plan FILE, not
        in the tool input, so an ``approval.request`` for it carries no plan to
        show. The client fetches it when that ask arrives.

        Fetched rather than folded into the ask because the ask is routed from
        the pump, and a control query issued there would wait on a response
        that same pump has to deliver.
        """
        result = await self._session(params).control_query("plan", {})
        if not isinstance(result, dict) or result.get("ok") is False:
            return {"plan": "", "mode": ""}
        plan = result.get("plan")
        return {
            "mode": str(result.get("mode") or ""),
            "path": str(result.get("plan_file_path") or ""),
            "plan": plan if isinstance(plan, str) else "",
        }

    async def image_attach(self, params: dict[str, Any]) -> dict[str, Any]:
        """Attach a pasted or dropped image to the next prompt.

        The agent's ``attach_image`` control reads a path off the machine it
        runs on, which a browser cannot produce. So the bytes come over the
        socket and land in a temp file the control then reads — the agent side
        stays exactly as the TUI uses it.

        ``placeholder: True`` is deliberate. It tells the agent this client
        renders an ``[Image #N]`` chip in the prompt text, which makes the chip
        authoritative: an image whose chip is gone at submit is dropped. That
        is how deleting the text un-attaches the image, and it is the same
        contract the TUI composer relies on.
        """
        import base64
        import os
        import tempfile

        raw = params.get("data")
        if not isinstance(raw, str) or not raw:
            return {"error": "no image data"}

        # Accept a data: URL as well as bare base64 — that is what a browser
        # paste hands you, and stripping it here beats making every caller.
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")

        try:
            blob = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            return {"error": "image data was not valid base64"}

        if not blob:
            return {"error": "image data was empty"}

        session = self._session(params)
        model = str((session.init_info or {}).get("model") or "")
        if not _vision_ok(model):
            # Sending it anyway is a hard 400 from the provider that kills the
            # turn; saying which model cannot read it is far more use than
            # "Failed to deserialize the JSON body".
            return {"error": f"{model} cannot read images — switch to a vision model first."}

        name = str(params.get("name") or "pasted-image.png")
        suffix = os.path.splitext(name)[1] or ".png"
        handle, path = tempfile.mkstemp(prefix="clawcodex-paste-", suffix=suffix)
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(blob)
            result = await session.control_query(
                "attach_image", {"path": path, "placeholder": True}
            )
        finally:
            # The control reads the file into memory, so the copy on disk is
            # dead the moment it returns — including when it failed.
            try:
                os.unlink(path)
            except OSError:
                logger.debug("image.attach: could not remove %s", path)

        if not isinstance(result, dict):
            return {"error": "no response from the session"}
        if result.get("attached") is not True:
            return {"error": str(result.get("error") or "the session refused the image")}
        return {
            "attached": True,
            "id": result.get("id"),
            "name": result.get("name") or name,
        }

    async def fs_search_files(self, params: dict[str, Any]) -> dict[str, Any]:
        """Workspace files matching ``query``, for the composer's @ mentions.

        Ranked server-side by ``workspace_search.filter_files`` rather than
        shipping the list for the client to score: that is the same fuzzy
        scorer the TUI's quick-open and the history search use, and a second
        implementation in TypeScript would rank the same query differently on
        two surfaces of the same app.

        The file list is cached briefly per directory because ``rg --files``
        runs per call and a mention is typed one keystroke at a time. The TTL
        is short enough that a file created while typing shows up in the same
        sitting.
        """
        import asyncio as _asyncio

        from src.services.workspace_search import filter_files

        cwd = _clean(params.get("cwd")) or self.state.workspace
        query = str(params.get("query") or "")
        try:
            limit = max(1, min(int(params.get("limit") or 20), 100))
        except (TypeError, ValueError):
            limit = 20

        try:
            files, truncated = await _asyncio.to_thread(_workspace_files_cached, cwd)
        except Exception as exc:  # noqa: BLE001 — a picker must not kill the socket
            logger.warning("fs.search_files: listing %s failed: %s", cwd, exc)
            # Reported rather than returned as an empty list: "no files here"
            # and "I could not look" are different answers.
            return {"error": str(exc), "files": [], "truncated": False}

        return {
            "files": filter_files(files, query, limit=limit),
            "total": len(files),
            "truncated": truncated,
        }

    async def effort_options(self, params: dict[str, Any]) -> dict[str, Any]:
        """The effort levels the given (or running) model will actually accept.

        Queried per model rather than ridden along on ``model.options``: the
        ladder is a property of the MODEL and some providers enumerate hundreds
        of them, so answering for all of them upfront would bloat every picker
        open to serve one row.

        ``supported`` False means the model takes no effort parameter at all —
        the client is expected to show no control rather than a list it cannot
        apply. With no live session there is nothing to ask, and saying
        ``supported: false`` is the honest answer: a level chosen now has
        nowhere to go.
        """
        session = self._first_session(params)
        if session is None:
            return {"supported": False, "levels": [], "current": ""}
        result = await session.control_query(
            "effort_options",
            {"provider": _clean(params.get("provider")), "model": _clean(params.get("model"))},
        )
        if not isinstance(result, dict) or result.get("ok") is False:
            return {"supported": False, "levels": [], "current": ""}
        return {
            "current": result.get("current") or "",
            "levels": result.get("levels") or [],
            "model": result.get("model") or "",
            "provider": result.get("provider") or "",
            "supported": result.get("supported") is True,
        }

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
        catalog = await asyncio.to_thread(_catalog_from_config)
        # …but what the FIRST session will actually run on is what `serve` was
        # launched with, not the config default. Without this the composer
        # advertises one model on the welcome screen and silently switches to
        # another the moment a session starts.
        config = self.state.agent_config
        model = _clean(getattr(config, "model", None)) if config is not None else ""
        provider = _clean(getattr(config, "provider_name", None)) if config is not None else ""
        if model:
            catalog["model"] = model
        if provider:
            catalog["provider"] = provider
        return catalog

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
        settings = await session.control_query("get_settings", {}) or {}
        # The Safety panel + /approvals read this single key and expect
        # {value}; without it they always resolved to "manual" (the fallback)
        # and misreported a Full Access session as asking every time.
        if str(params.get("key") or "") == "approvals.mode":
            return {"value": approval_mode_for(settings.get("permission_mode")) or "manual"}
        return settings

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

    async def fs_list_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        """One directory level for the workspace picker.

        Runs off the event loop: ``scandir`` on a cold or network-mounted
        directory blocks, and this socket also carries the turn's stream.
        """
        import asyncio as _asyncio

        from src.server.desktop_fs import list_directory

        path = params.get("path")
        return await _asyncio.to_thread(
            list_directory, str(path) if path else None
        )

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
