"""Agent server — the real :data:`SpawnAgent` for :class:`DirectConnectServer`.

This is the load-bearing piece of the "TS Ink TUI as a client of the Python
backend" redesign (see ``my-docs/tui-interface-redesign/``). It drives the
canonical agent loop (:mod:`src.query.query`, via the
:func:`src.query.agent_loop_compat.run_query_as_agent_loop` adapter) for one
Direct Connect session and bridges it to the NDJSON wire protocol that the
Direct Connect client (:mod:`src.server.direct_connect_manager`, a port of
``typescript/src/server/directConnectManager.ts``) already speaks. Because the
TS client and this server agree on that protocol, the existing Ink TUI can
``claude open cc://…`` straight into this server with no TS changes.

Wire protocol
-------------
server → client (``messages_from_agent``)::

    {type:'system', subtype:'init', model, tools:[{name,description,input_schema}],
     permission_mode, protocol_version, session_id, cwd}     # once, on connect
    {type:'stream_event', event:{...text_delta...}}           # live token deltas
    {type:'assistant', uuid, session_id, message:{role,content}}
    {type:'user',      uuid, session_id, message:{role,content:[tool_result…]}}
    {type:'control_request', request_id, request:{subtype:'can_use_tool', …}}
    {type:'control_response', response:{subtype, request_id, response}}  # to client pulls
    {type:'result', subtype:'success'|'error'|'cancelled', usage, num_turns, …}

client → server (``send_to_agent``)::

    {type:'user', message:{role:'user', content:<str|blocks>}}            # a prompt
    {type:'control_response', response:{request_id, response:{behavior,…}}} # perm reply
    {type:'control_request', request:{subtype:'interrupt'}}               # cancel turn
    {type:'control_request', request:{subtype:'set_permission_mode', mode}}
    {type:'control_request', request:{subtype:'set_model', model}}
    {type:'control_request', request_id, request:{subtype:'get_settings'|'get_context_usage'}}

Concurrency model
-----------------
The canonical permission handler is a **blocking, synchronous** callable
(``PermissionAskHandler``). To turn a permission ask into a wire round-trip we
must block *something* until the client answers — but never the asyncio loop
that pumps the WebSocket (that would deadlock: the reply can't arrive). So we
run the whole ``query()`` turn in a **worker thread** (the same pattern the
Textual TUI's ``AgentBridge`` uses), and the permission handler blocks that
thread on a :class:`threading.Event`. Outbound messages are handed to the main
loop with ``loop.call_soon_threadsafe`` (asyncio.Queue is not thread-safe).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue as _queue
import threading
import time
import uuid as _uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.server.server import AgentHandle
from src.utils.abort_controller import AbortController, AbortError

logger = logging.getLogger(__name__)

#: Wire-protocol version. Emitted in ``system/init`` so client and server can
#: refuse a mismatched major. Bump the major on any breaking shape change.
PROTOCOL_VERSION = "0.1.0"

#: Default ceiling for a permission round-trip. A disconnected/dead client must
#: not wedge a tool forever, so we default-deny after this (proposal §7).
DEFAULT_PERMISSION_TIMEOUT_S = 300.0

_SHUTDOWN = object()  # sentinel pushed onto the worker inbox to stop it


@dataclass
class AgentServerConfig:
    """Static configuration for an agent-server (one per process/server)."""

    provider_name: str | None = None
    model: str | None = None
    permission_mode: str = "default"
    max_turns: int = 20
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    permission_timeout_s: float = DEFAULT_PERMISSION_TIMEOUT_S


@dataclass
class _Pending:
    event: threading.Event
    reply: dict[str, Any] | None = None


@dataclass
class _AgentSession:
    """Per-WS-connection agent state, bridging the worker thread ↔ asyncio loop."""

    session_id: str
    cwd: str
    config: AgentServerConfig
    loop: asyncio.AbstractEventLoop
    out_queue: asyncio.Queue[dict | None]

    # Built lazily/eagerly at spawn; see ``_build_runtime``.
    provider: Any = None
    provider_name: str = ""
    tool_registry: Any = None
    tool_context: Any = None
    session: Any = None
    system_prompt: Any = "You are a helpful assistant."
    _base_system_prompt: Any = None  # system prompt before the /plan section is composed in
    _language: Any = None  # preferred response language (the original's LanguagePicker, §6)
    init_error: str | None = None
    _session_name: str | None = None  # user-set label (/rename) shown in /resume
    _mcp_runtime: Any = None  # McpRuntime (connected MCP servers) when configured
    _effort: str | None = None  # /effort reasoning level, injected via extra_body when set
    _knowledge: Any = None  # KnowledgeGraph (lazy-loaded), populated at each turn end
    _knowledge_enabled: bool = True  # the original's knowledgeGraphEnabled (default on)
    _knowledge_semantic: bool = False  # opt-in model-based extraction (vs heuristic)
    _bgtasks: Any = None  # BackgroundTasks registry (lazy), the original's Ctrl+B runs

    # Worker + cross-thread coordination.
    _inbox: _queue.Queue = field(default_factory=_queue.Queue)
    _worker: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _current_abort: AbortController | None = None

    # ─── outbound helpers (worker thread → main loop) ──────────────────────

    def _emit(self, msg: dict) -> None:
        """Thread-safe enqueue of one outbound SDK message.

        Every message is passed through ``_json_safe`` so a stray
        non-serializable value can never make the server's ``json.dumps`` in
        the WS pump raise and silently kill the outbound stream.
        """
        try:
            self.loop.call_soon_threadsafe(self.out_queue.put_nowait, _json_safe(msg))
        except RuntimeError:
            # Loop closed (server shutting down) — drop.
            pass

    def _close_stream(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.out_queue.put_nowait, None)
        except RuntimeError:
            pass

    # ─── init ──────────────────────────────────────────────────────────────

    def emit_init(self) -> None:
        """Emit ``system/init`` — the first message the client sees on connect."""
        tools = _tool_schemas(self.tool_registry)
        self._emit({
            "type": "system",
            "subtype": "init",
            "session_id": self.session_id,
            "protocol_version": PROTOCOL_VERSION,
            "model": getattr(self.provider, "model", self.config.model),
            "provider": self.provider_name,
            "cwd": self.cwd,
            "tools": tools,
            "permission_mode": _current_mode(self.tool_context, self.config.permission_mode),
            "apiKeySource": "config",
        })
        if self.init_error is not None:
            self._emit(_system_message(self.session_id, self.init_error, level="error"))

    # ─── inbound (main loop) ───────────────────────────────────────────────

    async def send_to_agent(self, msg: dict) -> None:
        """Route one client → server message. Runs on the main asyncio loop."""
        msg_type = msg.get("type")
        if msg_type == "user":
            self._inbox.put(_extract_prompt_content(msg))  # str, or blocks for multimodal
            return
        if msg_type == "control_response":
            self._resolve_permission(msg)
            return
        if msg_type == "control_request":
            await self._handle_control_request(msg)
            return
        logger.debug("[agent-server] ignoring unknown inbound type: %s", msg_type)

    async def _handle_control_request(self, msg: dict) -> None:
        inner = msg.get("request")
        if not isinstance(inner, dict):
            return
        subtype = inner.get("subtype")
        request_id = msg.get("request_id")
        if subtype == "interrupt":
            with self._lock:
                abort = self._current_abort
                pendings = list(self._pending.values())
            # Release any in-flight permission ask NOW so the worker unblocks
            # immediately rather than at permission_timeout_s (proposal §7: ESC
            # during a permission prompt must both deny the pending ask AND
            # abort the turn). Mirrors shutdown()'s deny-release.
            for pending in pendings:
                pending.reply = {"behavior": "deny", "message": "interrupted"}
                pending.event.set()
            if abort is not None:
                abort.abort("user_interrupt")
            return
        if subtype == "set_permission_mode":
            mode = inner.get("mode")
            if isinstance(mode, str) and self.tool_context is not None:
                _set_mode(self.tool_context, mode)
            self._ack(request_id)
            return
        if subtype == "set_model":
            model = inner.get("model")
            if isinstance(model, str) and self.provider is not None:
                try:
                    self.provider.model = model
                except Exception:  # noqa: BLE001
                    pass
            self._ack(request_id)
            return
        if subtype == "set_provider":
            self._do_set_provider(request_id, inner.get("provider"))
            return
        if subtype == "set_output_style":
            self._do_set_output_style(request_id, inner.get("style"))
            return
        if subtype == "knowledge":
            self._do_knowledge(request_id, inner.get("action"))
            return
        if subtype == "wiki":
            self._do_wiki(request_id, inner.get("action"), inner.get("path"))
            return
        if subtype in ("bg_run", "bg_list", "bg_kill", "bg_agent"):
            self._do_bgtask(request_id, subtype, inner.get("command"), inner.get("id"))
            return
        if subtype == "insights":
            self._do_insights(request_id)
            return
        if subtype == "plan":
            self._do_plan(request_id, inner.get("action"), inner.get("text"))
            return
        if subtype == "set_language":
            self._do_set_language(request_id, inner.get("language"))
            return
        if subtype == "set_effort":
            effort = inner.get("effort")
            if isinstance(effort, str) and effort in ("minimal", "low", "medium", "high"):
                self._effort = effort
            else:
                self._effort = None  # clear / invalid
            self._reply(request_id, {"ok": True, "effort": self._effort or "default"})
            return
        if subtype == "get_settings":
            self._reply(request_id, {
                "permission_mode": _current_mode(self.tool_context, self.config.permission_mode),
                "model": getattr(self.provider, "model", None),
                "provider": self.provider_name,
                "available_models": self._available_models(),
            })
            return
        if subtype == "get_context_usage":
            self._reply(request_id, self._context_usage())
            return
        if subtype == "compact":
            await self._do_compact(request_id, inner.get("instructions"))
            return
        if subtype == "rewind":
            self._do_rewind(request_id, inner.get("turns", 1))
            return
        if subtype == "list_sessions":
            self._reply(request_id, {"sessions": _list_saved_sessions()})
            return
        if subtype == "rename":
            name = inner.get("name")
            self._session_name = str(name).strip() if isinstance(name, str) and name.strip() else None
            self._save_session()
            self._reply(request_id, {"ok": True, "name": self._session_name or ""})
            return
        if subtype == "resume":
            self._do_resume(request_id, inner.get("session_id"))
            return
        if subtype == "branch":
            self._do_branch(request_id)
            return
        if subtype == "reload_plugins":
            count = 0
            try:
                from src.plugins.loader import load_plugins_from_directories

                dirs = [
                    str(Path.home() / ".claude" / "plugins"),
                    str(Path(self.cwd) / ".claude" / "plugins"),
                ]
                count = len(load_plugins_from_directories(dirs).plugins)
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] reload_plugins failed", exc_info=True)
            self._reply(request_id, {"ok": True, "count": count})
            return
        if subtype == "list_plugins":
            plugins: list[dict] = []
            try:
                from src.plugins.loader import load_plugins_from_directories

                dirs = [
                    str(Path.home() / ".claude" / "plugins"),
                    str(Path(self.cwd) / ".claude" / "plugins"),
                ]
                res = load_plugins_from_directories(dirs)
                plugins = [
                    {"name": p.name, "enabled": bool(p.enabled), "source": getattr(p, "source", "")}
                    for p in res.plugins
                ]
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] list_plugins failed", exc_info=True)
            self._reply(request_id, {"plugins": plugins})
            return
        if subtype == "list_skills":
            skills: list[dict] = []
            total = 0
            try:
                from src.skills.loader import get_all_skills

                all_s = list(get_all_skills(project_root=self.cwd))
                total = len(all_s)
                for s in all_s[:120]:
                    skills.append({
                        "name": getattr(s, "name", "") or "",
                        "description": str(getattr(s, "description", "") or "")[:80],
                    })
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] list_skills failed", exc_info=True)
            self._reply(request_id, {"skills": skills, "total": total})
            return
        if subtype == "list_agents":
            agents: list[dict] = []
            try:
                from src.agent.load_agents_dir import get_agent_definitions_with_overrides

                for a in get_agent_definitions_with_overrides(self.cwd):
                    agents.append({
                        "type": a.agent_type,
                        "source": getattr(a, "source", "built-in"),
                        "when": getattr(a, "when_to_use", "") or "",
                    })
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] list_agents failed", exc_info=True)
            self._reply(request_id, {"agents": agents})
            return
        if subtype == "list_hooks":
            info: dict = {}
            try:
                from src.settings.settings import load_settings

                h = load_settings(cwd=self.cwd).hooks
                info = {
                    "enabled": bool(getattr(h, "enabled", True)),
                    "timeout_ms": int(getattr(h, "timeout_ms", 0)),
                    "max_concurrent": int(getattr(h, "max_concurrent", 0)),
                }
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] list_hooks failed", exc_info=True)
            self._reply(request_id, {"hooks": info})
            return
        if subtype == "add_dir":
            path = inner.get("path")
            try:
                if not isinstance(path, str) or not path:
                    self._reply(request_id, {"ok": False, "error": "missing path"})
                    return
                p = Path(path)
                abspath = str((p if p.is_absolute() else Path(self.cwd) / p).resolve())
                if not Path(abspath).is_dir():
                    self._reply(request_id, {"ok": False, "error": "not a directory"})
                    return
                ctx = self.tool_context.permission_context if self.tool_context else None
                if ctx is None:
                    self._reply(request_id, {"ok": False, "error": "no permission context"})
                    return
                from src.permissions.types import AdditionalWorkingDirectory

                ctx.additional_working_directories[abspath] = AdditionalWorkingDirectory(
                    path=abspath, source="session"
                )
                self._reply(request_id, {"ok": True, "path": abspath})
            except Exception as exc:  # noqa: BLE001
                self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        if subtype == "list_permissions":
            ctx = self.tool_context.permission_context if self.tool_context else None
            mode, allow, deny = "default", [], []
            if ctx is not None:
                try:
                    from src.permissions import get_allow_rules, get_deny_rules

                    mode = getattr(ctx, "mode", "default") or "default"
                    allow = [_fmt_rule(r) for r in get_allow_rules(ctx)]
                    deny = [_fmt_rule(r) for r in get_deny_rules(ctx)]
                except Exception:  # noqa: BLE001
                    logger.debug("[agent-server] list_permissions failed", exc_info=True)
            self._reply(request_id, {"mode": mode, "allow": allow, "deny": deny})
            return
        if subtype == "list_mcp":
            rt = self._mcp_runtime
            servers = (
                [{"name": n, "tools": tools} for n, tools in rt.servers.items()]
                if rt is not None
                else []
            )
            self._reply(request_id, {"servers": servers})
            return
        if subtype == "clear":
            # Reset the conversation so /clear actually starts a fresh context
            # (not just the client screen). Idle-only.
            with self._lock:
                active = self._current_abort is not None
            if active:
                self._reply(request_id, {"ok": False, "error": "cannot clear during an active turn"})
                return
            try:
                if self.session is not None:
                    self.session.conversation.clear()
                self._reply(request_id, {"ok": True, "count": 0})
            except Exception as exc:  # noqa: BLE001
                self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        # Unknown subtype — error back so a correlating client doesn't hang.
        if isinstance(request_id, str):
            self._emit({
                "type": "control_response",
                "response": {
                    "subtype": "error",
                    "request_id": request_id,
                    "error": f"unsupported control request subtype: {subtype}",
                },
            })

    def _ack(self, request_id: object) -> None:
        if isinstance(request_id, str):
            self._reply(request_id, {"ok": True})

    def _reply(self, request_id: object, response: dict) -> None:
        if not isinstance(request_id, str):
            return
        self._emit({
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response,
            },
        })

    def _available_models(self) -> list[str]:
        """Provider's selectable models (for the /model picker). Best-effort."""
        try:
            fn = getattr(self.provider, "get_available_models", None)
            if callable(fn):
                models = fn()
                return [str(m) for m in models] if models else []
        except Exception:  # noqa: BLE001
            pass
        return []

    def _emit_agent_progress(self, ev: dict) -> None:
        """Forward a spawned subagent's progress to the client (the original's
        AgentProgressLine). Wired onto tool_context.agent_progress_emit."""
        self._emit({"type": "agent_progress", "session_id": self.session_id, **ev})

    def _save_session(self) -> None:
        """Persist the conversation to disk so it can be /resume'd. Best-effort,
        called at each turn end."""
        try:
            if self.session is None:
                return
            msgs = self.session.conversation.messages
            if not msgs:
                return
            d = _sessions_dir()
            d.mkdir(parents=True, exist_ok=True)
            payload = {
                "session_id": self.session_id,
                "model": getattr(self.provider, "model", None) or self.config.model or "",
                "cwd": self.cwd,
                "updated_at": time.time(),
                "message_count": len(msgs),
                "preview": _first_prompt_preview(msgs),
                "name": self._session_name,
                "conversation": self.session.conversation.to_dict(),
            }
            (d / f"{self.session_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        except Exception:  # noqa: BLE001 — persistence must never break a turn
            logger.debug("[agent-server] session save failed", exc_info=True)
        self._record_knowledge()

    @staticmethod
    def _message_text(msg: object) -> str:
        """Best-effort text of a conversation message (str content or text blocks)."""
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for b in content:
                t = getattr(b, "text", None)
                if t is None and isinstance(b, dict):
                    t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
            return "\n".join(parts)
        return ""

    def _record_knowledge(self) -> None:
        """Extract entities from the latest exchange into the knowledge graph
        (the original's /knowledge auto-learning). Best-effort; gated by the flag."""
        if not self._knowledge_enabled or self.session is None:
            return
        try:
            from src.knowledge import KnowledgeGraph

            if self._knowledge is None:
                self._knowledge = KnowledgeGraph.load()
            msgs = self.session.conversation.messages
            text = "\n".join(self._message_text(m) for m in msgs[-2:])  # last user+assistant
            recorded = False
            if self._knowledge_semantic and self.provider is not None:
                from src.knowledge import extract_entities_semantic

                ents = extract_entities_semantic(text, self.provider)
                for name, etype in ents:
                    self._knowledge.add(name, etype, now=time.time())
                recorded = bool(ents)
            if not recorded:  # heuristic (default, and fallback if semantic yields nothing)
                recorded = bool(self._knowledge.record_from_text(text, now=time.time()))
            if recorded:
                self._knowledge.save()
        except Exception:  # noqa: BLE001 — knowledge must never break a turn
            logger.debug("[agent-server] knowledge record failed", exc_info=True)

    def _do_set_provider(self, request_id: object, name: object) -> None:
        """Switch the LLM provider mid-session (the original's /provider). Rebuilds
        the provider + tool registry but keeps the conversation. Idle-only."""
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {"ok": False, "error": "cannot switch provider during an active turn"})
            return
        try:
            if not isinstance(name, str) or not name:
                self._reply(request_id, {"ok": False, "error": "missing provider"})
                return
            from src.config import get_provider_config
            from src.providers import get_provider_class, provider_requires_api_key, resolve_api_key
            from src.tool_system.defaults import build_default_registry

            provider_cfg = get_provider_config(name)
            api_key = resolve_api_key(name, provider_cfg)
            if not api_key and provider_requires_api_key(name):
                self._reply(request_id, {"ok": False, "error": f"provider '{name}' is not configured (no API key)"})
                return
            provider_cls = get_provider_class(name)
            model = provider_cfg.get("default_model")
            provider = provider_cls(api_key=api_key, base_url=provider_cfg.get("base_url"), model=model)
            registry = build_default_registry(provider=provider)
            cfg = self.config
            if cfg.allowed_tools:
                allow = {n.lower() for n in cfg.allowed_tools}
                _filter_registry(registry, keep=lambda n: n.lower() in allow)
            if cfg.disallowed_tools:
                deny = {n.lower() for n in cfg.disallowed_tools}
                _filter_registry(registry, keep=lambda n: n.lower() not in deny)
            if self._mcp_runtime is not None:  # keep MCP tools across the switch
                for mtool in self._mcp_runtime.tools:
                    try:
                        registry.register(mtool)
                    except Exception:  # noqa: BLE001
                        pass
            self.provider = provider
            self.provider_name = name
            cfg.provider_name = name
            cfg.model = model
            self.tool_registry = registry
            self._reply(request_id, {"ok": True, "provider": name, "model": model or ""})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_provider failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _compose_with_plan(self, base: Any) -> Any:
        """Append the active /plan as a system-prompt section so the agent follows
        it. No plan → returns base unchanged (regression-safe)."""
        try:
            from src.plan import get_plan

            plan = get_plan(self.cwd)
            if plan and isinstance(base, list):
                base = base + [{"type": "text", "text": f"# Current Plan\nFollow this plan set by the user:\n\n{plan}"}]
        except Exception:  # noqa: BLE001
            logger.debug("[agent-server] plan compose failed", exc_info=True)
        # Response language (the original's LanguagePicker, §6).
        lang = getattr(self, "_language", None)
        if lang and isinstance(base, list):
            base = base + [{"type": "text", "text": f"# Response Language\nRespond in {lang} unless the user writes in another language."}]
        return base

    def _do_set_language(self, request_id: object, language: object) -> None:
        """Set the preferred response language (LanguagePicker, §6) and recompose
        the system prompt so the agent honors it. Empty clears it."""
        lang = str(language or "").strip()
        self._language = lang or None
        if self._base_system_prompt is not None:
            self.system_prompt = self._compose_with_plan(self._base_system_prompt)
        self._reply(request_id, {"ok": True, "language": self._language or ""})

    def _do_plan(self, request_id: object, action: object, text: object) -> None:
        """/plan: view (default) | set <text> | clear. The plan is injected into
        the system prompt (the original's /plan)."""
        try:
            from src.plan import clear_plan, get_plan, set_plan

            act = str(action or "view").strip().lower()
            if act == "set":
                if not isinstance(text, str) or not text.strip():
                    self._reply(request_id, {"ok": False, "error": "usage: /plan <text>"})
                    return
                set_plan(self.cwd, text)
            elif act == "clear":
                clear_plan(self.cwd)
            if act in ("set", "clear") and self._base_system_prompt is not None:
                self.system_prompt = self._compose_with_plan(self._base_system_prompt)
            self._reply(request_id, {"ok": True, "plan": get_plan(self.cwd)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] plan failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_insights(self, request_id: object) -> None:
        """/insights: a model-based analysis of the session (the original's
        Insights). Runs the model call in a daemon thread (_emit is thread-safe)
        so it never blocks the control loop; replies when the narrative is ready."""
        if self.session is None or self.provider is None:
            self._reply(request_id, {"ok": False, "error": "no active session"})
            return
        msgs = list(self.session.conversation.messages)
        if not msgs:
            self._reply(request_id, {"ok": False, "error": "no conversation yet"})
            return
        text = "\n".join(f"{getattr(m, 'role', '?')}: {self._message_text(m)[:400]}" for m in msgs[-12:])

        def _work() -> None:
            try:
                prompt = (
                    "Analyze this coding session and give 3-5 concise insights: what was "
                    "accomplished, notable patterns, and one suggestion for next steps. "
                    "Be brief — short bullet points.\n\nSESSION:\n" + text
                )
                resp = self.provider.chat([{"role": "user", "content": prompt}])
                self._reply(request_id, {"ok": True, "insights": (getattr(resp, "content", "") or "").strip()})
            except Exception as exc:  # noqa: BLE001
                self._reply(request_id, {"ok": False, "error": str(exc)})

        threading.Thread(target=_work, name=f"insights-{self.session_id}", daemon=True).start()

    def _do_bgtask(self, request_id: object, subtype: str, command: object, tid: object) -> None:
        """Background tasks (the original's Ctrl+B runs): bg_run starts a detached
        shell command, bg_list lists them, bg_kill terminates one."""
        try:
            from src.background import BackgroundTasks

            if self._bgtasks is None:
                self._bgtasks = BackgroundTasks()
            if subtype == "bg_run":
                if not isinstance(command, str) or not command.strip():
                    self._reply(request_id, {"ok": False, "error": "usage: /bg <command>"})
                    return
                t = self._bgtasks.start(command.strip(), self.cwd, now=time.time())
                self._reply(request_id, {"ok": True, "id": t.id, "command": t.command})
                return
            if subtype == "bg_agent":
                # Background agent run: a detached `clawcodex -p <prompt>` subprocess
                # (the §9 async-agent variant) — fully isolated, concurrent, tracked.
                if not isinstance(command, str) or not command.strip():
                    self._reply(request_id, {"ok": False, "error": "usage: /bg-agent <prompt>"})
                    return
                import shlex

                cmd = f"clawcodex -p {shlex.quote(command.strip())}"
                t = self._bgtasks.start(cmd, self.cwd, now=time.time())
                self._reply(request_id, {"ok": True, "id": t.id, "command": cmd})
                return
            if subtype == "bg_kill":
                ok = self._bgtasks.kill(str(tid or ""))
                self._reply(request_id, {"ok": ok})
                return
            # bg_list
            tasks = [
                {
                    "id": t.id,
                    "command": t.command,
                    "status": t.status,
                    "exit_code": t.exit_code,
                    "output": (t.output or "")[-400:],
                }
                for t in self._bgtasks.list()
            ]
            self._reply(request_id, {"ok": True, "tasks": tasks})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] bgtask failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_wiki(self, request_id: object, action: object, path: object) -> None:
        """/wiki: init | status | ingest <path>. File-based project wiki under
        .clawcodex/wiki (the original's /wiki)."""
        try:
            from src.wiki import ingest_source, init_wiki, wiki_status

            act = str(action or "status").strip().lower()
            if act == "init":
                self._reply(request_id, {"ok": True, **init_wiki(self.cwd)})
            elif act == "ingest":
                if not isinstance(path, str) or not path.strip():
                    self._reply(request_id, {"ok": False, "error": "usage: /wiki ingest <path>"})
                    return
                self._reply(request_id, ingest_source(self.cwd, path.strip()))
            else:
                self._reply(request_id, {"ok": True, **wiki_status(self.cwd)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] wiki failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_knowledge(self, request_id: object, action: object) -> None:
        """/knowledge: status (default) | list | clear | enable | disable. Surfaces
        the auto-populated knowledge graph (the original's Knowledge Graph engine)."""
        try:
            from src.knowledge import KnowledgeGraph

            if self._knowledge is None:
                self._knowledge = KnowledgeGraph.load()
            act = str(action or "status").strip().lower()
            if act == "clear":
                self._knowledge.clear()
                self._knowledge.save()
                self._reply(request_id, {"ok": True, "enabled": self._knowledge_enabled, "stats": self._knowledge.stats()})
                return
            if act in ("enable", "disable"):
                self._knowledge_enabled = act == "enable"
                self._reply(request_id, {"ok": True, "enabled": self._knowledge_enabled, "stats": self._knowledge.stats()})
                return
            if act in ("semantic", "heuristic"):
                self._knowledge_semantic = act == "semantic"
                self._reply(
                    request_id,
                    {"ok": True, "enabled": self._knowledge_enabled, "semantic": self._knowledge_semantic, "stats": self._knowledge.stats()},
                )
                return
            entities = (
                [{"name": e.name, "type": e.type, "count": e.count} for e in self._knowledge.top(20)]
                if act == "list"
                else []
            )
            self._reply(
                request_id,
                {
                    "ok": True,
                    "enabled": self._knowledge_enabled,
                    "semantic": self._knowledge_semantic,
                    "stats": self._knowledge.stats(),
                    "entities": entities,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] knowledge failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_set_output_style(self, request_id: object, style: object) -> None:
        """Switch the output style mid-session (the original's /output-style).
        Sets tool_context.output_style_name + rebuilds the system prompt so the
        style's section is appended on the next turn. Idle-only."""
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {"ok": False, "error": "cannot change output style during an active turn"})
            return
        try:
            from src.settings.constants import VALID_OUTPUT_STYLES

            if not isinstance(style, str) or style not in VALID_OUTPUT_STYLES:
                self._reply(
                    request_id,
                    {"ok": False, "error": f"invalid style (valid: {', '.join(VALID_OUTPUT_STYLES)})"},
                )
                return
            tc = self.tool_context
            if tc is None:
                self._reply(request_id, {"ok": False, "error": "session not ready"})
                return
            tc.output_style_name = style
            # Rebuild the system prompt so the style section takes effect next turn.
            try:
                from src.outputStyles import resolve_output_style
                from src.query.agent_loop_compat import build_effective_system_prompt

                style_prompt = resolve_output_style(style, getattr(tc, "output_style_dir", None)).prompt
                self._base_system_prompt = build_effective_system_prompt(style_prompt, tc, provider=self.provider)
                self.system_prompt = self._compose_with_plan(self._base_system_prompt)
            except Exception:  # noqa: BLE001 - keep the style set even if rebuild is unavailable
                logger.debug("[agent-server] system prompt rebuild after set_output_style failed", exc_info=True)
            self._reply(request_id, {"ok": True, "style": style})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_output_style failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_branch(self, request_id: object) -> None:
        """Fork the current conversation to a new saved session (the original's
        /branch). Read-only on the live session — just writes a copy under a new
        id so /resume can switch to it later."""
        try:
            if self.session is None or not self.session.conversation.messages:
                self._reply(request_id, {"ok": False, "error": "nothing to branch"})
                return
            msgs = self.session.conversation.messages
            new_id = f"{self.session_id}-b{_uuid.uuid4().hex[:6]}"
            base = self._session_name or _first_prompt_preview(msgs) or self.session_id
            d = _sessions_dir()
            d.mkdir(parents=True, exist_ok=True)
            payload = {
                "session_id": new_id,
                "model": getattr(self.provider, "model", None) or self.config.model or "",
                "cwd": self.cwd,
                "updated_at": time.time(),
                "message_count": len(msgs),
                "preview": _first_prompt_preview(msgs),
                "name": f"branch of {base}",
                "conversation": self.session.conversation.to_dict(),
            }
            (d / f"{new_id}.json").write_text(json.dumps(payload), encoding="utf-8")
            self._reply(request_id, {"ok": True, "session_id": new_id})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] branch failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_resume(self, request_id: object, session_id: object) -> None:
        """Load a saved conversation into this session (the original's /resume).
        Idle-only — replacing the conversation mid-turn would race the worker."""
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {"ok": False, "error": "cannot resume during an active turn"})
            return
        try:
            if not isinstance(session_id, str) or not session_id:
                self._reply(request_id, {"ok": False, "error": "missing session_id"})
                return
            f = _sessions_dir() / f"{session_id}.json"
            if not f.exists():
                self._reply(request_id, {"ok": False, "error": "session not found"})
                return
            from src.agent.conversation import Conversation

            data = json.loads(f.read_text(encoding="utf-8"))
            conv = Conversation.from_dict(data.get("conversation", {"messages": []}))
            self.session.conversation = conv
            self._reply(request_id, {
                "ok": True,
                "count": len(conv.messages),
                "preview": data.get("preview", ""),
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] resume failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_rewind(self, request_id: object, turns: object) -> None:
        """Drop the last N prompt-turns from the conversation (the original's
        /rewind). A prompt-turn starts at a real user prompt (string/text
        content — not a tool_result, which is also role 'user') and runs to the
        end. Idle-only: the worker mutates the conversation during a turn."""
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {"ok": False, "error": "cannot rewind during an active turn"})
            return
        try:
            n = int(turns) if isinstance(turns, (int, float)) else 1
            n = max(1, n)
            msgs = self.session.conversation.messages if self.session is not None else []

            def is_prompt(m: Any) -> bool:
                if getattr(m, "role", None) != "user":
                    return False
                c = getattr(m, "content", None)
                if isinstance(c, str):
                    return True
                if isinstance(c, list):
                    for b in c:
                        t = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                        if t == "text":
                            return True
                return False

            prompt_idxs = [i for i, m in enumerate(msgs) if is_prompt(m)]
            if not prompt_idxs:
                self._reply(request_id, {"ok": True, "removed": 0, "count": len(msgs)})
                return
            target = prompt_idxs[max(0, len(prompt_idxs) - n)]
            removed = len(msgs) - target
            del msgs[target:]
            self._reply(request_id, {"ok": True, "removed": removed, "count": len(msgs)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] rewind failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _system_prompt_text(self) -> str:
        """The active system prompt as a plain string (it may be a block list —
        build_effective_system_prompt returns the full base block list)."""
        sp = self.system_prompt
        if isinstance(sp, str):
            return sp
        if isinstance(sp, list):
            parts: list[str] = []
            for b in sp:
                if isinstance(b, dict):
                    parts.append(str(b.get("text", "")))
                else:
                    parts.append(str(getattr(b, "text", b)))
            return "\n".join(parts)
        return str(sp)

    def _context_usage(self) -> dict:
        """Live context-window usage for the status bar (the original's
        get_context_usage). Best-effort — any failure degrades to just the
        protocol version so the client never hangs or crashes."""
        out: dict = {"protocol_version": PROTOCOL_VERSION}
        try:
            from src.context_system.context_analyzer import analyze_context

            model = getattr(self.provider, "model", None) or self.config.model or ""
            messages = (
                self.session.conversation.get_messages() if self.session is not None else []
            )
            data = analyze_context(
                conversation_api_messages=messages,
                model=model,
                system_prompt=self._system_prompt_text(),
                tool_schemas=_tool_schemas(self.tool_registry),
                claude_md_content="",
            )
            out.update({
                "total_tokens": data.total_tokens,
                "max_tokens": data.max_tokens,
                "percentage": round(data.percentage, 1),
                "categories": [
                    {"name": c.name, "tokens": c.tokens}
                    for c in data.categories
                    if not c.is_deferred and c.name != "Free space"
                ],
            })
        except Exception as exc:  # noqa: BLE001 — never let a usage pull break the session
            out["error"] = str(exc)
        return out

    async def _do_compact(self, request_id: object, instructions: object) -> None:
        """Manually compact the conversation (the original's /compact). Idle-only:
        the worker thread mutates the conversation during a turn, so refuse
        mid-turn rather than race the message list."""
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {"ok": False, "error": "cannot compact during an active turn"})
            return
        try:
            from src.compact_service.service import compact_conversation

            model = getattr(self.provider, "model", None) or self.config.model or ""
            instr = instructions if isinstance(instructions, str) and instructions.strip() else None
            res = await compact_conversation(
                self.session.conversation,
                self.provider,
                model,
                custom_instructions=instr,
                trigger="manual",
            )
            self._reply(request_id, {
                "ok": True,
                "tokens_saved": res.tokens_saved,
                "pre_compact_count": res.pre_compact_count,
                "post_compact_count": res.post_compact_count,
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] compact failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _resolve_permission(self, msg: dict) -> None:
        response = msg.get("response")
        if not isinstance(response, dict):
            return
        request_id = response.get("request_id")
        inner = response.get("response")
        if not isinstance(request_id, str):
            return
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return
        pending.reply = inner if isinstance(inner, dict) else {"behavior": "deny"}
        pending.event.set()

    # ─── permission handler (worker thread; BLOCKS) ────────────────────────

    def permission_handler(self, request: Any) -> Any:
        from src.permissions.types import PermissionAskReply

        request_id = str(_uuid.uuid4())
        pending = _Pending(event=threading.Event())
        with self._lock:
            self._pending[request_id] = pending

        self._emit({
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": getattr(request, "tool_name", ""),
                "input": getattr(request, "tool_input", None) or {},
                "tool_use_id": None,
            },
        })

        got = pending.event.wait(timeout=self.config.permission_timeout_s)
        with self._lock:
            self._pending.pop(request_id, None)

        if not got:
            return PermissionAskReply(
                behavior="deny", message="permission request timed out"
            )
        reply = pending.reply or {"behavior": "deny"}
        behavior = reply.get("behavior")
        if behavior == "allow":
            updated = reply.get("updatedInput")
            if not isinstance(updated, dict):
                updated = reply.get("updated_input")
            return PermissionAskReply(
                behavior="allow",
                updated_input=updated if isinstance(updated, dict) else None,
            )
        return PermissionAskReply(
            behavior="deny", message=str(reply.get("message", "")) or "denied by user"
        )

    # ─── worker thread (runs query() turns) ────────────────────────────────

    def start(self) -> None:
        self._worker = threading.Thread(
            target=self._run_worker,
            name=f"agent-server-{self.session_id}",
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            item = self._inbox.get()
            if item is _SHUTDOWN or self._stop.is_set():
                break
            if not isinstance(item, (str, list)):  # str prompt, or multimodal blocks
                continue
            self._run_turn(item)
        self._close_stream()

    def _run_turn(self, prompt) -> None:  # prompt: str | list[ContentBlock] (multimodal)
        from src.query.agent_loop_compat import run_query_as_agent_loop

        if self.init_error is not None:
            self._emit(_result_message(
                self.session_id, subtype="error", num_turns=0,
                result=self.init_error, is_error=True, error=self.init_error,
            ))
            return

        abort = AbortController()
        with self._lock:
            self._current_abort = abort
        # Wire the per-turn controller into the tool context so an interrupt
        # tears down an in-flight tool (Bash supervisor, etc.), not just the
        # model stream. A fresh controller per turn avoids a prior turn's
        # abort pre-cancelling the next one.
        if self.tool_context is not None:
            self.tool_context.abort_controller = abort

        self.session.conversation.add_user_message(prompt)
        start = time.monotonic()

        def on_text_chunk(chunk: str) -> None:
            self._emit({
                "type": "stream_event",
                "session_id": self.session_id,
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": chunk},
                },
            })

        def on_message(message: Any) -> None:
            # Persist into the session conversation so the next turn pairs
            # tool_use ↔ tool_result, then ship the SDK envelope to the client.
            try:
                self.session.conversation.add_message(message.role, message.content)
            except Exception:  # noqa: BLE001
                logger.exception("[agent-server] persist failed")
            env = _sdk_envelope(message, self.session_id)
            if env is not None:
                self._emit(env)

        # /effort: wrap the provider to inject reasoning_effort (default off ⇒
        # the real provider is used unchanged).
        turn_provider = _EffortProvider(self.provider, self._effort) if self._effort else self.provider
        try:
            result = asyncio.run(run_query_as_agent_loop(
                initial_messages=list(self.session.conversation.messages),
                provider=turn_provider,
                tool_registry=self.tool_registry,
                tool_context=self.tool_context,
                system_prompt=self.system_prompt,
                max_turns=self.config.max_turns,
                on_text_chunk=on_text_chunk,
                on_message=on_message,
                abort_controller=abort,
            ))
        except AbortError:
            self._emit(_result_message(
                self.session_id, subtype="cancelled", num_turns=0,
                result="", is_error=False,
                duration_ms=int((time.monotonic() - start) * 1000),
            ))
            return
        except Exception as exc:  # noqa: BLE001 - one bad turn must not kill the session
            logger.exception("[agent-server] turn failed")
            self._emit(_result_message(
                self.session_id, subtype="error", num_turns=0,
                result=str(exc), is_error=True, error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            ))
            return
        finally:
            with self._lock:
                self._current_abort = None

        _usage = result.usage if result.num_turns > 0 else None
        _cost = 0.0
        if _usage:
            try:
                from src.services.pricing import compute_cost

                _cost = compute_cost(getattr(self.provider, "model", None) or self.config.model or "", _usage)
            except Exception:  # noqa: BLE001 — cost is best-effort, never break the turn
                _cost = 0.0
        self._emit(_result_message(
            self.session_id,
            subtype="success",
            num_turns=result.num_turns,
            result=result.response_text,
            is_error=False,
            usage=_usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            total_cost_usd=_cost,
        ))
        self._save_session()  # persist for /resume

    async def shutdown(self) -> None:
        self._stop.set()
        # Unblock any in-flight permission asks with a deny.
        with self._lock:
            pendings = list(self._pending.values())
            abort = self._current_abort
        for pending in pendings:
            pending.reply = {"behavior": "deny", "message": "session closed"}
            pending.event.set()
        if abort is not None:
            abort.abort("session_closed")
        self._inbox.put(_SHUTDOWN)
        worker = self._worker
        if worker is not None:
            # Bounded join: a well-behaved tool honours the abort and unwinds
            # promptly. A tool that ignores the abort (e.g. a blocking sleep)
            # can outlive this 5s window — the thread is a daemon so it never
            # blocks process exit, but `_close_stream` is deferred until it
            # actually returns. Acceptable for the spike; revisit if a tool
            # needs hard preemption.
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: worker.join(timeout=5.0)
            )
        if self._mcp_runtime is not None:
            try:
                self._mcp_runtime.shutdown()  # disconnect MCP servers + stop their loop
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] MCP shutdown failed", exc_info=True)
            self._mcp_runtime = None


