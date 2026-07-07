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
    {type:'control_request', request_id, request:{subtype:'set_permission_mode', mode}}
    {type:'control_request', request_id, request:{subtype:'set_model', model, provider?}}
                                             # replies {ok, model, warning?} | {ok:false, error}
    {type:'control_request', request_id, request:{subtype:'get_settings'|'get_context_usage'}}

Concurrency model
-----------------
The canonical permission handler is a **blocking, synchronous** callable
(``PermissionAskHandler``). To turn a permission ask into a wire round-trip we
must block *something* until the client answers — but never the asyncio loop
that pumps the WebSocket (that would deadlock: the reply can't arrive). So we
run the whole ``query()`` turn in a **worker thread**, and the permission
handler blocks that
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

#: Default agent-loop turn ceiling for an interactive session. Shared by the
#: dataclass default below and the ``--max-turns`` CLI flag (agent_server_cli.py)
#: so the two can't drift apart from independently hand-edited literals.
DEFAULT_MAX_TURNS = 50

_SHUTDOWN = object()  # sentinel pushed onto the worker inbox to stop it


@dataclass
class AgentServerConfig:
    """Static configuration for an agent-server (one per process/server)."""

    provider_name: str | None = None
    model: str | None = None
    # ch04 round-4 GAP B — capacity-relief model after repeated 529s
    # (`--fallback-model`; session-sticky, never persisted).
    fallback_model: str | None = None
    permission_mode: str = "default"
    # bypassPermissions AVAILABILITY, decoupled from the launch mode: True when
    # the user passed --dangerously-skip-permissions / --allow-dangerously-
    # skip-permissions to the launcher. Availability is what lets Shift+Tab
    # cycling and set_permission_mode reach bypassPermissions at runtime;
    # launching IN bypass mode implies it (see _build_runtime). Mirrors
    # isBypassPermissionsModeAvailable in
    # typescript/src/utils/permissions/permissionSetup.ts:941.
    is_bypass_available: bool = False
    max_turns: int = DEFAULT_MAX_TURNS
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    permission_timeout_s: float = DEFAULT_PERMISSION_TIMEOUT_S
    # ch02 round-4 (critic B1): True only on the --stdio transport, which
    # serves exactly one session (the Ink client's spawned child). Gates
    # the process-global side effects in _build_runtime (post-trust env
    # apply, context-cache prefetch) that would bleed across sessions on
    # the multi-session --http transport, where sessions carry
    # client-supplied cwds and over-strict is the safe direction.
    single_session: bool = False


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
    # ch03 round-4 GAP A — per-session reactive AppState store (the book's
    # §3.2 tier). Attached only on single-session transports; None on
    # --http, where the centralized on_change side effects (user-level
    # settings persistence) must not fire from client-supplied sessions.
    app_state_store: Any = None
    # ch05 round-4 GAP A — SESSION-scoped auto-compact tracking: the
    # 3-consecutive-failures circuit breaker counts across turns (the
    # engine's engine.py:74-79 rationale); a per-turn instance would reset
    # it every prompt. Created lazily on first turn.
    _auto_compact_tracking: Any = None
    # ch11 round-4 WI-1 — SESSION-scoped set of already-surfaced memory
    # paths, so the LLM recall doesn't re-inject the same memory every turn.
    _memory_surfaced: set = field(default_factory=set)
    # ch12 round-4 WI-3 — SessionStart fires once, lazily, before the first
    # real turn (inside the async context; _build_runtime runs sync in an
    # executor with no live loop). Guarded so it fires exactly once.
    _session_start_fired: bool = False
    # Completed user turns — the "turns: N" odometer on the client's session
    # stats line (the deleted REPL's ``_stats_turns``, repl/core.py). Counts
    # successful non-internal, non-btw turns; /resume seeds it from the
    # restored conversation, /clear zeroes it, /rewind recomputes it.
    _stats_turns: int = 0
    session: Any = None
    system_prompt: Any = "You are a helpful assistant."
    _base_system_prompt: Any = None  # system prompt before the /plan section is composed in
    _language: Any = None  # preferred response language (the original's LanguagePicker, §6)
    _thinking: Any = None  # extended-thinking override (ThinkingToggle); None = model default
    init_error: str | None = None
    _session_name: str | None = None  # user-set label (/rename) shown in /resume
    _mcp_runtime: Any = None  # McpRuntime (connected MCP servers) when configured
    _effort: str | None = None  # /effort reasoning level, injected via extra_body when set
    _knowledge: Any = None  # KnowledgeGraph (lazy-loaded), populated at each turn end
    _knowledge_enabled: bool = True  # the original's knowledgeGraphEnabled (default on)
    _knowledge_semantic: bool = False  # opt-in model-based extraction (vs heuristic)
    _bgtasks: Any = None  # BackgroundTasks registry (lazy), the original's Ctrl+B runs
    # /goal — session-scoped completion-condition loop (src/goals). Built
    # lazily by _goal_manager(); the worker's post-turn hook evaluates it and
    # enqueues continuation turns. Persisted in the session file for /resume.
    _goal_mgr: Any = None
    # Monotonic goal-state capture counter (see _goal_snapshot_locked).
    _goal_rev: int = 0

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
        """Emit ``system/init`` — the first message the client sees on connect.

        Re-emitted after a resume-driven coordinator-mode flip (_do_resume) so
        the client's cached tool list tracks the mode."""
        # Coordinator mode narrows the MAIN loop's advertised tools to the
        # orchestration set; workers keep the full captured registry. Fresh
        # per call — see coordinator_main_loop_registry.
        from src.coordinator.mode import coordinator_main_loop_registry

        tools = _tool_schemas(coordinator_main_loop_registry(self.tool_registry))
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
            content = _extract_prompt_content(msg)  # str, or blocks for multimodal
            mm = msg.get("message")
            ephemeral = bool(msg.get("ephemeral") or (isinstance(mm, dict) and mm.get("ephemeral")))
            self._inbox.put({"__btw__": True, "content": content} if ephemeral else content)
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
        # A session that refused to start (``init_error`` — e.g. the sandbox
        # HARD GATE) must not service control requests that actually DO work.
        # ``bg_run``/``bg_agent`` (/bg, /bg-agent) spawn subprocesses via
        # ``_do_bgtask`` → ``subprocess.Popen(shell=True)`` OUTSIDE
        # ``_build_runtime`` and the turn path — so without this guard they'd
        # run UNSANDBOXED under a hard-gate config the session was supposed to
        # refuse (critic C8). ``interrupt`` is exempt (a benign abort of a
        # non-existent turn). Mirrors the ``session not ready`` pattern the
        # permission-mode handlers already use.
        if self.init_error is not None and subtype != "interrupt":
            self._reply(request_id, {"ok": False, "error": self.init_error})
            return
        if subtype == "interrupt":
            with self._lock:
                abort = self._current_abort
                pendings = list(self._pending.values())
                # ESC during a goal run auto-pauses the goal (donor
                # semantics, critic R4): without this, the interrupted turn
                # skips its continuation but the goal stays armed and the
                # loop silently resurrects at the end of the user's next
                # turn. /goal resume re-arms deliberately. Deviation from
                # CC (which keeps the goal active) — documented.
                goal_paused = False
                goal_snapshot = None
                goal_rev = 0
                if self._goal_mgr is not None and self._goal_mgr.is_active():
                    try:
                        self._goal_mgr.pause(reason="interrupted (ESC)")
                        goal_paused = True
                        goal_snapshot, goal_rev = self._goal_snapshot_locked()
                    except Exception:  # noqa: BLE001
                        # No event either — the indicator keeps showing
                        # "active", which is then TRUE: an unpaused goal
                        # resurrects the loop at the next completed turn.
                        logger.debug("[agent-server] goal pause on interrupt failed",
                                     exc_info=True)
            # Release any in-flight permission ask NOW so the worker unblocks
            # immediately rather than at permission_timeout_s (proposal §7: ESC
            # during a permission prompt must both deny the pending ask AND
            # abort the turn). Mirrors shutdown()'s deny-release.
            for pending in pendings:
                pending.reply = {"behavior": "deny", "message": "interrupted"}
                pending.event.set()
            if abort is not None:
                abort.abort("user_interrupt")
            if goal_paused:
                self._save_session()
                self._emit({
                    "type": "system",
                    "subtype": "goal_status",
                    "session_id": self.session_id,
                    "message": ("⏸ Goal paused — turn interrupted. Use "
                                "/goal resume to continue, or /goal clear to stop."),
                    "goal_active": False,
                    "goal": goal_snapshot,
                    "goal_rev": goal_rev,
                })
            return
        if subtype == "set_permission_mode":
            mode = inner.get("mode")
            # Validate BEFORE setting: an unknown string would land verbatim in
            # permission_context.mode and silently behave like a mode it isn't.
            # 'bubble' is runtime-only sub-agent escalation (rejected as a
            # top-level mode everywhere else, e.g. agent_server_cli).
            from src.permissions.types import PERMISSION_MODES

            if (
                not isinstance(mode, str)
                or mode not in PERMISSION_MODES
                or mode == "bubble"
            ):
                self._reply(request_id, {
                    "ok": False,
                    "error": f"invalid permission mode: {mode!r} "
                             "(default | plan | acceptEdits | bypassPermissions "
                             "| dontAsk | auto)",
                })
                return
            if self.tool_context is None:
                self._reply(request_id, {"ok": False, "error": "session not ready"})
                return
            # bypassPermissions is only settable when the session made it
            # available (--dangerously-skip-permissions / --allow-…). Same
            # guard the Shift+Tab cycle enforces (get_next_permission_mode) —
            # without it, /mode bypassPermissions silently disabled the whole
            # permission gate in any session. Mirrors the onSetPermissionMode
            # contract in typescript/src/bridge/replBridge.ts:182-193.
            pc = self.tool_context.permission_context
            if mode == "bypassPermissions" and not getattr(
                pc, "is_bypass_permissions_mode_available", False
            ):
                self._reply(request_id, {
                    "ok": False,
                    "error": "bypassPermissions is not available in this "
                             "session — launch with "
                             "--dangerously-skip-permissions or "
                             "--allow-dangerously-skip-permissions",
                })
                return
            _set_mode(self.tool_context, mode)
            # ch03 round-4 GAP A: the live gate home stays
            # tool_context.permission_context; the store dispatch runs
            # the centralized seams (listeners; future persistence).
            _dispatch_app_state(self, permission_mode=mode)
            self._reply(request_id, {"ok": True, "mode": mode})
            return
        if subtype == "cycle_permission_mode":
            # ch13 round-4 (critic B1) — shift+tab cycling MUST be computed
            # server-side from the LIVE mode via the guarded
            # get_next_permission_mode (bypassPermissions only when
            # is_bypass_permissions_mode_available). A client cursor
            # hardcoding the cycle both desyncs after /mode (M2) and would
            # step into bypass unconditionally, silently disabling the whole
            # permission gate. The server owns the mode + the availability
            # flag, so it owns the next-mode computation.
            if self.tool_context is None:
                self._reply(request_id, {"ok": False, "error": "session not ready"})
                return
            from src.permissions.cycle import get_next_permission_mode

            pc = self.tool_context.permission_context
            new_mode = get_next_permission_mode(pc)
            _set_mode(self.tool_context, new_mode)
            _dispatch_app_state(self, permission_mode=new_mode)
            self._reply(request_id, {"ok": True, "mode": new_mode})
            return
        if subtype == "set_model":
            self._do_set_model(request_id, inner.get("model"), inner.get("provider"))
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
        if subtype == "set_thinking":
            self._do_set_thinking(request_id, inner.get("action"))
            return
        if subtype == "set_mcp_enabled":
            self._do_set_mcp_enabled(request_id, inner.get("server"), inner.get("enabled"))
            return
        if subtype == "mcp_auth":
            await self._do_mcp_auth(request_id, inner.get("server"))
            return
        if subtype == "external_includes":
            # External CLAUDE.md @-imports (ClaudeMdExternalIncludesDialog, §6).
            try:
                from src.services.startup_gates import get_external_includes_state, list_external_includes

                externals = await list_external_includes(self.cwd)
                state = get_external_includes_state(self.cwd)
            except Exception:  # noqa: BLE001
                externals, state = [], "unset"
            self._reply(request_id, {"state": state, "externals": externals})
            return
        if subtype == "set_external_includes":
            try:
                from src.services.startup_gates import record_external_includes_choice

                ok = record_external_includes_choice(bool(inner.get("approved")), self.cwd)
            except Exception:  # noqa: BLE001
                ok = False
            self._reply(request_id, {"ok": ok})
            return
        if subtype == "set_effort":
            self._do_set_effort(request_id, inner.get("effort"))
            return
        if subtype == "workflows":
            self._do_workflows(request_id)
            return
        if subtype == "list_workflow_commands":
            self._do_list_workflow_commands(request_id)
            return
        if subtype == "workflow_command":
            self._do_workflow_command(request_id, inner.get("name"), inner.get("args"))
            return
        if subtype == "get_settings":
            self._reply(request_id, {
                "permission_mode": _current_mode(self.tool_context, self.config.permission_mode),
                "model": getattr(self.provider, "model", None),
                "provider": self.provider_name,
                "available_models": self._available_models(),
                # OS-1 W3 — the /output-style no-arg display.
                "output_style": getattr(self.tool_context, "output_style_name", None) or "default",
                "available_output_styles": self._available_output_styles(),
            })
            return
        if subtype == "get_context_usage":
            self._reply(request_id, self._context_usage())
            return
        if subtype == "cost":
            self._reply(request_id, _cost_snapshot())
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
                # Settings scope beats the loader bucket so disk skills split
                # into user/project/managed; everything else keeps its
                # loaded_from bucket (bundled/plugin/mcp/…).
                scope_names = {
                    "userSettings": "user",
                    "projectSettings": "project",
                    "policySettings": "managed",
                }
                # Cap raised 120 → 1000: the TUI skills hub groups the full
                # set by category, so a tight cap would skew its counts.
                for s in all_s[:1000]:
                    source = str(getattr(s, "source", "") or "")
                    loaded_from = str(getattr(s, "loaded_from", "") or "")
                    skills.append({
                        "name": getattr(s, "name", "") or "",
                        "description": str(getattr(s, "description", "") or "")[:400],
                        "category": scope_names.get(source) or loaded_from or source or "other",
                        "path": str(getattr(s, "skill_root", None) or getattr(s, "base_dir", None) or ""),
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
            reg = self.tool_registry
            disabled = reg.disabled_servers if reg is not None else set()
            servers = (
                [{"name": n, "tools": tools, "enabled": n not in disabled} for n, tools in rt.servers.items()]
                if rt is not None
                else []
            )
            self._reply(request_id, {"servers": servers})
            return
        if subtype == "goal":
            self._do_goal_command(request_id, inner.get("arg"))
            return
        if subtype == "subgoal":
            self._do_subgoal_command(request_id, inner.get("arg"))
            return
        if subtype == "advisor":
            self._do_advisor_command(request_id, inner.get("arg"))
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
                # /clear removes an active goal (CC docs/en/goal §Clear a
                # goal: "Running /clear to start a new conversation also
                # removes any active goal"). Under _lock — the worker's
                # post-turn hook shares this state. The on-disk strip makes
                # the clear DURABLE: /clear + immediate quit must not leave
                # an active goal in the session file for --resume to
                # restore (critic suggestion 2; _save_session can't do it —
                # it early-returns on the now-empty conversation).
                if self._goal_mgr is not None:
                    try:
                        with self._lock:
                            self._goal_mgr.clear()
                        f = _sessions_dir() / f"{self.session_id}.json"
                        if f.exists():
                            data = json.loads(f.read_text(encoding="utf-8"))
                            if data.pop("goal", None) is not None:
                                f.write_text(json.dumps(data), encoding="utf-8")
                    except Exception:  # noqa: BLE001 — never break /clear
                        logger.debug("[agent-server] goal clear on /clear failed",
                                     exc_info=True)
                # Fresh conversation, fresh odometer (token/cost totals are
                # process-wide spend and deliberately survive /clear).
                self._stats_turns = 0
                # Indicator rider (critic R1): only a SUCCESSFUL clear may
                # hide the client's goal indicator — a rejected /clear
                # (active turn) reply carries no `goal` field and the
                # client leaves the indicator alone.
                with self._lock:
                    goal_snapshot, goal_rev = self._goal_snapshot_locked()
                self._reply(request_id, {
                    "ok": True,
                    "count": 0,
                    # Stats-line refresh rider (same shape as the resume
                    # reply): turns reset with the conversation, spend stays.
                    "session_turns": 0,
                    "cost": _cost_snapshot(),
                    "goal": goal_snapshot,
                    "goal_rev": goal_rev,
                })
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

    def _available_output_styles(self) -> list[str]:
        """OS-1 W3 — builtins ∪ user styles for the /output-style listing."""
        try:
            from src.outputStyles import available_output_styles

            return available_output_styles(
                getattr(self.tool_context, "output_style_dir", None)
                if self.tool_context is not None
                else None
            )
        except Exception:  # noqa: BLE001
            return ["default", "explanatory"]

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
            # Scoped so a (theoretical) import failure costs only the mode
            # stamp, never the whole best-effort save.
            mode_value = "normal"
            try:
                from src.coordinator.mode import is_coordinator_mode

                if is_coordinator_mode():
                    mode_value = "coordinator"
            except Exception:  # noqa: BLE001
                pass

            payload = {
                "session_id": self.session_id,
                "model": getattr(self.provider, "model", None) or self.config.model or "",
                # ch03 round-4 (critic B1): the provider the model belongs
                # to — _do_resume's model restore is gated on it matching
                # the current provider, the same cross-provider hazard the
                # settings-seed guards (a stale model fired at the wrong
                # endpoint 400s and would self-persist the bad pairing).
                "provider": self.provider_name,
                "cwd": self.cwd,
                "updated_at": time.time(),
                "message_count": len(msgs),
                "preview": _first_prompt_preview(msgs),
                "name": self._session_name,
                # Coordinator-mode stamp so _do_resume can re-enter/exit the
                # mode (TS saveMode, sessionStorage.ts:3126). Stamped at every
                # turn-end save — subsumes TS's materialize/exit//clear
                # re-stamp sites.
                "mode": mode_value,
                "conversation": self.session.conversation.to_dict(),
                # Turns odometer — _do_resume prefers this exact counter over
                # recounting the conversation (which can't tell a real prompt
                # from a persisted notification/hook-context message).
                "turns": self._stats_turns,
            }
            # /goal state rides the session file so --resume restores an
            # ACTIVE goal (CC docs/en/goal §Resume). Snapshot under _lock —
            # the worker's post-turn hook mutates the same state.
            if self._goal_mgr is not None:
                try:
                    with self._lock:
                        goal_state = self._goal_mgr.state
                        goal_dict = goal_state.to_dict() if goal_state else None
                    if goal_dict:
                        payload["goal"] = goal_dict
                except Exception:  # noqa: BLE001 — goal snapshot is best-effort
                    logger.debug("[agent-server] goal snapshot failed",
                                 exc_info=True)
            # ch03 round-4 GAP B — the live persister carries the cost
            # block (schema owner: cost_restore.build_cost_block, matching
            # the /resume reader) so accumulated cost survives restarts.
            # single_session-gated for the same reason as the restore
            # (critic m1): bootstrap totals are process-global, so on a
            # multi-session --http server the block would record the SUM
            # of all sessions' cost under one session's file.
            if self.config.single_session:
                try:
                    from src.services.cost_restore import build_cost_block

                    payload["cost"] = build_cost_block()
                except Exception:  # noqa: BLE001 — cost snapshot is best-effort
                    logger.debug("[agent-server] cost snapshot failed", exc_info=True)
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

    def _do_set_model(self, request_id: object, model: object, provider: object = None) -> None:
        """Switch the active model (the /model picker + typed /model). Replies
        {ok, model, warning?} — the TUI's ConfigSetResponse contract needs the
        resulting model echoed back as proof the switch happened; a bare ack
        reads as failure client-side. ``provider`` (when sent) must match the
        session's provider: cross-provider switches need the full registry
        rebuild that set_provider does, so refusing here beats silently
        pointing the current provider at a foreign model id."""
        if not isinstance(model, str) or not model.strip():
            self._reply(request_id, {"ok": False, "error": "missing model"})
            return
        model = model.strip()
        if self.provider is None:
            self._reply(request_id, {"ok": False, "error": "session not ready"})
            return
        if isinstance(provider, str) and provider and provider != self.provider_name:
            self._reply(request_id, {
                "ok": False,
                "error": f"model '{model}' expects provider '{provider}' but this "
                         f"session is on '{self.provider_name}'",
            })
            return
        try:
            self.provider.model = model
        except Exception as exc:  # noqa: BLE001
            self._reply(request_id, {"ok": False, "error": f"model switch failed: {exc}"})
            return
        # ch03 round-4 GAP A: on_change mirrors the choice into bootstrap and
        # persists (model, model_provider) to user settings — /model survives
        # restarts.
        _dispatch_app_state(self, main_loop_model=model)
        response: dict = {"ok": True, "model": getattr(self.provider, "model", model)}
        known = self._available_models()
        if known and model not in known:
            response["warning"] = (
                f"'{model}' is not in {self.provider_name}'s model list — "
                "the API may reject it"
            )
        self._reply(request_id, response)

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
            # ch03 round-4 GAP A: keep the persisted (model, model_provider)
            # pair coherent across a provider switch — the supplier reads
            # self.provider_name, updated above, so on_change persists the
            # new pairing.
            _dispatch_app_state(self, main_loop_model=model)
            # INTEG-1 warm-on-activation (the refreshStartupDiscoveryForActiveRoute
            # analog, discoveryService.ts:415): one non-blocking
            # get_available_models call kicks the single-flight background
            # refresh at SWITCH time, so the picker's later read sees the
            # discovered list instead of the static stub. (Server init warms
            # the initial provider the same way via get_settings →
            # _available_models.)
            try:
                warm = getattr(provider, "get_available_models", None)
                if callable(warm):
                    warm()
            except Exception:  # noqa: BLE001 — warm is best-effort
                logger.debug("[agent-server] discovery warm failed", exc_info=True)
            self._reply(request_id, {"ok": True, "provider": name, "model": model or ""})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_provider failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _mcp_server_infos(self) -> list[Any] | None:
        """The connected MCP servers' info objects (name + instructions) for
        the system-prompt build, filtered by the registry's disabled set —
        a disabled server's tools are hidden, so its instructions hide too.
        ``None`` when no MCP runtime (C2 — MCP-instructions live wiring)."""
        rt = getattr(self, "_mcp_runtime", None)
        if rt is None:
            return None
        infos = list(getattr(rt, "server_infos", None) or [])
        reg = self.tool_registry
        disabled = set(getattr(reg, "disabled_servers", None) or ()) if reg is not None else set()
        live = [s for s in infos if getattr(s, "name", None) not in disabled]
        return live or None

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

    def _do_set_mcp_enabled(self, request_id: object, server: object, enabled: object) -> None:
        """Enable/disable an MCP server's tools (MCPServerMultiselectDialog). The
        registry hides disabled servers' tools from the agent; persisted globally."""
        reg = self.tool_registry
        name = str(server or "")
        if reg is not None and name:
            if enabled:
                reg.disabled_servers.discard(name)
            else:
                reg.disabled_servers.add(name)
            _save_disabled_mcp(reg.disabled_servers)
            # Re-render the MCP-instructions section for the new server set
            # (C2 — the port-idiomatic analog of TS's per-call UNCACHED
            # mcp_instructions section, given the memoized base prompt).
            self._rebuild_base_prompt_for_mcp()
        self._reply(request_id, {
            "ok": True,
            "disabled": sorted(reg.disabled_servers) if reg is not None else [],
        })

    def _rebuild_base_prompt_for_mcp(self) -> None:
        """Rebuild the memoized base prompt so a change to the live MCP server
        set (toggle, or a /mcp-auth late connect) re-renders the REQUEST-scoped
        mcp_instructions section (C2 uncached-section analog; C4 late-connect)."""
        if self._base_system_prompt is None or self.tool_context is None:
            return
        try:
            from src.outputStyles import resolve_output_style
            from src.query.agent_loop_compat import build_effective_system_prompt

            tc = self.tool_context
            style_prompt = resolve_output_style(
                getattr(tc, "output_style_name", None),
                getattr(tc, "output_style_dir", None),
            ).prompt
            self._base_system_prompt = build_effective_system_prompt(
                style_prompt, tc, provider=self.provider,
                mcp_servers=self._mcp_server_infos(),
            )
            self.system_prompt = self._compose_with_plan(self._base_system_prompt)
        except Exception:  # noqa: BLE001 — keep the change even if rebuild fails
            logger.debug("[agent-server] MCP prompt rebuild failed", exc_info=True)

    async def _do_mcp_auth(self, request_id: object, server: object) -> None:
        """/mcp auth <server> (C4): run the OAuth flow for a needs-auth MCP
        server, then register its now-available tools + rebuild the prompt so
        its instructions enter the system prompt (the C2 late-connect note).

        The blocking OAuth flow runs on the MCP runtime loop and is AWAITED via
        a wrapped future, so the agent-server MAIN loop stays responsive during
        the (up to 300s) browser round-trip — the user can still interrupt
        (B1). The registry/prompt mutations happen back on the main loop
        (single-threaded, no race with an in-flight turn)."""
        name = str(server or "")
        rt = getattr(self, "_mcp_runtime", None)
        if rt is None or not name:
            self._reply(request_id, {"ok": False, "error": "no MCP runtime or server name"})
            return
        try:
            fut = rt.submit(rt.trigger_oauth_async(name))
            result = await asyncio.wrap_future(fut)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] /mcp auth failed for %s", name)
            result = {"ok": False, "error": f"auth failed: {exc}"}
        if result.get("ok"):
            reg = self.tool_registry
            if reg is not None:
                for mtool in result.get("tools", []):
                    try:
                        reg.register(mtool)
                    except Exception:  # noqa: BLE001
                        logger.debug("[agent-server] register MCP tool failed", exc_info=True)
            # M1: a late-authed server needs the SAME elicitation +
            # tools/list_changed wiring boot-time servers get (agent_server
            # boot path) — else it silently can't elicit or push tool refreshes.
            client = result.get("client")
            if client is not None:
                self._wire_mcp_client_handlers(rt, client, name)
            self._rebuild_base_prompt_for_mcp()  # late-connect → surface instructions
        self._reply(request_id, {
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "pending_auth": rt.pending_auth(),
        })

    def _wire_mcp_client_handlers(self, rt: Any, client: Any, name: str) -> None:
        """Wire elicitation + (capability-gated) tools/list_changed handlers on
        an MCP client — the same wiring the boot path applies, reused for a
        late-authenticated server (M1)."""
        try:
            client.set_elicitation_handler(_make_elicitation_handler(self))
        except Exception:  # noqa: BLE001
            logger.debug("[agent-server] elicitation wiring failed for %s", name, exc_info=True)
        try:
            caps = getattr(client, "capabilities", None)
            if getattr(caps, "tools_list_changed", False):
                client.set_notification_handler(
                    _make_mcp_notification_handler(rt, self, name)
                )
        except Exception:  # noqa: BLE001
            logger.debug("[agent-server] list_changed wiring failed for %s", name, exc_info=True)

    def _do_set_thinking(self, request_id: object, action: object) -> None:
        """Toggle/set extended thinking (the original's ThinkingToggle). action:
        'on'|'off' set explicitly; anything else toggles. Applies next turn."""
        act = str(action or "").lower()
        if act == "on":
            self._thinking = True
        elif act == "off":
            self._thinking = False
        else:
            self._thinking = not bool(self._thinking)
        self._reply(request_id, {"ok": True, "thinking": bool(self._thinking)})

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

    # ─── workflow surfaces (control plane) ─────────────────────────────────
    # The dynamic-workflow UX used to be wired through the deleted Rich REPL /
    # Textual TUI (removed in #566); these controls are the agent-server
    # replacements the Ink TUI drives. All are gated on is_workflows_enabled()
    # (workflow-engine §4.8: the surfaces disappear when workflows are off).

    def _do_set_effort(self, request_id: object, effort: object) -> None:
        """``/effort`` backend: reasoning levels plus the ``ultracode``
        workflow auto-orchestration mode (mirrors ``effort_command.py``).

        No/empty arg ⇒ read-only report (the old picker's Esc-is-a-no-op).
        ``ultracode`` enables session mode and leaves the reasoning level
        untouched; real levels and ``auto``/``unset`` exit ultracode mode
        (spec: "reset with /effort high")."""
        try:
            from src.workflow.gating import is_workflows_enabled
            from src.workflow.ultracode import is_ultracode_session, set_ultracode_session

            if effort is None or (isinstance(effort, str) and not effort.strip()):
                # No arg ⇒ read-only report (the old picker's Esc-is-a-no-op).
                on = is_ultracode_session()
                self._reply(request_id, {
                    "ok": True,
                    "effort": "ultracode" if on else (self._effort or "default"),
                    "ultracode": on,
                })
                return
            if not isinstance(effort, str):
                self._reply(request_id, {
                    "ok": False,
                    "error": f"invalid effort '{effort}' (minimal|low|medium|high|auto|ultracode)",
                })
                return
            a = effort.strip().lower()
            if a == "ultracode":
                if not is_workflows_enabled():
                    self._reply(
                        request_id, {"ok": False, "error": "dynamic workflows are disabled"}
                    )
                    return
                set_ultracode_session(True)
                self._reply(request_id, {"ok": True, "effort": "ultracode", "ultracode": True})
                return
            if a in ("auto", "unset"):
                self._effort = None
                set_ultracode_session(False)
                self._reply(request_id, {"ok": True, "effort": "default", "ultracode": False})
                return
            if a in ("minimal", "low", "medium", "high"):
                self._effort = a
                set_ultracode_session(False)  # a real level exits ultracode mode
                self._reply(request_id, {"ok": True, "effort": a, "ultracode": False})
                return
            self._reply(request_id, {
                "ok": False,
                "error": f"invalid effort '{effort.strip()}' (minimal|low|medium|high|auto|ultracode)",
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_effort failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_workflows(self, request_id: object) -> None:
        """``/workflows``: text report of running/recent dynamic-workflow runs
        (shared renderer with the registry command — see
        ``render_workflows_report``)."""
        try:
            from src.command_system.workflows_command import (
                NO_WORKFLOW_RUNS_MESSAGE,
                render_workflows_report,
            )
            from src.workflow.gating import is_workflows_enabled

            if not is_workflows_enabled():
                self._reply(request_id, {"ok": False, "error": "dynamic workflows are disabled"})
                return
            registry = getattr(self.tool_context, "runtime_tasks", None)
            if registry is None:
                self._reply(
                    request_id,
                    {"ok": False, "error": "workflows are unavailable on this surface"},
                )
                return
            report = render_workflows_report(registry)
            self._reply(request_id, {"ok": True, "text": report or NO_WORKFLOW_RUNS_MESSAGE})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] workflows failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_list_workflow_commands(self, request_id: object) -> None:
        """Slash-menu catalog: bundled (/deep-research) + saved
        ``.claude/workflows/*.py`` workflow commands, read fresh from disk each
        call so a workflow authored mid-session (the ultracode keyword flow)
        appears without a restart — replaces the old REPL's mtime-gated
        ``_refresh_workflow_commands`` loop."""
        try:
            from src.workflow.gating import is_workflows_enabled

            if not is_workflows_enabled():
                self._reply(request_id, {"ok": True, "commands": []})
                return
            from src.command_system.types import PromptCommand
            from src.command_system.workflows_integration import load_workflow_commands

            commands = [
                {
                    "name": c.name,
                    "description": c.description or "",
                    "argument_hint": getattr(c, "argument_hint", "") or "",
                }
                for c in load_workflow_commands(self.cwd)
                if isinstance(c, PromptCommand)
            ]
            self._reply(request_id, {"ok": True, "commands": commands})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] list_workflow_commands failed")
            self._reply(request_id, {"ok": False, "error": str(exc), "commands": []})

    def _do_workflow_command(self, request_id: object, name: object, args: object) -> None:
        """Dispatch a workflow slash command (``/deep-research``, saved
        ``/<name>``): expand its directive prompt for the client to submit as a
        user turn — the model then launches the run via the Workflow tool."""
        try:
            from src.workflow.gating import is_workflows_enabled

            if not is_workflows_enabled():
                self._reply(request_id, {"ok": False, "error": "dynamic workflows are disabled"})
                return
            if not isinstance(name, str) or not name.strip():
                self._reply(request_id, {"ok": False, "error": "missing workflow command name"})
                return
            from src.command_system.argument_substitution import substitute_arguments
            from src.command_system.types import PromptCommand
            from src.command_system.workflows_integration import load_workflow_commands

            wanted = name.strip().lstrip("/").lower()
            for c in load_workflow_commands(self.cwd):
                if isinstance(c, PromptCommand) and c.name.lower() == wanted:
                    arg_str = args if isinstance(args, str) else ""
                    prompt = substitute_arguments(c.markdown_content, arg_str, c.arg_names)
                    self._reply(request_id, {
                        "ok": True,
                        "prompt": prompt,
                        "notice": f"⚡ launching workflow /{c.name}",
                    })
                    return
            self._reply(request_id, {"ok": False, "error": f"unknown workflow command '{wanted}'"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] workflow_command failed")
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
            from src.outputStyles import available_output_styles

            tc = self.tool_context
            valid = available_output_styles(
                getattr(tc, "output_style_dir", None) if tc is not None else None
            )
            # OS-1: validate against the loader's truth (builtins ∪ user
            # styles). The old fixed VALID_OUTPUT_STYLES list rejected the
            # real builtin "explanatory" and accepted three styles that
            # never existed.
            if not isinstance(style, str) or style not in valid:
                self._reply(
                    request_id,
                    {"ok": False, "error": f"invalid style (valid: {', '.join(valid)})",
                     "available_styles": valid},
                )
                return
            if tc is None:
                self._reply(request_id, {"ok": False, "error": "session not ready"})
                return
            tc.output_style_name = style
            # Rebuild the system prompt so the style section takes effect next turn.
            try:
                from src.outputStyles import resolve_output_style
                from src.query.agent_loop_compat import build_effective_system_prompt

                style_prompt = resolve_output_style(style, getattr(tc, "output_style_dir", None)).prompt
                self._base_system_prompt = build_effective_system_prompt(
                    style_prompt, tc, provider=self.provider,
                    mcp_servers=self._mcp_server_infos(),
                )
                self.system_prompt = self._compose_with_plan(self._base_system_prompt)
            except Exception:  # noqa: BLE001 - keep the style set even if rebuild is unavailable
                logger.debug("[agent-server] system prompt rebuild after set_output_style failed", exc_info=True)
            # OS-1 G3 — persist the choice (localSettings analog,
            # Settings/Config.tsx:1600). Best-effort: the in-memory switch
            # above already applies.
            try:
                from src.settings.settings import update_local_settings

                update_local_settings(
                    {"output_style": {"style": style}}, cwd=self.cwd,
                )
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] output style persist failed", exc_info=True)
            self._reply(request_id, {"ok": True, "style": style, "available_styles": valid})
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
            # Seed the turns odometer so the stats line continues where the
            # resumed session left off (its token/cost siblings restore below
            # via restore_cost_state). Prefer the exact persisted counter
            # (_save_session stamps it every turn end); fall back to counting
            # the restored conversation for pre-"turns" session files — an
            # approximation: notification prompts, hook-injected context and
            # aborted-turn prompts persist as plain user messages, so the
            # recount can run high vs the live success-only rule.
            saved_turns = data.get("turns")
            self._stats_turns = (
                saved_turns
                if isinstance(saved_turns, int) and not isinstance(saved_turns, bool) and saved_turns >= 0
                else _count_prompt_turns(conv.messages)
            )
            # ch03 round-4 GAP B — restore the accumulated cost counters
            # (guarded: the reader refuses a file whose session_id header
            # doesn't match). Gated single_session like every other
            # process-global write: bootstrap cost totals are one set per
            # process, and a multi-session --http server must not let one
            # session's resume overwrite another's accounting.
            if self.config.single_session:
                try:
                    from src.services.cost_restore import (
                        restore_cost_state_for_session,
                    )

                    restore_cost_state_for_session(session_id)
                except Exception:  # noqa: BLE001 — restore is best-effort
                    logger.debug("[agent-server] cost restore failed",
                                 exc_info=True)
            # Restore the session's saved model choice under the same
            # precedence as startup seeding: an explicit launch model
            # (cfg.model) wins; otherwise the resumed session's model is
            # what the user was using — put it back on the provider and
            # through the store (persists the pairing via on_change).
            # Provider-match guard (critic B1): the saved model applies
            # ONLY when it was saved under the CURRENT provider — the
            # same rule as seed_app_state_from_settings. Without it, a
            # cross-provider resume fires a stale model at the wrong
            # endpoint AND the store dispatch would persist the bad
            # (model, provider) pairing, poisoning every later launch.
            # Old session files without a "provider" field never match —
            # fail-safe.
            saved_model = data.get("model")
            saved_provider = data.get("provider")
            if (
                isinstance(saved_model, str) and saved_model
                and self.config.model is None
                and self.provider is not None
                and saved_provider == self.provider_name
            ):
                try:
                    self.provider.model = saved_model
                except Exception:  # noqa: BLE001
                    pass
                _dispatch_app_state(self, main_loop_model=saved_model)
            # Coordinator-mode sync (TS matchSessionMode at every resume
            # surface — sessionRestore.ts:429, print.ts:4909/5114). Absent
            # field (old session files) or junk value → None → no-op.
            #
            # single_session-gated like cost-restore above and for the same
            # reason: match_session_mode flips the process-global env var
            # (coordinator mode is inherently process-scoped — TS runs one
            # process per session via bridge/sessionRunner, so the env-var
            # design never meets a multi-session process there). On a
            # multi-session --http server, one session's resume must not
            # flip the mode — and thereby the prompt + tool set — of every
            # sibling session. Fail-safe: --http never enters/exits
            # coordinator mode via resume; the launch env decides for the
            # whole process.
            saved_mode = data.get("mode")
            mode_banner = None
            if self.config.single_session:
                try:
                    from src.coordinator.mode import match_session_mode

                    mode_banner = match_session_mode(
                        saved_mode if saved_mode in ("coordinator", "normal") else None
                    )
                except Exception:  # noqa: BLE001 — mode sync must not break resume
                    logger.debug("[agent-server] session-mode sync failed", exc_info=True)
            if mode_banner:
                # The flip changes the system prompt and the advertised tool
                # set. The prompt is CACHED (_base_system_prompt, built at
                # startup and by set_output_style) — rebuild it with the same
                # idiom; the tool list was sent once in system/init — re-emit
                # so the client's cached list tracks the mode (the client's
                # init handler re-sets session info idempotently).
                try:
                    from src.outputStyles import resolve_output_style
                    from src.query.agent_loop_compat import build_effective_system_prompt

                    tc = self.tool_context
                    if tc is not None:
                        style_prompt = resolve_output_style(
                            getattr(tc, "output_style_name", None),
                            getattr(tc, "output_style_dir", None),
                        ).prompt
                        self._base_system_prompt = build_effective_system_prompt(
                            style_prompt, tc, provider=self.provider,
                            mcp_servers=self._mcp_server_infos(),
                        )
                        self.system_prompt = self._compose_with_plan(self._base_system_prompt)
                except Exception:  # noqa: BLE001 - keep the resume even if rebuild fails
                    logger.debug(
                        "[agent-server] system prompt rebuild after mode flip failed",
                        exc_info=True,
                    )
                try:
                    self.emit_init()
                except Exception:  # noqa: BLE001
                    logger.debug("[agent-server] init re-emit after mode flip failed", exc_info=True)
            # /goal restore (CC docs/en/goal §Resume with an active goal):
            # only an ACTIVE goal carries over; turn count, timer, and the
            # token-spend baseline reset. Achieved/cleared goals stay gone.
            goal_notice = None
            saved_goal = data.get("goal")
            if isinstance(saved_goal, dict):
                try:
                    mgr = self._goal_manager()
                    snapshot_now = _cost_snapshot()
                    with self._lock:
                        restored = mgr.restore(saved_goal)
                        if restored is not None:
                            mgr.rebaseline(
                                tokens=self._usage_token_total(snapshot_now),
                                cost_usd=float(
                                    snapshot_now.get("total_cost_usd", 0.0) or 0.0
                                ),
                            )
                        restored_snapshot, restored_rev = self._goal_snapshot_locked()
                    if restored is not None:
                        goal_notice = (
                            f"◎ Goal restored (counters reset): {restored.goal}\n"
                            "I'll keep working toward it after your next "
                            "message. /goal clear to stop."
                        )
                        self._emit({
                            "type": "system",
                            "subtype": "goal_status",
                            "session_id": self.session_id,
                            "message": goal_notice,
                            "goal_active": True,
                            "goal": restored_snapshot,
                            "goal_rev": restored_rev,
                        })
                except Exception:  # noqa: BLE001 — a bad goal record must not break resume
                    logger.debug("[agent-server] goal restore failed",
                                 exc_info=True)
            self._reply(request_id, {
                "ok": True,
                "count": len(conv.messages),
                "preview": data.get("preview", ""),
                # Session-stats seed for the client's stats line — the next
                # result message is potentially a whole turn away, so the
                # reply carries the authoritative odometer + totals now.
                "session_turns": self._stats_turns,
                "cost": _cost_snapshot(),
                **({"mode_banner": mode_banner} if mode_banner else {}),
                **({"goal_notice": goal_notice} if goal_notice else {}),
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
            # Rewound turns leave the odometer — recount from what's left.
            # NOTE: `is_prompt` above counts isMeta text messages (rewind
            # boundaries pre-date the odometer); _count_prompt_turns excludes
            # them, so the recount can sit below the number of boundaries
            # rewind saw. Fine for an odometer; don't reuse is_prompt here.
            self._stats_turns = _count_prompt_turns(msgs)
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
            from src.coordinator.mode import coordinator_main_loop_registry

            data = analyze_context(
                conversation_api_messages=messages,
                model=model,
                system_prompt=self._system_prompt_text(),
                # Coordinator-filtered view: token accounting must reflect
                # the tool schemas actually sent on the wire.
                tool_schemas=_tool_schemas(coordinator_main_loop_registry(self.tool_registry)),
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
        # ch12 round-4 WI-3 — PreCompact hook fires BEFORE the summarize
        # call (TS commands/compact/compact.ts:160). Configured PreCompact
        # hooks (e.g. persist state before compaction) never ran because
        # the router had no live caller.
        try:
            from src.hooks.session_hooks import run_compact_hooks

            await run_compact_hooks(
                session_id=self.session_id, trigger="manual",
                tool_use_context=self.tool_context,
            )
        except Exception:  # noqa: BLE001 — a hook must not block compaction
            logger.debug("[agent-server] PreCompact hooks failed", exc_info=True)
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
            # R5 round-5 (ch11 #3) — compaction drops the earlier conversation
            # (and with it any memory the model had seen), so reset the
            # recall de-dup set: memories surfaced pre-compaction become
            # eligible again. Without this the monotonic set silently
            # degrades recall on long sessions (a memory recalled once is
            # never re-surfaced even after its context is compacted away).
            self._memory_surfaced.clear()
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

        # ch13 round-4 — forward the permission SUGGESTIONS (the "always
        # allow Bash(ls:*)" rule options) so the TUI can offer a persistable
        # choice. Previously only tool_name/input crossed the wire, so the
        # client hardcoded a generic "always allow" with no rule attached
        # and the choice was dropped — the user re-approved every turn AND
        # session while the UI falsely reported "approved (always)".
        self._emit({
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": getattr(request, "tool_name", ""),
                "input": getattr(request, "tool_input", None) or {},
                "tool_use_id": None,
                "suggestions": [
                    _serialize_permission_update(u)
                    for u in (getattr(request, "suggestions", None) or ())
                ],
                # Authoritative per-tool wording for the persist option, e.g.
                # "allow all edits during this session" for a file edit vs.
                # "and don't ask again for <rule>" for Bash — so the box states
                # the real grant scope instead of a generic "don't ask again for
                # <tool>". Mirrors the original's tool-specific option text.
                "session_label": _session_option_label_safe(request),
                # Destructive-command caution (e.g. "Note: may overwrite
                # remote history") — rendered as a warning line in the
                # approval box, mirroring the original's dialog warning.
                "warning": _permission_request_warning(request),
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
            # ch13 round-4 — read the user's chosen "don't ask again" rules
            # back off the reply and return them so handle_permission_ask /
            # the can_use_tool adapter PERSIST them (registry.py:169 →
            # _apply_and_persist_updates → settings). This is what makes
            # "always allow" actually stick.
            chosen_raw = reply.get("chosen_updates") or reply.get("chosenUpdates")
            chosen: tuple = ()
            if isinstance(chosen_raw, list):
                deserialized = [
                    _deserialize_permission_update(u)
                    for u in chosen_raw if isinstance(u, dict)
                ]
                chosen = tuple(u for u in deserialized if u is not None)
            return PermissionAskReply(
                behavior="allow",
                updated_input=updated if isinstance(updated, dict) else None,
                chosen_updates=chosen,
            )
        return PermissionAskReply(
            behavior="deny", message=str(reply.get("message", "")) or "denied by user"
        )

    # ─── /goal — completion-condition loop (src/goals) ─────────────────────

    def _goal_manager(self) -> Any:
        """The session's GoalManager, built lazily. Never raises."""
        if self._goal_mgr is None:
            from src.goals import DEFAULT_GOAL_MAX_TURNS, GoalManager

            max_turns = DEFAULT_GOAL_MAX_TURNS
            try:
                from src.settings.settings import get_settings

                configured = int(getattr(get_settings(), "goal_max_turns", 0) or 0)
                if configured > 0:
                    max_turns = configured
            except Exception:  # noqa: BLE001 — settings must not block /goal
                logger.debug("[agent-server] goal_max_turns read failed",
                             exc_info=True)
            self._goal_mgr = GoalManager(
                self.session_id, default_max_turns=max_turns,
            )
        # Judge rebound on every call so a mid-goal /model or /provider
        # switch is picked up (the callable closes over the provider object).
        try:
            from src.goals import build_judge_callable

            self._goal_mgr.judge = build_judge_callable(self.provider)
        except Exception:  # noqa: BLE001
            logger.debug("[agent-server] goal judge bind failed", exc_info=True)
        return self._goal_mgr

    def _goal_snapshot_locked(self) -> tuple[dict[str, Any] | None, int]:
        """Compact goal state for the TUI's persistent indicator
        (``◎ /goal active (14s)``). Call with ``_lock`` HELD — reads the
        same state the worker's post-turn hook mutates.

        Returns ``(snapshot, rev)``. Only active|paused states have an
        indicator; done/cleared return None so the client hides it.
        ``created_at`` is epoch seconds — the client owns the ticking
        elapsed display.

        ``rev`` is a per-session monotonic capture counter (critic R2):
        captures are serialized by ``_lock``, so rev order == state order —
        but the wire is enqueue order (``_save_session`` file IO sits
        between capture and emit, and the client's control-reply promise
        resolution can reorder against same-chunk events). The client
        applies a carrier only when its rev is newer, so a stale "active"
        can never clobber a fresher paused/done/cleared.
        """
        self._goal_rev += 1
        mgr = self._goal_mgr
        state = mgr.state if mgr is not None else None
        if state is None or state.status not in ("active", "paused"):
            return None, self._goal_rev
        return {
            "status": state.status,
            "goal": state.goal,
            "created_at": state.created_at,
            "turns_used": state.turns_used,
            "max_turns": state.max_turns,
        }, self._goal_rev

    def _goal_set_gate(self) -> str | None:
        """CC docs/en/goal §Requirements: /goal needs an accepted trust
        dialog and the hooks framework enabled — "the command tells you why
        instead of silently doing nothing". Returns the reason, or None."""
        if not getattr(self.tool_context, "workspace_trusted", False):
            return (
                "/goal requires a trusted workspace (the evaluator is part "
                "of the hooks system). Accept the trust dialog for this "
                "workspace, then set the goal again."
            )
        try:
            from src.settings.settings import load_settings

            if not load_settings(cwd=self.cwd).hooks.enabled:
                return (
                    "/goal is unavailable because hooks are disabled "
                    "(settings hooks.enabled=false — the evaluator is part "
                    "of the hooks system)."
                )
        except Exception:  # noqa: BLE001 — unreadable settings fail open (enabled)
            logger.debug("[agent-server] hooks.enabled read failed",
                         exc_info=True)
        return None

    @staticmethod
    def _usage_token_total(snapshot: dict) -> int:
        """Total input+output tokens across the cost snapshot's model_usage."""
        total = 0
        try:
            for usage in (snapshot.get("model_usage") or {}).values():
                total += int(usage.get("input_tokens", 0) or 0)
                total += int(usage.get("output_tokens", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0
        return total

    def _do_goal_command(self, request_id: object, arg: object) -> None:
        """Control handler for /goal. Allowed while a turn is RUNNING —
        /goal clear must be able to stop a runaway loop (the /clear control
        is idle-only, so it can't)."""
        try:
            from src.goals.command import run_goal_command

            mgr = self._goal_manager()
            snapshot = _cost_snapshot()
            # Under _lock: the worker's post-turn hook reads/mutates the
            # same state (critic R1). run_goal_command is pure state ops —
            # no I/O — so the critical section is short.
            with self._lock:
                result = run_goal_command(
                    mgr,
                    str(arg or ""),
                    set_gate=self._goal_set_gate,
                    baseline_tokens=self._usage_token_total(snapshot),
                    baseline_cost_usd=float(snapshot.get("total_cost_usd", 0.0) or 0.0),
                )
                goal_snapshot, goal_rev = self._goal_snapshot_locked()
            self._save_session()
            reply: dict[str, Any] = {
                "ok": result.ok,
                "text": result.text,
                "active": result.active,
                # Indicator feed — None means "no indicator" (cleared/done).
                "goal": goal_snapshot,
                "goal_rev": goal_rev,
            }
            if result.kickoff:
                reply["notice"] = result.notice
                reply["kickoff"] = result.kickoff
            if not result.ok:
                reply["error"] = result.text
            self._reply(request_id, reply)
        except Exception as exc:  # noqa: BLE001 — a goal bug must not kill the control channel
            logger.exception("[agent-server] goal command failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_subgoal_command(self, request_id: object, arg: object) -> None:
        try:
            from src.goals.command import run_subgoal_command

            mgr = self._goal_manager()
            with self._lock:
                result = run_subgoal_command(mgr, str(arg or ""))
                goal_snapshot, goal_rev = self._goal_snapshot_locked()
            self._save_session()
            reply: dict[str, Any] = {
                "ok": result.ok,
                "text": result.text,
                "active": result.active,
                "goal": goal_snapshot,
                "goal_rev": goal_rev,
            }
            if not result.ok:
                reply["error"] = result.text
            self._reply(request_id, reply)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] subgoal command failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_advisor_command(self, request_id: object, arg: object) -> None:
        """Control handler for /advisor — configure the reviewer model.

        Bridges to the command-system implementation (advisor_command_call),
        which owns the full grammar: bare status query, ``<provider>:<model>
        [--client]``, ``--client`` / ``--no-client`` alone, ``off`` /
        ``unset``. The command reads only ``provider`` (mode decision +
        main-loop model) and ``app_state_store`` off the context; the
        remaining CommandContext fields are required positionally but
        unused here.

        ``app_state_store`` is deliberately None even though single-session
        transports carry one: ``seed_app_state_from_settings`` doesn't seed
        the advisor fields, so a store-preferred read is blind to config
        persisted by a prior session ("/advisor" reports not-set while the
        advisor fires), and a store-preferred ``off`` write is swallowed by
        the persistence handlers' equality-skip (defaults → defaults).
        With no store, every helper reads AND writes user settings directly
        (+ cache invalidation) — the same channel the query layer's
        activation check reads (src/query/query.py).

        Reply shape: transport-level ``ok`` is True whenever the command
        ran; command-level rejections (unknown provider, bad grammar) ride
        ``text`` like every other command output. Only an exception or the
        multi-session gate produces ``ok: False`` + ``error``.
        """
        # /advisor persists user-level settings (~/.clawcodex/config.json),
        # and the query layer reads them globally — on the multi-session WS
        # transport one client's /advisor would flip the advisor for every
        # session on this host. Same gate as the other user-settings writers
        # (the app-state store is only wired when single_session).
        if not self.config.single_session:
            self._reply(request_id, {
                "ok": False,
                "error": "/advisor is only available on single-session "
                         "(stdio) transports — it persists user-level "
                         "settings.",
            })
            return
        try:
            from src.command_system.builtins import advisor_command_call
            from src.command_system.types import CommandContext

            ctx = CommandContext(
                workspace_root=Path(self.cwd),
                cwd=Path(self.cwd),
                conversation=getattr(self.session, "conversation", None),
                cost_tracker=None,
                history=None,
                app_state_store=None,
                provider=self.provider,
            )
            result = advisor_command_call(str(arg or ""), ctx)
            self._reply(request_id, {
                "ok": True,
                "text": str(getattr(result, "value", "") or ""),
            })
        except Exception as exc:  # noqa: BLE001 — must not kill the control channel
            logger.exception("[agent-server] advisor command failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _maybe_continue_goal(self, outcome: dict | None) -> None:
        """Post-turn goal hook (worker thread) — the port of hermes
        tui_gateway's "/goal continuation" block and the observable behavior
        of CC's session-scoped Stop-hook evaluator (docs/en/goal §How
        evaluation works).

        Runs after real user turns and __goal__ continuation turns (NOT
        after btw side-questions or internal notification turns — judging a
        goal against a background-task recap would entangle two self-driving
        loops). Skips when: no active goal, the turn was cancelled/errored
        (CC's no-Stop-hooks-on-error guard), the response is empty, or user
        input is already queued (preemption — the judge re-runs after their
        turn anyway; slash commands ride the control channel and never
        queue here, matching hermes's "slash commands don't preempt" rule).

        Concurrency (critic R1): goal state is shared with the control
        plane (/goal set|clear|pause on the asyncio loop), so this uses
        double-checked locking — snapshot + preflight under ``_lock``, the
        judge network call OUTSIDE the lock, verdict application +
        continuation enqueue back under the lock with an ``expected_state``
        identity check so a mid-judge clear/replace discards the stale
        verdict. Never raises.
        """
        try:
            if not outcome or outcome.get("subtype") != "success":
                return
            response_text = str(outcome.get("response_text") or "")
            if not response_text.strip():
                return

            # ── preflight under the lock ──────────────────────────────
            with self._lock:
                mgr = self._goal_mgr
                if mgr is None or not mgr.is_active():
                    return
                # Best-effort preemption, not a hard barrier: send_to_agent
                # puts user messages into _inbox WITHOUT _lock, so one can
                # land between the apply-block's empty() re-check and its
                # put() — order [user, __goal__], and one stale continuation
                # runs after the user's turn. Self-correcting: a queued
                # __goal__ item keeps this preflight returning early (at
                # most one ever queued), and the worker's staleness drop
                # kills it outright if the goal was cleared meanwhile.
                if not self._inbox.empty():
                    return  # user input pending — their turn wins
                state_snapshot = mgr.state
                goal_text = state_snapshot.goal
                subgoals = list(state_snapshot.subgoals)
            # Rebind the judge to the CURRENT provider (mid-goal /model
            # switches). Outside the lock: touches settings/imports only.
            mgr = self._goal_manager()

            from src.goals import collect_turn_evidence, judge_goal

            evidence = ""
            try:
                evidence = collect_turn_evidence(
                    list(self.session.conversation.messages)
                )
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] goal evidence failed", exc_info=True)
            if not evidence:
                evidence = response_text

            # ── judge OUTSIDE the lock (bounded network call) ─────────
            verdict, reason, parse_failed = judge_goal(
                goal_text, evidence, judge=mgr.judge,
                subgoals=subgoals or None,
            )

            snapshot = _cost_snapshot()
            # ── apply + enqueue back under the lock ───────────────────
            with self._lock:
                decision = mgr.apply_verdict(
                    verdict, reason, parse_failed,
                    tokens_now=self._usage_token_total(snapshot),
                    cost_now_usd=float(snapshot.get("total_cost_usd", 0.0) or 0.0),
                    expected_state=state_snapshot,
                )
                should_continue = bool(decision.get("should_continue"))
                continuation = decision.get("continuation_prompt") or ""
                if should_continue and continuation and self._inbox.empty():
                    # Internal-turn semantics downstream: no UserPromptSubmit
                    # hooks, no ultracode reminder, no memory recall, no
                    # stats-odometer tick — loop machinery, not a user prompt.
                    self._inbox.put({"__goal__": True, "content": continuation})
                goal_active = bool(mgr.is_active())
                goal_snapshot, goal_rev = self._goal_snapshot_locked()
            self._save_session()  # persist turns_used/verdict/achieved state

            message = decision.get("message") or ""
            if message:
                self._emit({
                    "type": "system",
                    "subtype": "goal_status",
                    "session_id": self.session_id,
                    "message": message,
                    "goal_active": goal_active,
                    "goal": goal_snapshot,
                    "goal_rev": goal_rev,
                })
        except Exception:  # noqa: BLE001 — the goal loop must never kill the worker
            logger.debug("[agent-server] goal continuation hook failed",
                         exc_info=True)

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
            try:
                item = self._inbox.get(timeout=0.5)
            except _queue.Empty:
                # Idle between turns: surface any background task (workflow /
                # background agent) that finished since the last check — the
                # old REPL's turn-boundary drain, on a poll instead of a
                # blocking prompt.
                self._deliver_task_notifications()
                continue
            if item is _SHUTDOWN or self._stop.is_set():
                break
            if isinstance(item, dict) and item.get("__btw__"):  # side question (/btw)
                self._run_turn(item.get("content"), btw=True)
                continue
            if isinstance(item, dict) and item.get("__goal__"):
                # /goal continuation — internal-turn semantics (no UPS hooks,
                # no ultracode reminder, no recall, no odometer tick), but
                # the post-turn goal hook still evaluates it so the loop
                # keeps going until the evaluator says done.
                # Staleness drop: a /goal clear|pause that landed while this
                # continuation sat queued must win — running it would spend
                # a full model turn on a dead goal (hermes clears pending
                # synthetic continuations from its FIFO for the same race).
                with self._lock:
                    goal_live = (
                        self._goal_mgr is not None and self._goal_mgr.is_active()
                    )
                if not goal_live:
                    continue
                outcome = self._run_turn(item.get("content"), internal=True)
                self._maybe_continue_goal(outcome)
                self._deliver_task_notifications()
                continue
            if not isinstance(item, (str, list)):  # str prompt, or multimodal blocks
                continue
            outcome = self._run_turn(item)
            self._maybe_continue_goal(outcome)
            # A task that finished while the turn ran is delivered right after.
            self._deliver_task_notifications()
        self._close_stream()

    def _deliver_task_notifications(self) -> bool:
        """Drain finished-task ``<task-notification>`` envelopes: emit one
        completion-banner frame per task, then hand the envelopes to the agent
        as ONE internal turn so it summarizes the results conversationally (the
        "the research is done…" behavior the workflow directives promise).

        The ``task-notification`` queue is shared by dynamic workflows AND
        background agents (``enqueue_workflow_notification`` /
        ``enqueue_agent_notification``) — this consumer intentionally delivers
        both. Runs on the worker thread strictly between turns, so it can never
        interleave with a user turn. Returns whether anything was delivered.

        CAVEAT (single-session-per-process assumption): the queue is
        process-global while sessions are per-connection, so in a
        multi-session process (DirectConnectServer spawns one agent per WS
        connection) whichever worker polls first would drain EVERY session's
        envelopes into its own conversation. Fine for the shipped stdio
        deployment (one session per process); per-session scoping is required
        before multi-session ``cc://`` ships.
        """
        if self.init_error is not None or self._stop.is_set():
            return False
        try:
            from src.utils.message_queue_manager import drain_pending_notifications

            drained = drain_pending_notifications(mode="task-notification")
        except Exception:  # noqa: BLE001 — delivery must never kill the worker
            logger.debug("[agent-server] notification drain failed", exc_info=True)
            return False
        if not drained:
            return False

        from src.server.task_notifications import (
            build_notification_turn,
            parse_task_id,
            render_banner,
        )

        registry = getattr(self.tool_context, "runtime_tasks", None)
        envelopes = [n.value for n in drained]
        for xml in envelopes:
            task_id = parse_task_id(xml)
            state = None
            if registry is not None and task_id:
                try:
                    state = registry.get(task_id)
                except Exception:  # noqa: BLE001
                    state = None
            self._emit({
                "type": "system",
                "subtype": "task_notification",
                "session_id": self.session_id,
                "task_id": task_id or "task",
                "message": "\n".join(render_banner(xml, state)),
            })

        # Let the agent read the results and report conversationally.
        self._run_turn(build_notification_turn(envelopes), internal=True)
        return True

    def _build_turn_pipeline_config(self, turn_provider: Any) -> Any:
        """ch05 round-4 GAP A — per-turn PipelineConfig with the
        SESSION-scoped AutoCompactTracking (lazy-created once; the
        3-consecutive-failures circuit breaker must count across turns —
        a per-turn instance would reset it every prompt). Never raises."""
        try:
            from src.services.compact.autocompact import AutoCompactTracking
            from src.services.compact.pipeline import (
                build_production_pipeline_config,
            )

            if self._auto_compact_tracking is None:
                self._auto_compact_tracking = AutoCompactTracking()
            return build_production_pipeline_config(
                turn_provider, self.tool_context, self._auto_compact_tracking,
            )
        except Exception:  # noqa: BLE001 — pipeline wiring must not kill the turn
            logger.debug("[agent-server] pipeline config build failed",
                         exc_info=True)
            return None

    def _fire_session_start_once(self) -> None:
        """ch12 round-4 WI-3 — fire SessionStart hooks exactly once, before
        the first real turn. Sync wrapper (a tiny asyncio.run) because
        _run_turn is a sync worker; the session_hooks router is async."""
        if self._session_start_fired:
            return
        self._session_start_fired = True
        try:
            import asyncio as _asyncio

            from src.hooks.session_hooks import run_session_start_hooks

            _asyncio.run(run_session_start_hooks(
                session_id=self.session_id, cwd=self.cwd,
                tool_use_context=self.tool_context,
            ))
        except Exception:  # noqa: BLE001 — a hook must not block the turn
            logger.debug("[agent-server] SessionStart hooks failed",
                         exc_info=True)

    def _run_user_prompt_submit_hooks(self, prompt: Any) -> Any:
        """ch14 round-4 — sync wrapper around the async UserPromptSubmit
        router (_run_turn is a sync worker). Returns the outcome or None on
        failure. Never raises."""
        try:
            import asyncio as _asyncio

            from src.hooks.session_hooks import run_user_prompt_submit_hooks

            text = _extract_prompt_text({"content": prompt})
            return _asyncio.run(run_user_prompt_submit_hooks(
                text, session_id=self.session_id, cwd=self.cwd,
                tool_use_context=self.tool_context,
            ))
        except Exception:  # noqa: BLE001 — a hook must not block the turn
            logger.debug("[agent-server] UserPromptSubmit hooks failed",
                         exc_info=True)
            return None

    @staticmethod
    def _parse_turn_budget(prompt: Any) -> int | None:
        """ch05 round-4 GAP B — the '+500k' auto-continue budget from the
        ORIGINAL user prompt (str or content-block list). Best-effort."""
        try:
            from src.query.token_budget import parse_token_budget

            return parse_token_budget(_extract_prompt_text({"content": prompt}))
        except Exception:  # noqa: BLE001 — budget parse is best-effort
            logger.debug("[agent-server] token budget parse failed",
                         exc_info=True)
            return None

    def _run_turn(self, prompt, btw: bool = False, internal: bool = False) -> dict | None:
        # prompt: str | list[ContentBlock]
        # Returns a small outcome dict {"subtype", "response_text"} for the
        # worker's post-turn goal hook (None-safe there: a missed path
        # degrades to "no continuation", never an error).
        # btw=True → a "side question" (the original's /btw): run with full context
        # but DON'T persist the Q&A, so the main conversation isn't interrupted.
        # internal=True → a system-generated turn (task-notification delivery):
        # skip user-turn decorations like the ultracode reminder.
        from src.query.agent_loop_compat import run_query_as_agent_loop

        if self.init_error is not None:
            self._emit(_result_message(
                self.session_id,
                permission_mode=_current_mode(self.tool_context, self.config.permission_mode), subtype="error", num_turns=0,
                result=self.init_error, is_error=True, error=self.init_error,
                session_turns=self._stats_turns,
            ))
            return {"subtype": "error", "response_text": ""}

        # ch05 round-4 GAP B (critic m1) — parse the '+500k' budget from the
        # ORIGINAL prompt BEFORE ultracode augmentation: the reminder is
        # APPENDED, and the shorthand's end-anchored regex would no longer
        # match a trailing "+500k" once a <system-reminder> follows it.
        token_budget = self._parse_turn_budget(prompt) if not internal else None

        # ch14 round-4 — UserPromptSubmit hooks (TS processUserInput.ts:182).
        # Fire on the RAW prompt of a real user turn, BEFORE any ultracode
        # augmentation (the hook contract sees exactly what the user typed;
        # internal/notification turns skip). A hook can BLOCK (erase the
        # prompt + warn, no query) or INJECT additionalContext the model
        # sees. Trust-gated via the per-context snapshot (ch12). A hook
        # failure never blocks the turn.
        # Skip on `internal` (notification/side-generated) turns AND on `btw`
        # side-questions: /btw is an ephemeral meta-turn whose Q&A is rolled
        # back, and firing on it would (a) run a real-prompt validation hook
        # on a meta-turn and (b) leak the prevent-path messages past the btw
        # rollback (critic-2 MINOR). UserPromptSubmit fires only on real,
        # persisted user prompts.
        _ups_contexts: list[str] = []
        if not internal and not btw:
            ups = self._run_user_prompt_submit_hooks(prompt)
            if ups is not None and ups.blocked:
                # blockingError → ERASE the prompt + warn (TS
                # processUserInput.ts:203-211): the model never sees it.
                self._emit(_system_message(
                    self.session_id,
                    f"UserPromptSubmit operation blocked by hook:\n"
                    f"{ups.block_message}\n\nOriginal prompt: "
                    f"{_extract_prompt_text({'content': prompt})}",
                    level="warning",
                ))
                self._emit(_result_message(
                    self.session_id,
                    permission_mode=_current_mode(self.tool_context, self.config.permission_mode), subtype="success", num_turns=0,
                    result="", is_error=False, duration_ms=0,
                    session_turns=self._stats_turns,
                ))
                return {"subtype": "success", "response_text": ""}
            if ups is not None and ups.prevented:
                # preventContinuation → KEEP the prompt in context + push an
                # "Operation stopped by hook" note; no query (TS :213-224).
                self.session.conversation.add_user_message(prompt)
                self.session.conversation.add_user_message(
                    f"Operation stopped by hook: {ups.prevent_reason}"
                )
                self._emit(_result_message(
                    self.session_id,
                    permission_mode=_current_mode(self.tool_context, self.config.permission_mode), subtype="success", num_turns=0,
                    result="", is_error=False, duration_ms=0,
                    session_turns=self._stats_turns,
                ))
                return {"subtype": "success", "response_text": ""}
            if ups is not None:
                _ups_contexts = list(ups.additional_contexts)

        # ultracode (workflow-engine §4.1): the `ultracode` keyword in this
        # message, or the session-long `/effort ultracode` mode, appends a
        # <system-reminder> nudging the model to author a workflow rather than
        # working turn by turn. No-op when workflows are disabled; skipped for
        # internal turns so a notification envelope can never trigger it.
        if not internal:
            prompt = _with_ultracode_reminder(prompt)

        abort = AbortController()
        with self._lock:
            self._current_abort = abort
        # Wire the per-turn controller into the tool context so an interrupt
        # tears down an in-flight tool (Bash supervisor, etc.), not just the
        # model stream. A fresh controller per turn avoids a prior turn's
        # abort pre-cancelling the next one.
        if self.tool_context is not None:
            self.tool_context.abort_controller = abort

        # Snapshot history for a side-question turn so we can restore it after
        # (drops the ephemeral Q + A on every exit path via the finally below).
        _btw_snapshot = list(self.session.conversation.messages) if btw else None
        self.session.conversation.add_user_message(prompt)
        # Inject any UserPromptSubmit additionalContext as a system-reminder
        # user message right after the prompt (the model reads it as context).
        for _ctx in _ups_contexts:
            self.session.conversation.add_user_message(
                f"<system-reminder>\n{_ctx}\n</system-reminder>"
            )
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

        def on_thinking_chunk(chunk: str) -> None:
            # Live reasoning deltas → a separate thinking delta the TUI renders
            # in its streaming thinking view (the original's live thinking, §3).
            self._emit({
                "type": "stream_event",
                "session_id": self.session_id,
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": chunk},
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

        # ch12 round-4 WI-3 — SessionStart fires once, before the first real
        # turn (skipped for internal/notification turns so a task
        # notification can't count as the session's start).
        if not internal:
            self._fire_session_start_once()

        # /effort: wrap the provider to inject reasoning_effort (default off ⇒
        # the real provider is used unchanged).
        turn_provider = _EffortProvider(self.provider, self._effort) if self._effort else self.provider
        # ch05 round-4 GAP A — the production compaction pipeline. The
        # tracking is session-scoped (circuit breaker survives turns); the
        # config is rebuilt per turn so it always carries the CURRENT
        # provider/model and read-file fingerprints.
        pipeline_config = self._build_turn_pipeline_config(turn_provider)
        try:
            # Coordinator mode: the MAIN loop runs on the filtered view
            # (Agent/SendMessage/TaskStop/StructuredOutput + PR-activity MCP);
            # subagents spawn from the Agent tool's captured FULL registry.
            from src.coordinator.mode import coordinator_main_loop_registry

            result = asyncio.run(run_query_as_agent_loop(
                initial_messages=list(self.session.conversation.messages),
                provider=turn_provider,
                tool_registry=coordinator_main_loop_registry(self.tool_registry),
                tool_context=self.tool_context,
                system_prompt=self.system_prompt,
                max_turns=self.config.max_turns,
                on_text_chunk=on_text_chunk,
                on_thinking_chunk=on_thinking_chunk,
                on_message=on_message,
                abort_controller=abort,
                extended_thinking=self._thinking,  # None = model default; True/False = ThinkingToggle
                fallback_model=self.config.fallback_model,
                pipeline_config=pipeline_config,
                query_source="repl_main_thread",
                token_budget=token_budget,
                # ch11 round-4 WI-1 — session-scoped memory-recall de-dup.
                # Only enable the recall for REAL user turns: passing None on
                # internal/notification turns (critic #8) means the adapter
                # uses a throwaway set, but we ALSO want to skip the recall
                # entirely there, so gate on `internal`.
                memory_surfaced=None if internal else self._memory_surfaced,
                memory_recall_enabled=not internal,
            ))
        except AbortError:
            self._emit(_result_message(
                self.session_id,
                permission_mode=_current_mode(self.tool_context, self.config.permission_mode), subtype="cancelled", num_turns=0,
                result="", is_error=False,
                duration_ms=int((time.monotonic() - start) * 1000),
                session_turns=self._stats_turns,
            ))
            return {"subtype": "cancelled", "response_text": ""}
        except Exception as exc:  # noqa: BLE001 - one bad turn must not kill the session
            logger.exception("[agent-server] turn failed")
            self._emit(_result_message(
                self.session_id,
                permission_mode=_current_mode(self.tool_context, self.config.permission_mode), subtype="error", num_turns=0,
                result=str(exc), is_error=True, error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
                session_turns=self._stats_turns,
            ))
            return {"subtype": "error", "response_text": ""}
        finally:
            with self._lock:
                self._current_abort = None
            if btw and _btw_snapshot is not None:
                msgs = self.session.conversation.messages
                msgs.clear()
                msgs.extend(_btw_snapshot)

        _usage = result.usage if result.num_turns > 0 else None
        _cost = 0.0
        if _usage:
            try:
                from src.services.pricing import compute_cost

                _cost = compute_cost(getattr(self.provider, "model", None) or self.config.model or "", _usage)
            except Exception:  # noqa: BLE001 — cost is best-effort, never break the turn
                _cost = 0.0
        # One more completed user turn. Internal (notification) and btw
        # (ephemeral, rolled-back) turns don't move the odometer — same rule
        # as the deleted REPL, which only counted real prompt→response rounds.
        if not internal and not btw:
            self._stats_turns += 1
        self._emit(_result_message(
            self.session_id,
            permission_mode=_current_mode(self.tool_context, self.config.permission_mode),
            subtype="success",
            num_turns=result.num_turns,
            result=result.response_text,
            is_error=False,
            usage=_usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            total_cost_usd=_cost,
            session_turns=self._stats_turns,
        ))
        self._save_session()  # persist for /resume
        return {"subtype": "success", "response_text": result.response_text or ""}

    async def shutdown(self) -> None:
        self._stop.set()
        # ch12 round-4 WI-3 — SessionEnd hooks fire at shutdown (TS
        # gracefulShutdown.ts:486). Configured cleanup hooks never ran.
        try:
            from src.hooks.session_hooks import run_session_end_hooks

            await run_session_end_hooks(
                session_id=self.session_id,
                tool_use_context=self.tool_context,
            )
        except Exception:  # noqa: BLE001 — a hook must not block shutdown
            logger.debug("[agent-server] SessionEnd hooks failed", exc_info=True)
        # ch10 round-4 WI-2 — stop the eviction sweeper daemon (started in
        # _build_runtime under single_session). Idempotent; safe if never
        # started.
        try:
            from src.tasks.eviction import stop_eviction_sweeper

            stop_eviction_sweeper()
        except Exception:  # noqa: BLE001
            logger.debug("[agent-server] eviction sweeper stop failed",
                         exc_info=True)
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


def _mcp_disabled_path() -> Path:
    return Path.home() / ".clawcodex" / "mcp-disabled.json"


def _load_disabled_mcp() -> set[str]:
    """Persisted set of MCP servers the user disabled (MCPServerMultiselectDialog)."""
    try:
        data = json.loads(_mcp_disabled_path().read_text())
        return {str(x) for x in data} if isinstance(data, list) else set()
    except Exception:  # noqa: BLE001
        return set()


def _save_disabled_mcp(disabled: set[str]) -> None:
    try:
        p = _mcp_disabled_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted(disabled)))
    except Exception:  # noqa: BLE001
        pass


def _make_mcp_notification_handler(mcp_rt: Any, sess: "_AgentSession", server: str) -> Any:
    """ch15 round-4 — sync MCP notification handler (method, params).

    On ``notifications/tools/list_changed`` schedule a tools re-fetch on the
    connection loop; when it lands, SWAP the server's tools in the live agent
    registry (remove the old ``mcp__{server}__*`` names, register the new) so
    a mid-session tool change is visible without a restart. Runs on the
    McpRuntime loop thread; the schedule is non-blocking (no self-deadlock).

    Resolves ``sess.tool_registry`` at REFRESH time, not boot time (critic
    M1): a provider/model switch builds a brand-new registry and rebinds
    ``sess.tool_registry``, so a handler closing over the boot registry would
    mutate an orphaned one and the agent would never see the refresh. The
    elicitation handler closes over ``sess`` for the same reason.
    """

    def _on_change(removed_full: list, new_tools: list) -> None:
        registry = getattr(sess, "tool_registry", None)
        if registry is None:
            return
        for full in removed_full:
            try:
                registry.remove_tool(full)
            except Exception:  # noqa: BLE001
                logger.debug("[mcp] remove_tool failed: %s", full, exc_info=True)
        for tool in new_tools:
            try:
                registry.register(tool)
            except Exception:  # noqa: BLE001
                logger.debug("[mcp] re-register failed: %s",
                             getattr(tool, "name", "?"), exc_info=True)

    def _handle(method: str, _params: Any) -> None:
        if method == "notifications/tools/list_changed":
            mcp_rt.schedule_tool_refresh(server, _on_change)

    return _handle


def _make_elicitation_handler(sess: "_AgentSession") -> Any:
    """Async MCP elicitation handler that bridges a server's input request to the
    TUI via the session's control-request round-trip (reusing the permission
    ``_pending`` mechanism). Runs on the McpRuntime loop; ``_emit`` is thread-safe
    and the main loop's control_response handler sets the pending event.
    """

    async def _elicit(params: dict[str, Any]) -> dict[str, Any]:
        # MCP elicitation hooks (C3): fire the 3-event unit around the prompt.
        # An Elicitation hook may provide a response (short-circuit) or block
        # (→ decline); an ElicitationResult hook may OVERRIDE the response or
        # block; a Notification hook records the final action. server_name is
        # threaded in by McpClient._run_elicitation.
        ctx = getattr(sess, "tool_context", None)
        server_name = params.get("serverName") or ""
        mode = params.get("mode")
        elicitation_id = params.get("elicitationId")

        if ctx is not None and server_name:
            try:
                from src.hooks.hook_executor import execute_elicitation_hooks

                resp, block = await execute_elicitation_hooks(
                    server_name, params.get("message", ""), ctx,
                    requested_schema=params.get("requestedSchema"),
                    mode=mode, url=params.get("url"),
                    elicitation_id=elicitation_id,
                )
                if block is not None:
                    return {"action": "decline"}
                if resp is not None:
                    # short-circuit: the Elicitation hook itself answered.
                    # TS returns this DIRECTLY (elicitationHandler.ts:96-107 —
                    # `if (hookResponse) return hookResponse`), WITHOUT running
                    # the result hooks or the notification (those run only on
                    # the real user-prompt path below). Return as-is.
                    return _elicit_result(resp["action"], resp.get("content"))
            except Exception:  # noqa: BLE001 — a hook failure must not brick elicitation
                logger.debug("[hooks] elicitation pre-hook failed", exc_info=True)

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
        raw = {"action": "cancel"} if not got else (pending.reply or {"action": "decline"})
        if ctx is not None and server_name:
            return await _finish_elicitation(raw, ctx, server_name, mode, elicitation_id)
        return raw

    def _elicit_result(action: str, content: Any) -> dict[str, Any]:
        # Omit ``content`` when None (TS emits ``undefined``, which
        # JSON.stringify drops — a strict MCP server validating ElicitResult
        # against "object or absent" could reject an explicit ``null``).
        return {"action": action} if content is None else {"action": action, "content": content}

    async def _finish_elicitation(
        raw: dict[str, Any], ctx: Any, server_name: str,
        mode: str | None, elicitation_id: str | None,
    ) -> dict[str, Any]:
        """Run ElicitationResult hooks (may override/block), then fire the
        elicitation_response Notification (port of runElicitationResultHooks).

        The return value is computed inside the try (a result-hook raise leaves
        ``final = raw``) but the notification fires OUTSIDE it: TS fires it
        fire-and-forget (``void``) so it can NEVER alter the ElicitResult
        (critic C3-MAJOR). It fires on every path — block, override,
        passthrough, and the result-hook error path (TS "even on error",
        elicitationHandler.ts:304-310 → MINOR-1). ``execute_notification_hooks``
        itself never raises."""
        from src.hooks.hook_executor import (
            execute_elicitation_result_hooks,
            execute_notification_hooks,
        )

        final = raw
        try:
            resp, block = await execute_elicitation_result_hooks(
                server_name, raw.get("action", "decline"), raw.get("content"),
                ctx, mode=mode, elicitation_id=elicitation_id,
            )
            if block is not None:
                final = {"action": "decline"}
            elif resp is not None:
                _c = resp.get("content")
                final = _elicit_result(
                    resp["action"], _c if _c is not None else raw.get("content")
                )
        except Exception:  # noqa: BLE001 — a result-hook failure falls back to raw
            logger.debug("[hooks] elicitation result-hook failed", exc_info=True)
            final = raw
        # Fire-and-forget observability — never alters ``final``.
        await execute_notification_hooks(
            f'Elicitation response for server "{server_name}": {final.get("action")}',
            "elicitation_response", ctx,
        )
        return final

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
        from src.utils.startup_profiler import profile_checkpoint

        profile_checkpoint("agent_server_build_runtime_start")

        # ch02 round-4 WI-1 — one persisted-trust verdict for this
        # session's cwd, evaluated BEFORE any config read: get_merged's
        # untrusted-tier strip now gates per-cwd on this same source, and
        # the provider below is resolved through that merge. Never
        # prompts; NEVER flips the process-global session flag — one
        # server process can host sessions with different (even
        # client-supplied, on --http) cwds, and the flag short-circuits
        # check_trust_accepted for every later session (critic B1).
        trusted = False
        try:
            from src.services.startup_gates import check_trust_accepted

            trusted = check_trust_accepted(sess.cwd)
        except Exception:  # noqa: BLE001 — unknown trust stays untrusted
            logger.debug("[agent-server] trust check failed", exc_info=True)
        if trusted and sess.config.single_session:
            # Post-trust env repair for a standalone single-session server
            # (`clawcodex agent-server --stdio` run by hand — no parent
            # bootstrap applied any config env). For the Ink-spawned child
            # this re-applies what the parent already applied — an
            # idempotent no-op. Gated single_session (critic B1): on the
            # multi-session --http transport a session must not mutate the
            # process-global os.environ from its project config (bleed
            # into other sessions + unlocked concurrent mutation).
            # Standalone limitation, documented (critic M2): the MDM
            # policy tier and keychain stash live in init()'s SAFE pass,
            # which no agent-server lane runs — config-file env only here.
            try:
                from src.permissions.trust_boundary import (
                    apply_full_config_environment_variables,
                )

                apply_full_config_environment_variables()
            except Exception:  # noqa: BLE001 — env repair is best-effort
                logger.debug("[agent-server] post-trust env apply failed",
                             exc_info=True)
        # ch02 round-4 WI-2 — warm the context caches (CLAUDE.md walk,
        # git status when trusted) during the user's typing window; the
        # first turn's build_context_prompt reads the same underlying
        # caches. Fire-and-forget daemon thread; failures swallowed.
        # Gated single_session: _git_context_cache is process-global and
        # not cwd-keyed (pre-existing; ch03 follow-up), so warming it on a
        # multi-session --http server would seed other sessions' prompts
        # with this session's git status (critic M3).
        if sess.config.single_session:
            try:
                from src.deferred_init import start_deferred_prefetches

                start_deferred_prefetches(cwd=sess.cwd)
            except Exception:  # noqa: BLE001 — prefetch is advisory
                logger.debug("[agent-server] deferred prefetch kick failed",
                             exc_info=True)
        profile_checkpoint("agent_server_trust_prefetch_done")

        cfg = sess.config

        # Sandbox HARD GATE (C8): TS's failIfUnavailable is a REFUSE-TO-START
        # at the entrypoints (print.ts:600 / REPL.tsx:2362 "refusing to start
        # without a working sandbox"), NOT a per-command refusal. The port has
        # no sandbox enforcement, so under sandbox.enabled+failIfUnavailable the
        # SESSION must refuse to start — otherwise /bg, MCP servers, and hooks
        # (which run OUTSIDE _bash_call) would execute unsandboxed while the
        # per-_bash_call guard only stops foreground/bg BashTool commands.
        try:
            from src.permissions.sandbox_guard import sandbox_hard_gate_error
            from src.settings.settings import get_settings

            _gate = sandbox_hard_gate_error(get_settings(cwd=sess.cwd))
            if _gate:
                sess.init_error = _gate
                return
        except Exception:  # noqa: BLE001 — the guard must never crash startup
            logger.debug("[agent-server] sandbox hard-gate check failed", exc_info=True)

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
        profile_checkpoint("agent_server_provider_ready")

        # (ch03 round-4 GAP A: the per-session AppState store is created
        # below, once the session's launch permission mode is known, so
        # the store's initial state doesn't misreport the mode — see the
        # block after setup_permissions.)

        registry = build_default_registry(provider=provider)
        profile_checkpoint("agent_server_registry_built")
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
                # ch15 round-4 — wire tools/list_changed → live tool refresh.
                # A server that changes its tools mid-session pushes
                # notifications/tools/list_changed; we re-fetch and SWAP the
                # tools in the live registry so the agent sees them without a
                # session restart. Previously the notification was dropped.
                for _srv_name, _cl in mcp_rt.clients.items():
                    try:
                        _cl.set_elicitation_handler(_eh)
                    except Exception:  # noqa: BLE001
                        pass
                    # R5 (ch15 m3) — only wire the tools/list_changed refresh
                    # for servers that ADVERTISE tools.listChanged, mirroring
                    # TS (useManageMCPConnections registers the handler only
                    # when client.capabilities.tools.listChanged). This makes
                    # the parsed capability load-bearing; a server that never
                    # advertised it can't trigger a refresh.
                    try:
                        _caps = getattr(_cl, "capabilities", None)
                        if getattr(_caps, "tools_list_changed", False):
                            _cl.set_notification_handler(
                                _make_mcp_notification_handler(
                                    mcp_rt, sess, _srv_name
                                )
                            )
                    except Exception:  # noqa: BLE001
                        pass
                logger.info(
                    "[agent-server] MCP: %d tool(s) from %d server(s)",
                    len(mcp_rt.tools), len(mcp_rt.servers),
                )
                # Surface OAuth servers awaiting auth (C4): a needs-auth server
                # used to fail to connect silently. Tell the user how to act —
                # /mcp auth <server> triggers the flow.
                _pending = mcp_rt.pending_auth()
                if _pending:
                    _names = ", ".join(_pending)
                    sess._emit(_system_message(
                        sess.session_id,
                        f"MCP server(s) need authentication: {_names}. "
                        f"Run `/mcp auth <server>` to sign in.",
                        level="info",
                    ))
        except Exception:  # noqa: BLE001 — MCP must never break startup
            logger.debug("[agent-server] MCP bootstrap skipped", exc_info=True)
        profile_checkpoint("agent_server_mcp_done")

        workspace_root = Path(sess.cwd)
        mode = perm_mode or cfg.permission_mode or "default"
        # ch03 round-4 GAP A — re-home the two-tier bridge: a per-session
        # AppState store whose on_change router runs the centralized side
        # effects (bootstrap model mirror + user-settings persistence).
        # The seed applies a persisted /model choice back to the provider
        # under seed_app_state_from_settings' provider-match guard — an
        # explicit model (CLI/client cfg.model) always wins. The initial
        # state carries the session's real launch permission mode (critic
        # n5: seeding the default then dispatching the true mode would
        # fire a spurious first mode-change notification). Gated
        # single_session (same rule as ch02's env apply): user-level
        # settings writes must not fire from client-supplied --http
        # sessions.
        if cfg.single_session:
            try:
                from src.state.app_state import (
                    create_app_state_store,
                    replace_state,
                    seed_app_state_from_settings,
                    set_active_provider_supplier,
                )

                set_active_provider_supplier(lambda: sess.provider_name)
                seeded_state = replace_state(
                    seed_app_state_from_settings(provider_name),
                    permission_mode=mode,
                )
                sess.app_state_store = create_app_state_store(seeded_state)
                if cfg.model is None and seeded_state.main_loop_model:
                    provider.model = seeded_state.main_loop_model
            except Exception:  # noqa: BLE001 — store failure must not break startup
                logger.debug("[agent-server] app-state store init failed",
                             exc_info=True)
        # Availability = launched IN bypass mode, OR the launch boundary
        # resolved it available (flags and/or trusted settings) and forwarded
        # it via cfg.is_bypass_available. We do NOT read settings ambiently
        # here: on the multi-session --http transport that would let the server
        # host's own settings unlock bypass for every client session,
        # regardless of the client's cwd. Availability is decided once per
        # launch (src/cli.py _resolve_permission_state,
        # tui_launcher.run_tui_launcher, and agent_server_cli for the
        # single-session stdio case) and carried in. Availability alone does
        # NOT enter bypass; it only unlocks it for Shift+Tab /
        # set_permission_mode. Mirrors permissionSetup.ts:941-945.
        bypass = mode == "bypassPermissions" or cfg.is_bypass_available
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
        # ch01 round-4 WI-1 — load settings hooks into the executor-visible
        # snapshot + global registry. Safe here: _build_runtime runs in an
        # executor thread with no live event loop. Never raises.
        from src.hooks.config_manager import bootstrap_hook_config_manager

        tool_context.hook_config_manager = bootstrap_hook_config_manager(
            cwd=sess.cwd,
        )
        # workspace_trusted feeds the hook trust gate (trust_gate WI-0.2) —
        # the verdict hoisted at function entry (ch02 WI-1), same source of
        # truth as the CLI's startup trust gate (computeTrustDialogAccepted
        # parity).
        tool_context.workspace_trusted = trusted
        profile_checkpoint("agent_server_permissions_hooks_done")
        if sess._mcp_runtime is not None:
            tool_context.mcp_clients = sess._mcp_runtime.clients  # server-name catalog for the agent tool

        # PLUGINS-1 — initBuiltinPlugins (main.tsx:1926 analog): register
        # bundled built-in plugins before commands/prompt assemble. Idempotent.
        try:
            from src.plugins.init_builtin import init_builtin_plugins

            init_builtin_plugins()
        except Exception:  # noqa: BLE001 — plugins must not block startup
            logger.debug("[agent-server] init_builtin_plugins failed", exc_info=True)

        # OS-1 G1 — the startup producer: a settings-configured output style
        # applies from the FIRST prompt (before this, output_style_name was
        # only ever set by the set_output_style control).
        try:
            from src.outputStyles import output_style_from_settings

            settings_style = output_style_from_settings(cwd=sess.cwd)
            if settings_style and getattr(tool_context, "output_style_name", None) is None:
                tool_context.output_style_name = settings_style
        except Exception:  # noqa: BLE001 — style must not block startup
            logger.debug("[agent-server] output style from settings failed", exc_info=True)

        # Assign the registry + load the persisted MCP toggles BEFORE the
        # prompt build — _mcp_server_infos() filters by
        # registry.disabled_servers, so a disabled server's instructions must
        # be excludable at this init build (critic C2-MAJOR: the filter was
        # non-functional here because these ran AFTER the build).
        sess.tool_registry = registry
        registry.disabled_servers = _load_disabled_mcp()  # honor persisted MCP toggles

        try:
            from src.outputStyles import resolve_output_style
            from src.query.agent_loop_compat import build_effective_system_prompt

            style_prompt = resolve_output_style(
                getattr(tool_context, "output_style_name", None),
                getattr(tool_context, "output_style_dir", None),
            ).prompt
            system_prompt = build_effective_system_prompt(
                style_prompt, tool_context, provider=provider,
                mcp_servers=sess._mcp_server_infos(),
            )
        except Exception:  # noqa: BLE001 - fall back to a plain prompt
            logger.debug("[agent-server] system prompt build failed", exc_info=True)
            system_prompt = "You are a helpful assistant."
        profile_checkpoint("agent_server_prompt_built")

        sess.provider = provider
        sess.provider_name = provider_name
        sess.tool_context = tool_context
        tool_context.agent_progress_emit = sess._emit_agent_progress  # stream subagent progress
        sess.session = Session.create(provider_name, getattr(provider, "model", model or ""))
        sess._base_system_prompt = system_prompt
        sess.system_prompt = sess._compose_with_plan(system_prompt)  # honor an existing /plan

        # ch10 round-4 WI-2 (critic B1) — start the terminal-task eviction
        # sweeper HERE, after tool_context is constructed and stored (the
        # earlier placement read sess.tool_context while it was still None,
        # so the sweeper never started — reintroducing the very
        # built-but-dead defect WI-2 exists to fix). The sweeper
        # (src/tasks/eviction.py) reclaims terminal background tasks that
        # otherwise pile up in runtime_tasks / /tasks unbounded. Gated
        # single_session: bound to THIS session's runtime_tasks (a
        # multi-session --http server would need one per session, deferred).
        if sess.config.single_session:
            try:
                from src.tasks.eviction import start_eviction_sweeper

                start_eviction_sweeper(tool_context.runtime_tasks)
            except Exception:  # noqa: BLE001 — sweeper is advisory
                logger.debug("[agent-server] eviction sweeper start failed",
                             exc_info=True)
        profile_checkpoint("agent_server_build_runtime_end")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[agent-server] runtime build failed")
        sess.init_error = f"agent-server failed to start: {exc}"


def _with_ultracode_reminder(prompt):
    """Append the ultracode ``<system-reminder>`` to a user turn when the
    keyword / session mode calls for it (:mod:`src.workflow.ultracode` — the
    seam the deleted REPL provided at ``core.py:3163``). Handles both prompt
    shapes the inbox carries: a plain string, or a content-block list
    (multimodal) — detection joins the text blocks and the reminder lands as an
    extra text block. Returns the prompt unchanged when no reminder applies."""
    try:
        from src.workflow.ultracode import ultracode_reminder_for

        if isinstance(prompt, str):
            reminder = ultracode_reminder_for(prompt)
            if reminder:
                return f"{prompt}\n\n{reminder}" if prompt else reminder
            return prompt
        if isinstance(prompt, list):
            text = "\n".join(
                str(b.get("text", ""))
                for b in prompt
                if isinstance(b, dict) and b.get("type") == "text"
            )
            reminder = ultracode_reminder_for(text)
            if reminder:
                return [*prompt, {"type": "text", "text": reminder}]
            return prompt
    except Exception:  # noqa: BLE001 — the reminder must never break a turn
        logger.debug("[agent-server] ultracode reminder failed", exc_info=True)
    return prompt


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


def _display_tool_result(value: Any) -> dict | None:
    """Trim a rich Edit/Write or WebSearch tool output for the wire (display
    data only).

    Recognizes self-describing shapes rather than a tool name so mid-turn
    clients can render without tool_use bookkeeping:

    * Edit/Write (``type``/``filePath``/``structuredPatch``) — deliberate
      delta from real CC's full-parity ``tool_use_result``: ``originalFile``
      is dropped (the display renderer never reads it) and update-type
      ``content`` (the full post-edit file) is reduced to ``firstLine``
      (language/shebang detection only; the original uses the pre-edit first
      line — differs only when line 1 itself changed); create-type keeps
      ``content`` for the file preview.
    * WebSearch (``query``/``results``/``duration_seconds``) — reduced to the
      two numbers the original's one-line render needs (UI.tsx
      renderToolResultMessage: "Did N searches in Xs"): ``searchCount`` per
      getSearchSummary (non-string entries in ``results``) and
      ``durationSeconds``. The result blob itself already travels as the
      tool_result content.

    Always builds a new dict — ``value`` is shared with the in-memory message
    and the persisted transcript.
    """
    if not isinstance(value, dict):
        return None
    if (
        "type" not in value
        and isinstance(value.get("query"), str)
        and isinstance(value.get("results"), list)
        and isinstance(value.get("duration_seconds"), (int, float))
        and not isinstance(value.get("duration_seconds"), bool)
    ):
        return {
            "type": "web_search",
            "durationSeconds": float(value["duration_seconds"]),
            "searchCount": sum(
                1 for r in value["results"] if r is not None and not isinstance(r, str)
            ),
        }
    if value.get("type") not in ("create", "update"):
        return None
    if not isinstance(value.get("filePath"), str) or not isinstance(value.get("structuredPatch"), list):
        return None
    trimmed: dict[str, Any] = {
        "type": value["type"],
        "filePath": value["filePath"],
        "structuredPatch": value["structuredPatch"],
    }
    content = value.get("content")
    if isinstance(content, str):
        if value["type"] == "create":
            trimmed["content"] = content
        else:
            trimmed["firstLine"] = content.split("\n", 1)[0]
    return trimmed


def _sdk_envelope(message: Any, session_id: str) -> dict | None:
    """Wrap a :class:`Message` into the SDK envelope the client renders."""
    from src.types.messages import message_to_dict

    try:
        d = message_to_dict(message)
    except Exception:  # noqa: BLE001
        return None
    role = d.get("role", getattr(message, "role", "assistant"))
    msg_type = "assistant" if role == "assistant" else "user"
    env: dict[str, Any] = {
        "type": msg_type,
        "uuid": d.get("uuid"),
        "session_id": session_id,
        "message": {"role": role, "content": d.get("content")},
    }
    # Rich Edit/Write result → snake_case per the SDK stream convention
    # (SDKUserMessage.tool_use_result); the TUI renders the structured patch
    # from it instead of fabricating a diff from tool input.
    tool_use_result = _display_tool_result(d.get("toolUseResult"))
    if tool_use_result is not None:
        env["tool_use_result"] = tool_use_result
    return env


def _cost_snapshot() -> dict:
    """Session cost/duration totals for the client's /cost command and
    exit summary (the original's formatTotalCost inputs, cost-tracker.ts:249).

    Reads the bootstrap accumulators — the same counters the /resume cost
    restore repopulates — so the numbers survive restarts. Best-effort:
    a failure returns an empty snapshot rather than breaking the caller.
    """
    try:
        from src.bootstrap.state import (
            cost_state_lock,
            get_model_usage,
            get_total_api_duration,
            get_total_cost_usd,
            get_total_duration,
            get_total_lines_added,
            get_total_lines_removed,
            has_unknown_model_cost,
        )

        # Hold the accumulator lock across the multi-accessor read —
        # concurrent subagent threads insert into model_usage mid-turn, and
        # an unguarded dict iteration can raise (state.py:240 contract).
        with cost_state_lock():
            return {
                "total_cost_usd": get_total_cost_usd(),
                "total_api_duration_ms": int(get_total_api_duration()),
                "total_duration_ms": get_total_duration(),
                "total_lines_added": get_total_lines_added(),
                "total_lines_removed": get_total_lines_removed(),
                "has_unknown_model_cost": has_unknown_model_cost(),
                "model_usage": {
                    model: {
                        "input_tokens": u.input_tokens,
                        "output_tokens": u.output_tokens,
                        "cache_read_input_tokens": u.cache_read_input_tokens,
                        "cache_creation_input_tokens": u.cache_creation_input_tokens,
                        "web_search_requests": u.web_search_requests,
                        "cost_usd": u.cost_usd,
                    }
                    for model, u in get_model_usage().items()
                },
            }
    except Exception:  # noqa: BLE001 — cost display is best-effort
        logger.debug("[agent-server] cost snapshot failed", exc_info=True)
        return {}


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
    permission_mode: str | None = None,
    session_turns: int | None = None,
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
        # Running session totals, refreshed every turn so the client can
        # print the exit cost summary synchronously (the original registers
        # process.on('exit') over live cost-tracker state, costHook.ts:12).
        "cost": _cost_snapshot(),
    }
    if error is not None:
        payload["error"] = error
    # Completed-user-turn odometer for the client's session stats line
    # (distinct from num_turns, the agent-loop iteration count of THIS query).
    if session_turns is not None:
        payload["session_turns"] = session_turns
    # Server-side mode flips (plan approval, "accept edits for this session")
    # emit no dedicated event — the end-of-turn result refreshes the client's
    # permission-mode badge instead (at most one turn stale, and mode changes
    # only bind next turn anyway).
    if permission_mode is not None:
        payload["permission_mode"] = permission_mode
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


def _count_prompt_turns(msgs: list) -> int:
    """Real user prompts in a conversation — re-seeds ``_stats_turns`` after
    /resume and /rewind. A prompt is a user message that isn't an injected
    reminder (``isMeta``) and carries string or text-block content (a
    tool_result carrier is also role 'user' but has no text block)."""
    n = 0
    for m in msgs:
        if getattr(m, "role", None) != "user" or getattr(m, "isMeta", False):
            continue
        c = getattr(m, "content", None)
        if isinstance(c, str):
            n += 1
        elif isinstance(c, list) and any(
            (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "text"
            for b in c
        ):
            n += 1
    return n


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
                "cwd": data.get("cwd", ""),  # for the TagTabs project filter
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


def _session_option_label_safe(request: Any) -> str | None:
    """Authoritative per-tool label for the persist option (see
    ``session_option_label``). Best-effort — never break a permission prompt
    over label wording."""
    try:
        from src.permissions.updates import session_option_label

        return session_option_label(
            getattr(request, "suggestions", None) or (),
            getattr(request, "tool_name", "") or None,
            getattr(request, "tool_input", None),
        )
    except Exception:  # noqa: BLE001 — label is cosmetic
        return None


def _permission_request_warning(request: Any) -> str | None:
    """Destructive-command caution line for the approval box.

    The original renders this inside its Bash permission dialog
    (destructiveCommandWarning). Since the loosening rework routes
    destructive commands through the ordinary grantable prompt (no more
    un-grantable class asks), the warning is how the risk stays visible.
    Best-effort and purely informational."""
    try:
        if (getattr(request, "tool_name", "") or "") != "Bash":
            return None
        command = (getattr(request, "tool_input", None) or {}).get("command", "")
        if not isinstance(command, str) or not command:
            return None
        from src.tool_system.tools.bash.destructive_warnings import (
            get_destructive_command_warning,
        )

        return get_destructive_command_warning(command)
    except Exception:  # noqa: BLE001 — warning is cosmetic
        return None


def _serialize_permission_update(update: Any) -> dict:
    """ch13 round-4 — wire shape for a PermissionUpdate. Delegates to the
    canonical serializer (promoted to src/permissions/updates.py in HOOKS-1,
    paired with deserialize_permission_update)."""
    from src.permissions.updates import serialize_permission_update

    return serialize_permission_update(update)


def _deserialize_permission_update(data: dict) -> Any:
    """Reverse of _serialize_permission_update. Delegates to the canonical
    parser (promoted to src/permissions/updates.py in HOOKS-1 so the
    PermissionRequest-hook path shares it)."""
    from src.permissions.updates import deserialize_permission_update

    return deserialize_permission_update(data)


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


def _dispatch_app_state(sess: "_AgentSession", **changes: Any) -> None:
    """Route a state change through the session's AppState store, if any.

    ch03 round-4 GAP A — the store's on_change router owns the side
    effects (bootstrap mirror, settings persistence, listener seams), so
    control handlers dispatch instead of scattering those effects. No-op
    when the session has no store (--http transports). A store failure
    must never break the control channel.
    """
    store = getattr(sess, "app_state_store", None)
    if store is None:
        return
    try:
        from src.state.app_state import replace_state

        store.set_state(lambda prev: replace_state(prev, **changes))
    except Exception:  # noqa: BLE001
        logger.debug("[agent-server] app-state dispatch failed", exc_info=True)


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