def make_spawn_agent(config: AgentServerConfig | None = None):
    """Build a :data:`SpawnAgent` bound to ``config``.

    The returned coroutine matches the ``DirectConnectServer.spawn_agent``
    contract: ``(session_id, cwd, permission_mode) -> AgentHandle``.
    """

    cfg = config or AgentServerConfig()

    async def spawn(session_id: str, cwd: str, perm_mode: str | None) -> AgentHandle:
        loop = asyncio.get_running_loop()
        out_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        sess = _AgentSession(
            session_id=session_id,
            cwd=cwd,
            config=cfg,
            loop=loop,
            out_queue=out_queue,
        )
        # Build the provider/registry/tool_context off the event loop — these
        # touch config/filesystem and must not block the WS pump.
        await loop.run_in_executor(None, lambda: _build_runtime(sess, perm_mode))
        # Wire the permission handler now that tool_context exists.
        if sess.tool_context is not None and sess.init_error is None:
            sess.tool_context.permission_handler = sess.permission_handler
        sess.start()
        sess.emit_init()

        async def messages_from_agent() -> AsyncIterator[dict]:
            while True:
                item = await out_queue.get()
                if item is None:
                    return
                yield item

        return AgentHandle(
            send_to_agent=sess.send_to_agent,
            messages_from_agent=messages_from_agent,
            shutdown=sess.shutdown,
        )

    return spawn


# ─── runtime construction (mirrors entrypoints/headless.py) ───────────────────


def _make_elicitation_handler(sess: "_AgentSession") -> Any:
    """Async MCP elicitation handler that bridges a server's input request to the
    TUI via the session's control-request round-trip (reusing the permission
    ``_pending`` mechanism). Runs on the McpRuntime loop; ``_emit`` is thread-safe
    and the main loop's control_response handler sets the pending event.
    """

    async def _elicit(params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(_uuid.uuid4())
        pending = _Pending(event=threading.Event())
        with sess._lock:
            sess._pending[request_id] = pending
        sess._emit({
            "type": "control_request",
            "request_id": request_id,
            "request": {"subtype": "mcp_elicitation", "params": params},
        })
        loop = asyncio.get_event_loop()
        try:
            got = await loop.run_in_executor(
                None, pending.event.wait, sess.config.permission_timeout_s
            )
        finally:
            with sess._lock:
                sess._pending.pop(request_id, None)
        if not got:
            return {"action": "cancel"}
        return pending.reply or {"action": "decline"}

    return _elicit


def _build_runtime(sess: _AgentSession, perm_mode: str | None) -> None:
    """Construct provider, registry, session, tool_context for ``sess``.

    Errors are captured into ``sess.init_error`` rather than raised, so the
    client gets a clean error message instead of a bare socket close.
    """
    try:
        from src.config import get_default_provider, get_provider_config
        from src.permissions.settings_paths import default_setup_paths
        from src.permissions.setup import setup_permissions
        from src.providers import (
            get_provider_class,
            provider_requires_api_key,
            resolve_api_key,
        )
        from src.agent import Session
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry

        cfg = sess.config
        provider_name = cfg.provider_name or get_default_provider()
        provider_cfg = get_provider_config(provider_name)
        api_key = resolve_api_key(provider_name, provider_cfg)
        if not api_key and provider_requires_api_key(provider_name):
            sess.init_error = (
                f"API key for provider '{provider_name}' is not configured. "
                "Run `clawcodex login` to set it up."
            )
            sess.provider_name = provider_name
            return

        provider_cls = get_provider_class(provider_name)
        model = cfg.model or provider_cfg.get("default_model")
        provider = provider_cls(
            api_key=api_key, base_url=provider_cfg.get("base_url"), model=model
        )

        registry = build_default_registry(provider=provider)
        if cfg.allowed_tools:
            allow = {n.lower() for n in cfg.allowed_tools}
            _filter_registry(registry, keep=lambda n: n.lower() in allow)
        if cfg.disallowed_tools:
            deny = {n.lower() for n in cfg.disallowed_tools}
            _filter_registry(registry, keep=lambda n: n.lower() not in deny)

        # Connect configured MCP servers (guarded: no servers ⇒ no-op). Their
        # tools run on McpRuntime's dedicated loop so they survive the per-turn
        # asyncio.run. Registered after allow/deny filtering so an MCP-enabling
        # user always gets them.
        try:
            from src.server.mcp_runtime import McpRuntime

            mcp_rt = McpRuntime()
            if mcp_rt.start():
                for mtool in mcp_rt.tools:
                    try:
                        registry.register(mtool)
                    except Exception:  # noqa: BLE001
                        logger.debug("[mcp] register failed: %s", getattr(mtool, "name", "?"), exc_info=True)
                sess._mcp_runtime = mcp_rt
                # Wire MCP elicitation → TUI form (servers can request user input).
                _eh = _make_elicitation_handler(sess)
                for _cl in mcp_rt.clients.values():
                    try:
                        _cl.set_elicitation_handler(_eh)
                    except Exception:  # noqa: BLE001
                        pass
                logger.info(
                    "[agent-server] MCP: %d tool(s) from %d server(s)",
                    len(mcp_rt.tools), len(mcp_rt.servers),
                )
        except Exception:  # noqa: BLE001 — MCP must never break startup
            logger.debug("[agent-server] MCP bootstrap skipped", exc_info=True)

        workspace_root = Path(sess.cwd)
        mode = perm_mode or cfg.permission_mode or "default"
        bypass = mode == "bypassPermissions"
        perm_setup = setup_permissions(
            cwd=str(workspace_root),
            mode=mode,  # type: ignore[arg-type]
            is_bypass_available=bypass,
            **default_setup_paths(str(workspace_root)),
        )
        tool_context = ToolContext(
            workspace_root=workspace_root,
            permission_context=perm_setup.context,
            abort_controller=AbortController(),
        )
        tool_context.options.is_non_interactive_session = True
        if sess._mcp_runtime is not None:
            tool_context.mcp_clients = sess._mcp_runtime.clients  # server-name catalog for the agent tool

        try:
            from src.outputStyles import resolve_output_style
            from src.query.agent_loop_compat import build_effective_system_prompt

            style_prompt = resolve_output_style(
                getattr(tool_context, "output_style_name", None),
                getattr(tool_context, "output_style_dir", None),
            ).prompt
            system_prompt = build_effective_system_prompt(
                style_prompt, tool_context, provider=provider
            )
        except Exception:  # noqa: BLE001 - fall back to a plain prompt
            logger.debug("[agent-server] system prompt build failed", exc_info=True)
            system_prompt = "You are a helpful assistant."

        sess.provider = provider
        sess.provider_name = provider_name
        sess.tool_registry = registry
        sess.tool_context = tool_context
        tool_context.agent_progress_emit = sess._emit_agent_progress  # stream subagent progress
        sess.session = Session.create(provider_name, getattr(provider, "model", model or ""))
        sess._base_system_prompt = system_prompt
        sess.system_prompt = sess._compose_with_plan(system_prompt)  # honor an existing /plan
    except Exception as exc:  # noqa: BLE001
        logger.exception("[agent-server] runtime build failed")
        sess.init_error = f"agent-server failed to start: {exc}"


def _filter_registry(registry, *, keep) -> None:
    try:
        entries = list(registry.list_tools())
    except Exception:  # noqa: BLE001
        return
    for tool in entries:
        name = getattr(tool, "name", "")
        if not keep(name):
            try:
                registry.unregister(name)
            except Exception:  # noqa: BLE001
                continue


# ─── message shaping ─────────────────────────────────────────────────────────


def _sdk_envelope(message: Any, session_id: str) -> dict | None:
    """Wrap a :class:`Message` into the SDK envelope the client renders."""
    from src.types.messages import message_to_dict

    try:
        d = message_to_dict(message)
    except Exception:  # noqa: BLE001
        return None
    role = d.get("role", getattr(message, "role", "assistant"))
    msg_type = "assistant" if role == "assistant" else "user"
    return {
        "type": msg_type,
        "uuid": d.get("uuid"),
        "session_id": session_id,
        "message": {"role": role, "content": d.get("content")},
    }


def _result_message(
    session_id: str,
    *,
    subtype: str,
    num_turns: int,
    result: str,
    is_error: bool,
    usage: dict | None = None,
    error: str | None = None,
    duration_ms: int = 0,
    total_cost_usd: float = 0.0,
) -> dict:
    payload: dict[str, Any] = {
        "type": "result",
        "subtype": subtype,
        "session_id": session_id,
        "num_turns": num_turns,
        "result": result,
        "duration_ms": duration_ms,
        "is_error": is_error,
        "usage": usage or None,
        "total_cost_usd": total_cost_usd,
    }
    if error is not None:
        payload["error"] = error
    return payload


def _fmt_rule(rule: Any) -> str:
    """Render a PermissionRule as e.g. ``Bash(ls:*)`` or ``Read`` (for /permissions)."""
    v = getattr(rule, "rule_value", None)
    tool = getattr(v, "tool_name", "") or "?"
    content = getattr(v, "rule_content", None)
    return f"{tool}({content})" if content else tool


class _EffortProvider:
    """Wraps a provider to inject ``reasoning_effort`` via ``extra_body`` on chat
    calls (the original's /effort). Used only when /effort is set; delegates
    everything else to the inner provider, so the default path is untouched."""

    def __init__(self, inner: Any, effort: str) -> None:
        self._inner = inner
        self._effort = effort

    def __getattr__(self, name: str) -> Any:  # model, get_available_models, …
        return getattr(self._inner, name)

    def _inject(self, kwargs: dict) -> dict:
        eb = dict(kwargs.get("extra_body") or {})
        eb.setdefault("reasoning_effort", self._effort)
        kwargs["extra_body"] = eb
        return kwargs

    def chat_stream_response(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.chat_stream_response(*args, **self._inject(kwargs))

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.chat(*args, **self._inject(kwargs))

    def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.chat_stream(*args, **self._inject(kwargs))

    async def chat_async(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.chat_async(*args, **self._inject(kwargs))


def _sessions_dir() -> Path:
    return Path.home() / ".clawcodex" / "sessions"


def _first_prompt_preview(msgs: list) -> str:
    """First real user prompt text (for the /resume session list)."""
    for m in msgs:
        if getattr(m, "role", None) != "user":
            continue
        c = getattr(m, "content", None)
        if isinstance(c, str):
            return c[:80]
        if isinstance(c, list):
            for b in c:
                t = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                if t == "text":
                    txt = b.get("text") if isinstance(b, dict) else getattr(b, "text", "")
                    if txt:
                        return str(txt)[:80]
    return ""


def _list_saved_sessions(limit: int = 20) -> list[dict]:
    """Saved sessions, newest first (for /resume)."""
    out: list[dict] = []
    try:
        d = _sessions_dir()
        if not d.exists():
            return []
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            out.append({
                "session_id": data.get("session_id", f.stem),
                "updated_at": data.get("updated_at", 0),
                "preview": data.get("preview", ""),
                "name": data.get("name") or "",
                "message_count": data.get("message_count", 0),
                "model": data.get("model", ""),
            })
        out.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
    except Exception:  # noqa: BLE001
        pass
    return out[:limit]


def _system_message(session_id: str, text: str, *, level: str = "info") -> dict:
    return {
        "type": "system",
        "subtype": "status",
        "session_id": session_id,
        "level": level,
        "message": text,
    }


def _extract_prompt_text(msg: dict) -> str:
    message = msg.get("message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content is not None else ""


def _extract_prompt_content(msg: dict):
    """Like ``_extract_prompt_text`` but PRESERVES a content-block list when it
    carries non-text blocks (e.g. images) so multimodal input flows through to
    ``add_user_message`` (MessageContent = str | list[ContentBlock]). Text-only
    content still collapses to a plain string (the common path)."""
    message = msg.get("message")
    content = message.get("content") if isinstance(message, dict) else msg.get("content")
    if isinstance(content, list):
        has_nontext = any(
            isinstance(b, dict) and b.get("type") not in (None, "text") for b in content
        )
        if has_nontext:
            return content  # keep blocks intact (images, etc.)
    return _extract_prompt_text(msg)


def _tool_schemas(registry: Any) -> list[dict[str, Any]]:
    """JSON-able ``[{name, description, input_schema}]`` for ``system/init``.

    Mirrors the canonical API tool-schema build at ``query.py:637`` — the
    description comes from ``tool.prompt()`` (a string), NOT the raw
    ``tool.description`` field, which may be a callable for dynamic tools.
    """
    out: list[dict[str, Any]] = []
    if registry is None:
        return out
    try:
        tools = list(registry.list_tools())
    except Exception:  # noqa: BLE001 - init must never crash the session
        logger.debug("[agent-server] tool enumeration failed", exc_info=True)
        return out
    for tool in tools:
        is_enabled = getattr(tool, "is_enabled", None)
        if callable(is_enabled) and not is_enabled():
            continue
        try:
            prompt = getattr(tool, "prompt", None)
            desc = prompt() if callable(prompt) else getattr(tool, "description", "")
        except Exception:  # noqa: BLE001
            desc = ""
        schema = getattr(tool, "input_schema", None)
        out.append({
            "name": getattr(tool, "name", ""),
            "description": desc if isinstance(desc, str) else "",
            "input_schema": dict(schema) if isinstance(schema, Mapping) else None,
        })
    return out


def _json_safe(obj: Any) -> Any:
    """Recursively coerce ``obj`` into a JSON-serializable structure.

    Unknown/opaque values (functions, dataclasses, …) degrade to ``str`` so a
    single bad field never makes the WS pump's ``json.dumps`` raise.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Mapping):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return str(obj)


def _current_mode(tool_context: Any, default: str) -> str:
    if tool_context is None:
        return default
    pc = getattr(tool_context, "permission_context", None)
    return getattr(pc, "mode", default) if pc is not None else default


def _set_mode(tool_context: Any, mode: str) -> None:
    pc = getattr(tool_context, "permission_context", None)
    if pc is not None:
        try:
            pc.mode = mode  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "AgentServerConfig",
    "PROTOCOL_VERSION",
    "make_spawn_agent",
]
