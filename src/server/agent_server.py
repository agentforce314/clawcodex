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
    {type:'system', subtype:'recap', session_id, recap, suggestion}
                                             # post-turn recap line + tab-acceptable composer prefill

client → server (``send_to_agent``)::

    {type:'user', message:{role:'user', content:<str|blocks>}}            # a prompt
    {type:'control_response', response:{request_id, response:{behavior,…}}} # perm reply
    {type:'control_request', request:{subtype:'interrupt'}}               # cancel turn
    {type:'control_request', request_id, request:{subtype:'set_permission_mode', mode}}
    {type:'control_request', request_id, request:{subtype:'set_model', model, provider?}}
                                             # replies {ok, model, warning?} | {ok:false, error}
    {type:'control_request', request_id, request:{subtype:'get_settings'|'get_context_usage'}}
    {type:'control_request', request_id, request:{subtype:'set_recap', value:'on'|'off'}}
                                             # /recap toggle; replies {ok, value} | {ok:false, error}
    {type:'control_request', request_id, request:{subtype:'worktree_status'}}
                                             # {ok, active, name?, path?, branch?, git_ok?, dirty_files?, commits?}
    {type:'control_request', request_id, request:{subtype:'worktree_exit', action:'keep'|'remove'}}
                                             # {ok, message} | {ok:false, error}; client uses a LONG timeout

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
import re
import threading
import time
import uuid as _uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.scheduled_tasks import (
    FALLBACK_WAKEUP_DELAY_SECONDS,
    SessionCronScheduler,
)
from src.server.server import AgentHandle
from src.utils.abort_controller import AbortController, AbortError

logger = logging.getLogger(__name__)

#: Wire-protocol version. Emitted in ``system/init`` so client and server can
#: refuse a mismatched major. Bump the major on any breaking shape change.
PROTOCOL_VERSION = "0.1.0"

#: Default ceiling for a permission round-trip. A disconnected/dead client must
#: not wedge a tool forever, so we default-deny after this (proposal §7).
DEFAULT_PERMISSION_TIMEOUT_S = 300.0

#: Default ceiling for an AskUserQuestion round-trip. Deliberately much longer
#: than the permission timeout above: that one guards against a dead client,
#: whereas this one is a human reading up to four questions and composing
#: answers. Expiring at 300s would yank the dialog out from under someone who
#: was still thinking. On expiry the user is not denied -- the tool returns a
#: "proceed autonomously" answer so the agent keeps moving (see
#: ``ask_user`` below).
DEFAULT_ASK_USER_TIMEOUT_S = 1800.0

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
    # Launch-time reasoning effort (``--effort``), seeding each session's
    # ``/effort`` level so the flag works on the interactive path and not
    # just headless ``-p``. Routed per provider family by
    # ``_AgentSession._turn_effort_routing``; ``None`` = whatever
    # ``settings.effort`` resolves to at the wire boundary.
    effort: str | None = None
    permission_mode: str = "default"
    # bypassPermissions AVAILABILITY, decoupled from the launch mode: True when
    # the user passed --dangerously-skip-permissions / --allow-dangerously-
    # skip-permissions to the launcher. Availability is what lets Shift+Tab
    # cycling and set_permission_mode reach bypassPermissions at runtime;
    # launching IN bypass mode implies it (see _build_runtime). Mirrors
    # isBypassPermissionsModeAvailable in
    # typescript/src/utils/permissions/permissionSetup.ts:941.
    is_bypass_available: bool = False
    # bypassPermissions SELECTABILITY — whether `/permissions` may choose Full
    # Access. Strictly weaker than is_bypass_available above, and deliberately a
    # SEPARATE field: availability ALSO relaxes plan mode (check.py
    # `should_bypass`), so reusing it to make the picker work would silently turn
    # /plan into full access in every session that defaults to Full Access.
    # Resolved once at the interactive launch boundary and forwarded via
    # --allow-select-bypass; never derived from settings here (see the
    # multi-tenant --http note in _build_runtime).
    bypass_selectable: bool = False
    max_turns: int = DEFAULT_MAX_TURNS
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    permission_timeout_s: float = DEFAULT_PERMISSION_TIMEOUT_S
    ask_user_timeout_s: float = DEFAULT_ASK_USER_TIMEOUT_S
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
    # Images attached (clipboard paste, /image, dropped path) but not yet sent,
    # as ``(image_id, PastedImage, expects_placeholder)``. Drained into the NEXT
    # user message as image content blocks -- the client holds no bytes (it shows
    # an ``[Image #N]`` chip), so this list is the single source of truth. Cleared by /clear and /resume so a
    # switched-away conversation cannot smuggle an image into a new one.
    _pending_images: list = field(default_factory=list)
    # Monotonic id behind the ``[Image #N]`` chip the client inserts. Increments
    # across the session rather than per prompt: the reference only requires
    # uniqueness within one prompt, and a session-wide counter satisfies that
    # while matching what users actually see (#1, #2, #3 as they paste).
    _image_seq: int = 0
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
    # /effort reasoning level. Carried to the query loop as
    # ``thinking_effort`` (see _turn_effort_routing) and turned into the
    # parameter each provider family accepts at the wire boundary:
    # ``output_config.effort`` on the Anthropic wire, a top-level
    # ``reasoning_effort`` body field on the OpenAI-compatible one.
    _effort: str | None = None
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
    # --worktree exit already serviced (keep or remove) — a second
    # worktree_exit is refused so the client can't double-remove.
    _worktree_done: bool = False
    # Self-improvement background review (hermes-agent port, src/memory).
    # The worker bumps the counter per completed real user turn and spawns
    # a memory-review fork every ``memory_review_interval`` turns; a
    # foreground Memory-tool call resets it (organic writes postpone the
    # nudge). Hydrated lazily from the restored conversation on the first
    # post-turn check (donor issue #22357).
    _turns_since_memory: int = 0
    _memory_counter_hydrated: bool = False
    _memory_review_thread: threading.Thread | None = None
    # End-of-turn recap + tab-acceptable suggestion (src/services/turn_recap):
    # a post-turn small-model fork, one at a time, emitting one system/recap
    # frame. Staleness is a TWO-key check at emit: ``_stats_turns`` equality
    # AND ``_recap_serial`` equality. The odometer alone has an ABA hole —
    # /clear zeroes it, /rewind recounts it DOWN, /resume re-seeds it, so a
    # slow fork can watch the value leave and come back while the
    # conversation underneath it was replaced. ``_recap_serial`` is
    # monotonic (same idea as ``_goal_rev``): bumped on EVERY completed
    # turn (btw/internal included) and on every conversation-identity
    # change (/clear, /rewind, /resume), so ABA is unrepresentable.
    _recap_thread: threading.Thread | None = None
    _recap_serial: int = 0
    # /loop + Cron* + ScheduleWakeup engine (docs/en/scheduled-tasks port).
    # The worker's idle branch pops due tasks and runs their prompts as
    # internal turns; _build_runtime hands this to tool_context so the
    # Cron*/ScheduleWakeup tools register real firing jobs on it.
    cron_scheduler: SessionCronScheduler = field(
        default_factory=SessionCronScheduler
    )
    # Last cron_status snapshot pushed to the client (JSON string) — the
    # post-turn push only re-emits when the state actually changed.
    _cron_push_json: str = ""

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
            # The active fusion model's name, "" when not fused. Carried on
            # init (not just the set_model reply) so a session started with
            # ``--model <fusion-name>`` shows it from the first frame.
            "fusion": self._active_fusion_name(),
            "provider": self.provider_name,
            "cwd": self.cwd,
            "tools": tools,
            "permission_mode": _current_mode(self.tool_context, self.config.permission_mode),
            # The client renders this next to the model name
            # (appLayout.tsx modelReasoningEffort). Nothing produced it
            # before, so the badge was permanently blank even with
            # --effort set; None keeps it hidden.
            "reasoning_effort": self._effort,
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
            # Attached-but-unsent images ride along with this prompt. Drained
            # (not copied) so a resend cannot duplicate them.
            #
            # NOT for an ephemeral "btw" message: ``_run_turn(btw=True)`` restores
            # a pre-turn snapshot afterwards, so draining here would consume the
            # image and then throw it away. Leave it queued for the real turn.
            if not ephemeral:
                content = self._drain_pending_images(content)
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
                turn_active = abort is not None
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
            # Under the lock and honoring the latch: a reply that already
            # landed must win over the sweep, or an answer the user really
            # submitted gets thrown away by an unrelated interrupt.
            with self._lock:
                for pending in pendings:
                    if pending.event.is_set():
                        continue
                    pending.reply = {"behavior": "deny", "message": "interrupted"}
                    pending.event.set()
            if abort is not None:
                abort.abort("user_interrupt")
            # ESC while IDLE clears the pending dynamic-loop wakeup
            # (docs/en/scheduled-tasks §Stop a loop: "press Esc while it is
            # waiting for the next iteration — this clears the pending
            # wakeup so the loop does not fire again"). Gated on no active
            # turn: the same interrupt control also arrives from busy-turn
            # Esc, interrupt-and-send busy-input mode, and double-Enter
            # force-send — aborting an unrelated in-flight turn must not
            # silently kill a waiting loop. Cron jobs are NEVER affected;
            # they stay until CronDelete or their 7-day expiry. The save
            # makes the stop durable — without it a crash before the next
            # turn-end save would resurrect the wakeup on --resume.
            if not turn_active:
                try:
                    if self.cron_scheduler.clear_wakeup():
                        self._push_cron_state(
                            "⟳ Loop stopped (Esc) — the pending wakeup was "
                            "cleared and the loop will not fire again."
                        )
                        self._save_session()
                except Exception:  # noqa: BLE001 — interrupt must never fail
                    logger.debug("[agent-server] wakeup clear on interrupt failed",
                                 exc_info=True)
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
            # available (--dangerously-skip-permissions / --allow-…) or
            # SELECTABLE (--allow-select-bypass, set by the interactive
            # launchers so `/permissions full` works). Without a guard,
            # `/permissions bypassPermissions` silently disabled the whole
            # permission gate in any session. Mirrors the onSetPermissionMode
            # contract in typescript/src/bridge/replBridge.ts:182-193.
            #
            # Selecting bypass sets the MODE only — it never flips the engine
            # availability flag, so a later /plan still restrains (that flag
            # also relaxes plan mode; see AgentServerConfig.bypass_selectable).
            pc = self.tool_context.permission_context
            if mode == "bypassPermissions" and not (
                getattr(pc, "is_bypass_permissions_mode_available", False)
                or self.config.bypass_selectable
            ):
                self._reply(request_id, {
                    "ok": False,
                    "error": "Full Access is not available in this session — "
                             "launch with --dangerously-skip-permissions or "
                             "--allow-dangerously-skip-permissions",
                })
                return
            _set_mode(self.tool_context, mode)
            # ch03 round-4 GAP A: the live gate home stays
            # tool_context.permission_context; the store dispatch runs
            # the centralized seams (listeners; future persistence).
            _dispatch_app_state(self, permission_mode=mode)
            # `persist` is set only by `/permissions` choosing one of the three
            # LEVELS — a standing preference that must survive relaunch, so a
            # user who deliberately dials DOWN to "Ask for approval" is not
            # silently returned to Full Access next launch. Shift+Tab cycling and
            # the raw-mode escape hatch stay transient.
            persisted = False
            if inner.get("persist") and self._may_persist_mode(mode):
                try:
                    from src.permissions.modes import set_settings_default_mode

                    persisted = bool(set_settings_default_mode(mode))  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001 — a failed write must not fail the set
                    logger.debug("[agent-server] defaultMode persist failed", exc_info=True)
            self._reply(request_id, {"ok": True, "mode": mode, "persisted": persisted})
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
            # Pass selectability: a session that defaults to Full Access has
            # availability False on purpose, so without this the first Shift+Tab
            # was a one-way exit out of the default mode.
            new_mode = get_next_permission_mode(
                pc, can_select_bypass=self.config.bypass_selectable,
            )
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
        if subtype == "list_model_providers":
            self._do_list_model_providers(request_id)
            return
        if subtype == "effort_options":
            self._do_effort_options(request_id, inner.get("provider"), inner.get("model"))
            return
        if subtype == "save_provider_key":
            self._do_save_provider_key(
                request_id, inner.get("slug"), inner.get("api_key")
            )
            return
        if subtype == "disconnect_provider":
            self._do_disconnect_provider(request_id, inner.get("slug"))
            return
        if subtype == "set_output_style":
            self._do_set_output_style(request_id, inner.get("style"))
            return
        if subtype == "set_logo_color":
            self._do_set_logo_color(request_id, inner.get("name"))
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
            # External CLAWCODEX.md @-imports (ClaudeMdExternalIncludesDialog, §6).
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
        if subtype == "memory_targets":
            await self._do_memory_targets(request_id)
            return
        if subtype == "memory_manage":
            # /memory <args> — bounded-store status + pending-write review
            # (src/memory/manage.py). Pure disk/settings reads + pending-file
            # mutations; safe on the control loop.
            try:
                from src.memory.manage import handle_memory_manage

                self._reply(request_id, {
                    "ok": True,
                    "text": handle_memory_manage(str(inner.get("arg") or "")),
                })
            except Exception as exc:  # noqa: BLE001 — must not kill the control channel
                self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        if subtype == "memory_edited":
            # /memory post-editor sync: the memory-file cache keys on cwd only
            # (no mtimes), so an external $EDITOR write stays invisible until a
            # bust — clear so the next turn's prompt assembly re-reads disk.
            try:
                from src.context_system.clawcodex_md import clear_memory_file_caches

                clear_memory_file_caches()
                self._reply(request_id, {"ok": True})
            except Exception as exc:  # noqa: BLE001
                self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        if subtype == "set_effort":
            self._do_set_effort(request_id, inner.get("effort"))
            return
        if subtype == "attach_image":
            await self._do_attach_image(
                request_id, inner.get("path"),
                expects_placeholder=bool(inner.get("placeholder")),
            )
            return
        if subtype == "clipboard_image":
            await self._do_clipboard_image(
                request_id, expects_placeholder=bool(inner.get("placeholder")),
            )
            return
        if subtype == "detect_file_drop":
            await self._do_detect_file_drop(
                request_id, inner.get("text"),
                expects_placeholder=bool(inner.get("placeholder")),
            )
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
                # The active fusion model's NAME, or "" when not on one.
                # ``model`` above stays the base model id (what serves the
                # turn, and what cost/context-window lookups key off), so
                # this is the only channel telling the client that images
                # are being routed through a second model.
                "fusion": self._active_fusion_name(),
                # OS-1 W3 — the /output-style no-arg display.
                "output_style": getattr(self.tool_context, "output_style_name", None) or "default",
                "available_output_styles": self._available_output_styles(),
                # /logo — the persisted startup-logo palette (None = default).
                "logo_color": _current_logo_color(),
                # /recap — end-of-turn recap + composer suggestion toggle.
                "recap": _recap_setting_enabled(),
            })
            return
        if subtype == "set_recap":
            self._do_set_recap(request_id, inner.get("value"))
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
                    str(Path.home() / ".clawcodex" / "plugins"),
                    str(Path(self.cwd) / ".clawcodex" / "plugins"),
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
                    str(Path.home() / ".clawcodex" / "plugins"),
                    str(Path(self.cwd) / ".clawcodex" / "plugins"),
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
        if subtype == "skill_command":
            await self._do_skill_command(request_id, inner.get("name"), inner.get("args"))
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
        if subtype == "eco":
            self._do_eco_command(request_id, inner.get("arg"))
            return
        if subtype == "fusion":
            self._do_fusion_command(request_id, inner.get("arg"))
            return
        if subtype == "vision":
            self._do_vision_command(request_id, inner.get("arg"))
            return
        if subtype == "worktree_status":
            await self._do_worktree_status(request_id)
            return
        if subtype == "worktree_exit":
            await self._do_worktree_exit(request_id, inner.get("action"))
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
                # An image attached but never sent belongs to the conversation
                # the user just discarded; carrying it into the fresh one would
                # silently attach it to an unrelated prompt.
                with self._lock:
                    self._pending_images = []
                # /clear starts a FRESH plan file (TS clearAllPlanSlugs on
                # clear — plans.ts:75-86): drop every session's slug so the
                # next plan-mode turn mints a new file instead of appending
                # to the cleared conversation's plan.
                try:
                    from src.utils.plans import clear_all_plan_slugs

                    clear_all_plan_slugs()
                except Exception:  # noqa: BLE001 — best-effort
                    logger.debug("[agent-server] plan slug clear failed", exc_info=True)
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
                # /clear starts a fresh conversation, and session-scoped
                # scheduled tasks go with it (docs/en/scheduled-tasks
                # §Limitations: "Starting a fresh conversation clears all
                # session-scoped tasks"). The on-disk strip keeps a /clear +
                # immediate-quit from resurrecting them on --resume.
                try:
                    for job in self.cron_scheduler.list_jobs():
                        self.cron_scheduler.delete(job.id)
                    self.cron_scheduler.clear_wakeup()
                    self._push_cron_state()
                    f = _sessions_dir() / f"{self.session_id}.json"
                    if f.exists():
                        data = json.loads(f.read_text(encoding="utf-8"))
                        if data.pop("scheduled_tasks", None) is not None:
                            f.write_text(json.dumps(data), encoding="utf-8")
                except Exception:  # noqa: BLE001 — never break /clear
                    logger.debug("[agent-server] scheduled-tasks clear failed",
                                 exc_info=True)
                # Fresh conversation, fresh odometer (token/cost totals are
                # process-wide spend and deliberately survive /clear).
                self._stats_turns = 0
                # Conversation identity changed — kill any in-flight recap.
                self._recap_serial += 1
                # Fresh conversation, fresh review cadence (the donor's
                # counters are per-agent-lifetime; a gateway reset builds a
                # new agent). Hydration stays done — the counter simply
                # restarts at zero with the emptied history.
                self._turns_since_memory = 0
                # /clear is a cache-boundary event (the request prefix
                # restarts with the emptied conversation) — rebuild the
                # prompt so the bounded-memory snapshot refreshes and the
                # fresh context starts with everything learned so far
                # (design-critic M2).
                self._rebuild_system_prompt()
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

    # ─── image attachment (clipboard paste / /image / dropped path) ────────

    def _drain_pending_images(self, content):
        """Prepend any attached images to this prompt's content.

        Returns ``content`` untouched when nothing is pending, so the common
        text-only path keeps sending a plain string (which
        ``_extract_prompt_content`` and ``add_user_message`` both prefer).

        Block order is images, then the USER'S TEXT, then the coordinate-mapping
        metadata. The metadata matters for screenshots (a downsampled image needs
        the scale factor or the model's pixel coordinates are wrong, see
        ``create_image_metadata_text``) but it must not come first, because three
        consumers flatten content by concatenating text blocks with no separator
        (``_extract_prompt_text``) and then read the FRONT of the result:

        * ``_parse_turn_budget`` -- its regexes are ``^``-anchored, so a leading
          ``[Image: source: …]`` makes a ``+500k`` directive silently no-op.
        * ``_first_prompt_preview`` -- takes the first text block, so ``/resume``
          and branch names would show the metadata instead of the prompt.
        * UserPromptSubmit hooks -- any ``^``-anchored or ``startswith`` matcher
          stops firing.

        Images stay first (the API prefers image-before-question).

        An image whose ``[Image #N]`` chip is gone from the text is DROPPED. That
        is how the chip doubles as un-attach: deleting it in the composer removes
        the image, matching the reference ("Images are only sent if their
        [Image #N] placeholder is still in the text", handlePromptSubmit.ts:225).
        The chip text itself stays in the prompt -- the reference leaves image
        refs inline and sends the bytes as separate blocks (history.ts:79).
        """
        with self._lock:
            pending = self._pending_images
            self._pending_images = []
        if not pending:
            return content

        from src.utils.image_processor import create_image_metadata_text

        referenced = _parse_image_refs(_content_text(content))
        blocks: list[dict] = []
        trailing: list[dict] = []
        for image_id, image, expects_placeholder in pending:
            if expects_placeholder and image_id not in referenced:
                logger.debug(
                    "[agent-server] dropping image #%s: its [Image #%s] chip was "
                    "deleted from the prompt",
                    image_id, image_id,
                )
                continue
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": image.base64,
                },
            })
            meta = create_image_metadata_text(image.dimensions, image.source_path)
            if meta:
                trailing.append({"type": "text", "text": meta})

        if not blocks:
            # Every pending image was un-attached; keep the plain-string shape.
            return content

        if isinstance(content, list):
            return blocks + content + trailing
        text = content if isinstance(content, str) else str(content or "")
        if text:
            blocks.append({"type": "text", "text": text})
        return blocks + trailing

    #: Cap on images queued for one prompt. Without it, N pastes all go out in a
    #: single request, the API 400s on total size, and because the drain is
    #: destructive the images are already gone -- the user sees an opaque error
    #: and has lost the attachments. Refusing the (N+1)th with a clear message is
    #: strictly better than losing all N.
    MAX_PENDING_IMAGES = 8

    def _queue_image(self, image, *, expects_placeholder: bool = False) -> int | None:
        """Append under the lock and return the new image's id, or None if full.

        Single entry point so every producer (clipboard, ``/image``, dropped
        path) is capped and numbered -- an inline append in one of them would
        silently bypass both.

        ``expects_placeholder`` says the caller will render an ``[Image #N]`` chip
        for this id, which makes the chip authoritative: delete it and the image
        is dropped at submit. Callers that render no chip (headless ``-p``, the
        VS Code bridge) leave it False and their images always send.
        """
        with self._lock:
            if len(self._pending_images) >= self.MAX_PENDING_IMAGES:
                return None
            self._image_seq += 1
            image_id = self._image_seq
            self._pending_images.append((image_id, image, expects_placeholder))
            return image_id

    def _too_many_images_error(self) -> dict:
        return {
            "error": (
                f"already holding {self.MAX_PENDING_IMAGES} attached images "
                "— send them or run /clear before attaching another"
            ),
        }

    def _attach_image(
        self,
        request_id: object,
        image,
        *,
        remainder: str = "",
        extra: dict | None = None,
        expects_placeholder: bool = False,
    ) -> None:
        """Queue ``image`` and reply in the client's ImageAttachResponse shape.

        ``id``/``count`` carry the number behind the client's ``[Image #N]`` chip.
        Both names are sent: ``ImageAttachResponse`` readers want ``id``,
        ``ClipboardPasteResponse`` readers want ``count``.
        """
        from src.utils.image_paste import dimensions_to_wire

        image_id = self._queue_image(image, expects_placeholder=expects_placeholder)
        if image_id is None:
            # Spread ``extra`` into the error too: the drop routes key on
            # ``matched``, so an error reply without it reads as "not a file
            # drop" and the cap message never reaches the user — the paste
            # silently inserts the path as text instead.
            self._reply(request_id, {**(extra or {}), **self._too_many_images_error()})
            return
        name = Path(image.source_path).name if image.source_path else "clipboard image"
        self._reply(request_id, {
            "attached": True,
            "count": image_id,
            "id": image_id,
            "name": name,
            "token_estimate": image.token_estimate,
            "remainder": remainder,
            **dimensions_to_wire(image.dimensions),
            **(extra or {}),
        })

    async def _do_attach_image(
        self, request_id: object, raw_path: object, *, expects_placeholder: bool = False
    ) -> None:
        """``/image <path>`` and the dropped-path paste route."""
        from src.utils.image_paste import as_image_file_path, try_read_image_from_path

        text = str(raw_path or "").strip()
        if not text:
            self._reply(request_id, {"error": "no path given"})
            return
        if as_image_file_path(text) is None:
            # Not an image path at all -- decline so the caller falls through to
            # its generic file-drop / plain-text handling rather than reporting
            # a phantom attachment.
            self._reply(request_id, {})
            return
        try:
            image = await asyncio.to_thread(
                try_read_image_from_path, text, cwd=Path(self.cwd or ".")
            )
        except Exception as exc:  # noqa: BLE001 — a bad paste must not kill the session
            logger.debug("[agent-server] attach_image failed", exc_info=True)
            self._reply(request_id, {"error": str(exc)})
            return
        if image is None:
            self._reply(request_id, {"error": f"could not read image: {text}"})
            return
        self._attach_image(request_id, image, expects_placeholder=expects_placeholder)

    async def _do_clipboard_image(
        self, request_id: object, *, expects_placeholder: bool = False
    ) -> None:
        """Ctrl+V / Cmd+V with an image on the clipboard."""
        from src.utils.image_paste import (
            clipboard_tooling_available,
            get_image_from_clipboard,
        )

        if not clipboard_tooling_available():
            # Distinguishable from "no image": the client falls back to a text
            # paste either way, but only this case is worth telling the user
            # about (install xclip / wl-clipboard).
            self._reply(request_id, {"unavailable": True})
            return
        try:
            image = await asyncio.to_thread(get_image_from_clipboard)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[agent-server] clipboard_image failed", exc_info=True)
            self._reply(request_id, {"error": str(exc)})
            return
        if image is None:
            self._reply(request_id, {})  # no image on the clipboard
            return
        self._attach_image(request_id, image, expects_placeholder=expects_placeholder)

    async def _do_detect_file_drop(
        self, request_id: object, raw_text: object, *, expects_placeholder: bool = False
    ) -> None:
        """Classify a paste that looks like a dragged-in path.

        Images are ATTACHED here (so a dropped screenshot works with one paste);
        non-image files are only reported, and the caller decides what to insert.
        """
        from src.utils.image_paste import (
            as_image_file_path,
            looks_like_dropped_path,
            resolve_dropped_file,
            try_read_image_from_path,
        )

        text = str(raw_text or "")
        if not looks_like_dropped_path(text):
            self._reply(request_id, {"matched": False})
            return
        cwd = Path(self.cwd or ".")
        if as_image_file_path(text) is not None:
            try:
                image = await asyncio.to_thread(
                    try_read_image_from_path, text, cwd=cwd
                )
            except Exception:  # noqa: BLE001
                logger.debug("[agent-server] detect_file_drop image read failed", exc_info=True)
                image = None
            if image is not None:
                # Same queue-and-reply as /image, plus the drop-specific fields.
                # ``text: ""`` because the client inserts an ``[Image #N]`` chip
                # itself rather than any text this reply carries.
                self._attach_image(
                    request_id, image,
                    extra={"matched": True, "is_image": True, "text": ""},
                    expects_placeholder=expects_placeholder,
                )
                return
        resolved = await asyncio.to_thread(resolve_dropped_file, text, cwd=cwd)
        if resolved is None:
            self._reply(request_id, {"matched": False})
            return
        # A real non-image file: hand back an @-reference the model can read
        # with the Read tool, which is what the path was pasted for.
        self._reply(request_id, {
            "matched": True,
            "is_image": False,
            "name": resolved.name,
            "text": f"@{resolved}",
        })

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

    def _active_fusion_name(self) -> str:
        """The active fusion model's name, or ``""`` when not on one.

        Type-checked rather than a bare ``getattr(...) or ""``: this value
        is serialized onto the NDJSON control channel, so a provider (or a
        test double) answering a non-string for ``fusion_name`` would put
        an unserializable object on the wire and break the channel for the
        whole session. Cheap insurance on an attribute read from a
        duck-typed object.
        """
        name = getattr(self.provider, "fusion_name", "")
        return name if isinstance(name, str) else ""

    def _available_models(self) -> list[str]:
        """Selectable models for the /model picker. Best-effort.

        Enabled fusion models are listed FIRST, then the provider's own
        models. CCR's contract is that a saved Fusion model "appears in
        routing and Agent Profiles like a normal model"; the picker is this
        client's equivalent surface, and a fusion model that cannot be found
        there is effectively unusable outside typed ``/model <name>``.

        Listed ahead of the provider's models because they are few,
        hand-created, and the reason the user opened the picker; the
        provider list can run to dozens of ids.
        """
        fusion: list[str] = []
        try:
            from src.providers.fusion_models import load_fusion_models

            fusion = [m.name for m in load_fusion_models() if m.enabled]
        except Exception:  # noqa: BLE001 — a bad record must not empty the picker
            logger.debug("[agent-server] fusion model listing failed", exc_info=True)
        try:
            fn = getattr(self.provider, "get_available_models", None)
            if callable(fn):
                models = fn()
                if models:
                    return fusion + [str(m) for m in models]
        except Exception:  # noqa: BLE001
            pass
        return fusion

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
            # Scheduled tasks ride the session file so --resume restores
            # unexpired jobs and a still-future wakeup (docs/en/
            # scheduled-tasks §Limitations). The restore-side rules
            # (7-day expiry, past one-shots dropped) live in
            # SessionCronScheduler.restore.
            try:
                sched_snap = self.cron_scheduler.snapshot()
                if sched_snap.get("jobs") or sched_snap.get("wakeup"):
                    payload["scheduled_tasks"] = sched_snap
            except Exception:  # noqa: BLE001 — snapshot is best-effort
                logger.debug("[agent-server] scheduled-tasks snapshot failed",
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
        # A fusion model is selected by name like any other model (CCR:
        # "a Fusion model appears in routing … like a normal model"), but
        # activating one swaps the whole provider rather than poking
        # ``.model``: it carries its own base provider + model, and the
        # vision wrapper has to be installed around it.
        if self._do_set_fusion_model(request_id, model):
            return
        # Canonicalized on BOTH sides: a session launched as ``--provider glm``
        # keeps that spelling in ``provider_name`` while the picker's rows
        # carry the canonical ``zai``, so a raw string compare would refuse a
        # same-provider switch and then send the client off to "switch" to a
        # provider it is already on.
        from src.providers import canonical_provider_name as _canonical

        if (
            isinstance(provider, str)
            and provider
            and _canonical(provider) != _canonical(self.provider_name or "")
        ):
            # ``provider_mismatch`` is the machine-readable half of this
            # refusal: the /model picker legitimately selects a model from
            # another provider (step 1 picks the provider, step 2 the model),
            # and its client re-drives the switch through ``set_provider``
            # — which does the registry rebuild this handler deliberately
            # will not — before re-applying the model. Without the flag the
            # client would have to pattern-match the error prose.
            self._reply(request_id, {
                "ok": False,
                "provider_mismatch": True,
                "provider": self.provider_name,
                "error": f"model '{model}' expects provider '{provider}' but this "
                         f"session is on '{self.provider_name}'",
            })
            return
        # Leaving a fusion model for a plain one: drop the wrapper, so the
        # session stops paying for vision substitution it no longer needs
        # and ``isinstance(provider, AnthropicProvider)`` checks downstream
        # see the real provider again.
        from src.providers.fusion_provider import FusionProvider

        target = self.provider
        unfusing = isinstance(target, FusionProvider)
        if unfusing:
            # Idle-only, exactly like /provider and the fusion branch above:
            # un-fusing replaces provider AND tool_registry, and the turn
            # runs on the worker thread while this handler runs on the main
            # loop — so a mid-turn swap pulls the registry out from under
            # live tool dispatch. The plain path below is only a ``.model``
            # assignment, which is why it needs no gate; this branch is not.
            with self._lock:
                active = self._current_abort is not None
            if active:
                self._reply(request_id, {
                    "ok": False,
                    "error": "cannot leave a fusion model during an active turn",
                })
                return
        try:
            if unfusing:
                # Inside the try: _install_provider rebuilds the registry,
                # re-registers MCP tools, and dispatches app state, any of
                # which can raise. Escaping here replies NOTHING — on stdio
                # the client's controlQuery hangs to its RPC timeout, and on
                # the WS transport it kills the inbound task and drops the
                # whole connection.
                self._install_provider(target.inner, self.provider_name, model)
                target = self.provider
            target.model = model
        except Exception as exc:  # noqa: BLE001
            self._reply(request_id, {"ok": False, "error": f"model switch failed: {exc}"})
            return
        # ch03 round-4 GAP A: on_change mirrors the choice into bootstrap and
        # persists (model, model_provider) to user settings — /model survives
        # restarts.
        _dispatch_app_state(self, main_loop_model=model)
        # Echo the provider alongside the model. This path cannot CHANGE the
        # provider (a cross-provider id is refused above), but the client
        # displays provider and model as one line and only ever learns the
        # provider from a reply — so a reply that omits it leaves the label
        # stuck on whatever ``init`` said, which is wrong the moment a
        # cross-provider switch lands via set_provider + a retry through here.
        # The fusion and set_provider paths already echo it; this was the gap.
        response: dict = {
            "ok": True,
            "model": getattr(self.provider, "model", model),
            "provider": self.provider_name,
        }
        known = self._available_models()
        if known and model not in known:
            response["warning"] = (
                f"'{model}' is not in {self.provider_name}'s model list — "
                "the API may reject it"
            )
        self._reply(request_id, response)

    def _do_set_fusion_model(self, request_id: object, model: str) -> bool:
        """Activate ``model`` if it names a fusion model. Returns whether handled.

        Returns True (having replied) when ``model`` matched a saved fusion
        model — enabled or not — so the caller stops. Returns False when the
        name is not a fusion model, leaving the plain-model path to run.

        Selecting a fusion model whose base lives on another provider is a
        genuine cross-provider switch, so this goes through
        :meth:`_install_provider` — the same rebuild ``/provider`` does —
        rather than the ``.model`` assignment a same-provider switch needs.
        """
        from src.providers.fusion_models import get_fusion_model

        try:
            fusion = get_fusion_model(model)
        except Exception:  # noqa: BLE001 — a config problem must not block /model
            logger.debug("[agent-server] fusion lookup failed", exc_info=True)
            return False
        if fusion is None:
            return False
        if not fusion.enabled:
            self._reply(request_id, {
                "ok": False,
                "error": f"fusion model '{fusion.name}' is disabled — run "
                         f"`/fusion enable {fusion.name}` first",
            })
            return True

        # Idle-only, matching /provider: the turn runs on the worker thread
        # while this handler runs on the main loop, so swapping the provider
        # and tool registry mid-turn would pull them out from under it.
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {
                "ok": False,
                "error": "cannot switch to a fusion model during an active turn",
            })
            return True

        try:
            from src.providers import provider_has_credentials, resolve_api_key
            from src.providers.fusion_provider import build_fusion_provider

            # Check credentials for BOTH halves up front. Without the vision
            # check the switch succeeds and then every image silently
            # degrades to a "vision model failed" note — a failure the user
            # would only discover mid-task.
            for ref, role in ((fusion.base, "base"), (fusion.vision, "vision")):
                key = resolve_api_key(ref.provider)
                if not provider_has_credentials(ref.provider, key):
                    self._reply(request_id, {
                        "ok": False,
                        "error": f"fusion model '{fusion.name}' needs credentials for "
                                 f"its {role} provider '{ref.provider}' (no API key "
                                 f"configured)",
                    })
                    return True

            fused = build_fusion_provider(fusion)
            self._install_provider(
                fused, fusion.base.provider, fusion.base.model,
                # Persist the NAME the user selected, so a restart restores
                # the fusion model rather than the bare base model.
                persist_model=fusion.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] fusion model switch failed")
            self._reply(request_id, {
                "ok": False, "error": f"fusion model switch failed: {exc}",
            })
            return True

        self._reply(request_id, {
            "ok": True,
            # The fusion NAME is echoed as the switch result: it is what the
            # user selected and what the client's model line should show.
            # ``fusion_base`` carries the id actually on the wire.
            "model": fusion.name,
            "fusion": fusion.name,
            "fusion_base": fusion.base.selector,
            "fusion_vision": fusion.vision.selector,
            "provider": fusion.base.provider,
        })
        return True

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
            from src.providers import get_provider_class, provider_has_credentials, resolve_api_key

            provider_cfg = get_provider_config(name)
            api_key = resolve_api_key(name, provider_cfg)
            if not provider_has_credentials(name, api_key):
                self._reply(request_id, {"ok": False, "error": f"provider '{name}' is not configured (no API key)"})
                return
            provider_cls = get_provider_class(name)
            model = provider_cfg.get("default_model")
            provider = provider_cls(api_key=api_key, base_url=provider_cfg.get("base_url"), model=model)
            self._install_provider(provider, name, model)
            self._reply(request_id, {"ok": True, "provider": name, "model": model or ""})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_provider failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_effort_options(
        self, request_id: object, provider: object, model: object
    ) -> None:
        """The /model picker's step 3: the effort levels the model just picked
        in step 2 will actually accept.

        Queried per selection rather than ridden along on
        ``list_model_providers`` — the ladder is a property of the MODEL, and
        some providers enumerate hundreds of them, so answering for all of
        them upfront would bloat every picker open to serve one row.

        ``current`` lets the picker preselect the session's live level.
        Defaults fall back to the session's own provider so a caller that
        omits ``provider`` still gets a sensible answer.
        """
        try:
            from src.providers.effort_options import effort_options

            slug = provider if isinstance(provider, str) and provider else self.provider_name
            name = model if isinstance(model, str) and model else getattr(self.provider, "model", "")
            options = effort_options(slug, name)
        except Exception as exc:  # noqa: BLE001 — never break the control channel
            logger.exception("[agent-server] effort_options failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        self._reply(request_id, {
            "ok": True,
            "current": self._effort or "",
            "model": name,
            "provider": slug,
            **options,
        })

    def _do_list_model_providers(self, request_id: object) -> None:
        """Every provider ClawCodex knows about — the /model picker's step 1.

        ``get_settings`` reports only the ACTIVE provider, so a picker built on
        it can only ever render one row (the bug this fixes: the list showed
        `anthropic · 22 models` and nothing else). The catalog enumerates the
        real registry instead, and the active provider's row carries this
        session's live model list so endpoint-discovered catalogues show their
        real models.
        """
        try:
            from src.providers.catalog import provider_catalog

            providers = provider_catalog(
                current=self.provider_name,
                current_models=self._available_models(),
                current_ready=self.provider is not None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] list_model_providers failed")
            self._reply(request_id, {"ok": False, "error": str(exc), "providers": []})
            return
        self._reply(request_id, {
            "ok": True,
            "model": getattr(self.provider, "model", None),
            # Same channel get_settings uses: on a fused session ``model`` is
            # the BASE id that serves the turn, so without this the picker
            # would mark the base row current instead of the fusion entry the
            # user actually selected.
            "fusion": self._active_fusion_name(),
            "provider": self.provider_name,
            "providers": providers,
        })

    def _do_save_provider_key(
        self, request_id: object, slug: object, api_key: object
    ) -> None:
        """Persist an API key typed into the picker's inline key stage.

        Writes where ``clawcodex login`` writes (the global config's
        ``providers.<id>`` block) so the two paths agree and a key set here
        survives a restart. An existing ``base_url`` / ``default_model`` is
        preserved — seeding the defaults unconditionally would silently
        clobber a user's custom endpoint.

        The existing block is read from the GLOBAL tier, never the merged
        view. ``get_provider_config`` merges the project/local tiers, and this
        writes globally — so reading merged would launder a repo-committable
        ``providers.*.base_url`` into the user's global config, permanently and
        for every other project, paired with the key they just typed. That is
        the exact redirect ``_UNTRUSTED_TIER_BLOCKED_KEYS`` exists to contain.
        """
        if not isinstance(slug, str) or not slug.strip():
            self._reply(request_id, {"ok": False, "error": "missing provider"})
            return
        if not isinstance(api_key, str) or not api_key.strip():
            self._reply(request_id, {"ok": False, "error": "missing api key"})
            return
        try:
            from src.config import _get_default_manager, set_api_key
            from src.providers import PROVIDER_INFO, canonical_provider_name
            from src.providers.catalog import provider_catalog

            pid = canonical_provider_name(slug.strip())
            info = PROVIDER_INFO.get(pid)
            if info is None:
                self._reply(
                    request_id, {"ok": False, "error": f"unknown provider '{slug}'"}
                )
                return
            global_blocks = _get_default_manager().load_global().get("providers") or {}
            existing = global_blocks.get(pid) or {}
            if not isinstance(existing, dict):
                existing = {}
            set_api_key(
                pid,
                api_key=api_key.strip(),
                base_url=existing.get("base_url") or info.get("default_base_url"),
                default_model=(
                    existing.get("default_model") or info.get("default_model")
                ),
            )
            row = next(
                (
                    r
                    for r in provider_catalog(
                        current=self.provider_name,
                        current_models=self._available_models(),
                        current_ready=self.provider is not None,
                    )
                    if r["slug"] == pid
                ),
                None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] save_provider_key failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        self._reply(request_id, {"ok": True, "provider": row})

    def _do_disconnect_provider(self, request_id: object, slug: object) -> None:
        """Clear a provider's stored credentials (the picker's ^d).

        Refuses the ACTIVE provider: this session holds an already-constructed
        provider instance, so pulling its key would leave the next turn firing
        at an endpoint it can no longer authenticate against. Switch away
        first.

        Clears all three places a key can live — the config ``providers.<id>``
        block, the config ``env`` block, and a subscription OAuth login — then
        re-probes. A key exported in the real shell environment cannot be
        removed from here, so that case is reported honestly rather than
        claiming a disconnect that did not happen.
        """
        if not isinstance(slug, str) or not slug.strip():
            self._reply(request_id, {"ok": False, "error": "missing provider"})
            return
        try:
            from src.config import _get_default_manager
            from src.providers import (
                PROVIDER_INFO,
                canonical_provider_name,
                provider_env_vars,
                provider_has_credentials,
                provider_requires_api_key,
                resolve_api_key,
            )
            from src.providers.catalog import exclusive_env_vars
            from src.secret_store import delete_secret, get_secret

            pid = canonical_provider_name(slug.strip())
            if pid not in PROVIDER_INFO:
                self._reply(
                    request_id, {"ok": False, "error": f"unknown provider '{slug}'"}
                )
                return
            if pid == canonical_provider_name(self.provider_name or ""):
                self._reply(request_id, {
                    "ok": False,
                    "disconnected": False,
                    "error": f"'{pid}' is the active provider — switch to another "
                             "provider before disconnecting it",
                })
                return

            removed = False
            mgr = _get_default_manager()
            cfg = mgr.load_global()
            blocks = cfg.get("providers")
            if isinstance(blocks, dict):
                # Rebuild rather than pop in place: load_global returns a
                # SHALLOW copy, so mutating a nested block reaches the
                # manager's cache and can desync it from disk when nothing
                # ends up being written.
                rebuilt = dict(blocks)
                touched = False
                for key, block in blocks.items():
                    if not isinstance(block, dict):
                        continue
                    if canonical_provider_name(key) != pid:
                        continue
                    if str(block.get("api_key") or "").strip():
                        stripped = {k: v for k, v in block.items() if k != "api_key"}
                        rebuilt[key] = stripped
                        touched = True
                if touched:
                    cfg["providers"] = rebuilt
                    mgr.save_global(cfg)
                    removed = True
            # Sampled BEFORE the delete loop. ``delete_secret`` pops the name
            # from ``os.environ`` as well as the config block, so reading this
            # afterwards would find nothing precisely when the name exists in
            # BOTH places — and that is the case that matters: the config copy
            # goes, the shell export survives to resurrect it next launch, and
            # the reply would have claimed a clean disconnect.
            #
            # Presence in os.environ alone does NOT mean the user exported it:
            # ``set_secret`` mirrors every config-block write into the live
            # process. The value is what separates them — a mirrored secret
            # matches its config entry, a shell export does not (a name absent
            # from the block compares against "").
            import os as _os

            from src.secret_store import CONFIG_ENV_KEY

            stored_env = cfg.get(CONFIG_ENV_KEY)
            if not isinstance(stored_env, dict):
                stored_env = {}
            shell_env = []
            for _name in provider_env_vars(pid):
                live = (_os.environ.get(_name) or "").strip()
                if live and live != str(stored_env.get(_name) or "").strip():
                    shell_env.append(_name)

            # Only names this provider EXCLUSIVELY owns. Shared ones belong to
            # another provider too (nvidia-nim lists DEEPSEEK_API_KEY), and
            # deleting them would destroy a credential the user set for
            # something else.
            owned, shared = exclusive_env_vars(pid)
            # …and never a name the LIVE session authenticates through. The
            # active-provider guard above only compares slugs, so it misses the
            # borrowed-name case: a session running on nvidia-nim via
            # DEEPSEEK_API_KEY would lose its credential when the user
            # disconnects deepseek, which owns that name outright. Whatever the
            # slug, disconnect must not de-authenticate the session you are in.
            active = canonical_provider_name(self.provider_name or "")
            in_use = set(provider_env_vars(active)) if active else set()
            kept_in_use: list[str] = []
            for env_name in owned:
                if env_name in in_use:
                    kept_in_use.append(env_name)
                    continue
                if delete_secret(env_name):
                    removed = True
            # Kept SEPARATE from kept_shared: the two have different remedies.
            # A contested name is fixed by disconnecting the co-owner; a name
            # the live session resolves through is fixed by switching
            # providers first — and folding them together emits the co-owner
            # advice for the in-use case, which names the active provider that
            # this handler's own guard will refuse to disconnect.
            kept_shared = [e for e in shared if (get_secret(e) or "").strip()]
            if pid == "anthropic":
                from src.auth.anthropic_subscription import remove_credentials

                removed = remove_credentials() or removed
            elif pid == "openai":
                from src.auth.openai_subscription import remove_credentials

                removed = remove_credentials() or removed

            # ``or bool(shell_env)``: a shell export that delete_secret popped
            # out of this process is still in the user's environment and will
            # be back next launch, so the provider is NOT disconnected even
            # though re-probing now finds nothing.
            still = provider_has_credentials(pid, resolve_api_key(pid)) or bool(shell_env)
            keyless = not provider_requires_api_key(pid)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] disconnect_provider failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})
            return
        response: dict = {
            "ok": True,
            "disconnected": removed and not still,
            "still_authenticated": still,
        }
        if kept_shared:
            response["kept_shared_env"] = kept_shared
        if kept_in_use:
            response["kept_in_use_env"] = kept_in_use
        if still:
            if keyless:
                # A local server (Ollama / vLLM / SGLang) accepts any or no
                # token, so it is never "disconnected" in the credential sense.
                detail = f"'{pid}' is a local server and needs no credentials"
            elif shell_env:
                # Checked BEFORE the shared case: when a name is both shared
                # and shell-exported, "unset it in your shell" is the half the
                # user can act on.
                detail = (
                    f"'{pid}' still authenticates — {', '.join(shell_env)} is "
                    "exported in your shell, which only your shell can unset"
                )
            elif kept_in_use:
                # The remedy here is switching providers, NOT disconnecting
                # the co-owner (that IS the active provider, which the guard
                # above refuses) and NOT unsetting the name (the live value is
                # the config copy this handler just declined to delete).
                detail = (
                    f"'{pid}' still authenticates via {', '.join(kept_in_use)} — "
                    f"this session's provider '{active}' resolves through it, so "
                    "it was left in place; switch to another provider first"
                )
            elif kept_shared:
                # Name the co-owner and the way out; "another provider also
                # uses it" alone leaves the user with no next step.
                owners = sorted(
                    o
                    for o in PROVIDER_INFO
                    if o != pid and set(provider_env_vars(o)) & set(kept_shared)
                )
                detail = (
                    f"'{pid}' still authenticates via {', '.join(kept_shared)}, "
                    f"also used by {', '.join(owners)} — left in place; disconnect "
                    f"that too, or unset {kept_shared[0]} yourself"
                )
            else:
                # Reached when the surviving key lives somewhere this handler
                # does not write: a project/local `.clawcodex/config.json`, or
                # a subscription login. Naming the environment here would send
                # the user hunting through shell rc files for nothing.
                detail = (
                    f"'{pid}' still authenticates from outside the global "
                    "config (project config or a subscription login)"
                )
            response["error"] = detail
        self._reply(request_id, response)

    def _install_provider(
        self, provider: Any, name: str, model: str | None,
        *, persist_model: str | None = None,
    ) -> None:
        """Adopt ``provider`` as the session's provider, rebuilding the registry.

        Extracted from :meth:`_do_set_provider` so the fusion-model branch of
        :meth:`_do_set_model` performs an IDENTICAL swap — a fusion model
        carries its own base provider, so selecting one can be a
        cross-provider switch, and re-deriving these steps separately is how
        the two paths would drift (a missed MCP re-register or app-state
        dispatch is silent).

        ``model`` is the id to record as current; for a fusion model that is
        the base model, since that is what serves the turn (see
        ``FusionProvider.model``).

        ``persist_model`` is what gets PERSISTED as the user's choice, when
        it differs from ``model``. For a fusion model that is the fusion
        NAME: persisting the base id instead would restore the next session
        as the plain base model and silently drop vision — the user picked
        ``deepseek-v4-pro-V``, not ``deepseek-v4-pro``. The restore side
        (``settings.get_persisted_model``) resolves that name back to the
        fusion record.
        """
        from src.tool_system.defaults import build_default_registry

        registry = build_default_registry(provider=provider)
        cfg = self.config
        # Canonicalize up front so alias-form flags (e.g. KillShell ->
        # TaskStop) resolve while the tool is still registered.
        allow = (
            registry.canonicalize_names(cfg.allowed_tools)
            if cfg.allowed_tools
            else None
        )
        deny = (
            registry.canonicalize_names(cfg.disallowed_tools)
            if cfg.disallowed_tools
            else None
        )
        if allow is not None:
            _filter_registry(registry, keep=lambda n: n.lower() in allow)
        if deny is not None:
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
        _dispatch_app_state(self, main_loop_model=persist_model or model)
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
        reply = {"ok": True, "thinking": bool(self._thinking)}
        # Reciprocal of the note in _do_set_effort: on the Anthropic path
        # effort rides inside the thinking block, so turning thinking OFF
        # discards a level the user already set. Whichever order the two
        # commands arrive in, say it once.
        if self._thinking is False and self._effort:
            reply["note"] = (
                f"Effort {self._effort} is discarded while extended thinking "
                "is off."
            )
        self._reply(request_id, reply)

    def _do_set_language(self, request_id: object, language: object) -> None:
        """Set the preferred response language (LanguagePicker, §6) and recompose
        the system prompt so the agent honors it. Empty clears it."""
        lang = str(language or "").strip()
        self._language = lang or None
        if self._base_system_prompt is not None:
            self.system_prompt = self._compose_with_plan(self._base_system_prompt)
        self._reply(request_id, {"ok": True, "language": self._language or ""})

    def _do_plan(self, request_id: object, action: object, text: object) -> None:
        """/plan status: the CC plan-MODE command's server half.

        Repurposed from the hermes-legacy "inject plan text into the system
        prompt" control (that read path — ``_compose_with_plan`` picking up a
        pre-existing legacy plan file at bootstrap/resume — stays intact and
        inert-unless-a-legacy-file-exists; only this control changed owners).
        The client composes the TS ``/plan`` semantics
        (commands/plan/plan.tsx): not in plan mode → ``set_permission_mode
        plan`` (+ optional prompt submit); in plan mode → show this status's
        plan file content."""
        try:
            from src.utils.plans import get_plan, get_plan_file_path

            mode = _current_mode(self.tool_context, self.config.permission_mode)
            self._reply(request_id, {
                "ok": True,
                "mode": mode,
                "plan": get_plan(),
                "plan_file_path": str(get_plan_file_path()),
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] plan failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    async def _do_memory_targets(self, request_id: object) -> None:
        """/memory picker rows (the TS MemoryFileSelector data): the shared
        ``build_memory_options`` hierarchy — synthetic User/Project candidates
        plus every loaded memory file — serialized for the TUI overlay, which
        owns the ensure-create + ``$EDITOR`` spawn (memory.tsx)."""
        try:
            from src.command_system.memory_command import build_memory_options

            options = await build_memory_options(self.cwd)
            self._reply(request_id, {
                "ok": True,
                "targets": [
                    {"path": o.value, "label": o.label, "description": o.description or ""}
                    for o in options
                ],
            })
        except Exception as exc:  # noqa: BLE001
            self._reply(request_id, {"ok": False, "error": str(exc), "targets": []})

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

    def _turn_effort_routing(self) -> tuple[Any, str | None]:
        """Return ``(provider_for_this_turn, thinking_effort)`` for ``/effort``.

        The two provider families take reasoning effort as DIFFERENT wire
        parameters — ``output_config.effort`` on the Anthropic wire (incl.
        Minimax, which speaks the Anthropic shape), a top-level
        ``reasoning_effort`` body field on the OpenAI-compatible one — and
        sending one family's shape to the other is a hard 400. Probed
        2026-07-25 against claude-opus-5: ``400 invalid_request_error —
        reasoning_effort: Extra inputs are not permitted``.

        That routing now lives ENTIRELY at the wire boundary in
        ``query.py::_call_model_sync``, which branches on the same
        ``is_anthropic_wire`` predicate. So this method just hands the level
        over as ``thinking_effort`` and does not wrap the provider.

        It used to wrap OpenAI-compatible providers in an ``_EffortProvider``
        that injected ``extra_body.reasoning_effort`` itself, because
        ``query.py`` emitted effort only on its Anthropic branch. When
        query.py learned the OpenAI-compatible half, the two injection sites
        collided: routing returned ``thinking_effort=None`` for this family,
        so ``resolve_thinking_effort`` fell through to ``settings.effort``
        and filled ``extra_body`` first, and the wrapper's ``setdefault``
        then found the key taken. The session's ``/effort`` was silently
        discarded in favour of the persisted setting — an inversion of the
        documented precedence (explicit beats persisted), reproducible as
        ``/effort max`` + ``settings.effort medium`` putting ``medium`` on
        the wire. One level, one injection site, no drift.
        """
        return self.provider, (self._effort or None)

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
            # The accepted ladder IS the settings source of truth
            # (low|medium|high|xhigh|max) — the same set ``--effort``,
            # ``/effort`` on the other surfaces, and settings.effort accept.
            # Before this, the list was hardcoded to
            # (minimal|low|medium|high): ``/effort xhigh`` and
            # ``/effort max`` were REJECTED in the interactive TUI despite
            # both being valid Claude levels (xhigh + max probed on
            # claude-opus-5 2026-07-25), while ``minimal`` — which is a
            # GPT-5 level, not a Claude one, and is absent from every other
            # surface — was accepted. Keeping ``minimal`` here would be
            # actively harmful on the Anthropic path: it is not in
            # VALID_THINKING_EFFORT_LEVELS, so resolve_thinking_effort
            # treats it as "no explicit value" and silently substitutes
            # settings.effort — i.e. ``/effort minimal`` could emit `max`
            # while the TUI echoed "minimal". OpenAI-compat users take
            # ``low``.
            from src.settings.constants import VALID_EFFORT_VALUES

            levels = tuple(v for v in VALID_EFFORT_VALUES if v)
            choices = "|".join((*levels, "auto", "ultracode"))
            if not isinstance(effort, str):
                self._reply(request_id, {
                    "ok": False,
                    "error": f"invalid effort '{effort}' ({choices})",
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
            if a in levels:
                self._effort = a
                set_ultracode_session(False)  # a real level exits ultracode mode
                reply = {"ok": True, "effort": a, "ultracode": False}
                # Effort rides INSIDE the extended-thinking block on the
                # Anthropic path (query.py gates the whole thing on
                # ``extended_thinking is not False``), so /thinking off
                # silently discards it. Say so rather than reporting a
                # level that will not reach the wire — doubly worth saying
                # on Opus 5, where thinking is on by DEFAULT, so
                # /thinking off doesn't stop the model reasoning, it only
                # throws the effort setting away.
                if self._thinking is False:
                    reply["note"] = (
                        "Extended thinking is off, which discards effort — "
                        "run /thinking on for it to take effect."
                    )
                self._reply(request_id, reply)
                return
            self._reply(request_id, {
                "ok": False,
                "error": f"invalid effort '{effort.strip()}' ({choices})",
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
        ``.clawcodex/workflows/*.py`` workflow commands, read fresh from disk each
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

    async def _do_skill_command(self, request_id: object, name: object, args: object) -> None:
        """Expand a user-typed skill slash command (``/loop 5m check ci``)
        into its prompt for the client to submit as a user turn — the
        missing producer for the TUI's ``{type:'skill'}`` dispatch. Uses
        the exact expansion path the model-side Skill tool uses
        (bundled ``get_prompt_for_command`` builders, disk SKILL.md
        rendering, MCP skills), so typed and model invocations of the
        same skill can't drift. Runs in an executor thread: expansion
        scans skill directories and a disk skill's embedded ``!`cmd```
        lines execute shell inline — neither belongs on the asyncio
        loop that pumps the WebSocket.

        Known scope limit: only the rendered prompt travels back; a
        skill's ``context_modifier`` (allowed-tools / model / effort
        scoping) applies on the model-side Skill-tool path only."""
        try:
            if not isinstance(name, str) or not name.strip():
                self._reply(request_id, {"ok": False, "error": "missing skill name"})
                return
            if self.tool_context is None:
                self._reply(request_id, {"ok": False, "error": "session not ready"})
                return
            from src.tool_system.tools.skill import run_markdown_skill

            skill_name = name.strip().lstrip("/")
            arg_str = args if isinstance(args, str) else ""
            result = await asyncio.get_running_loop().run_in_executor(
                None, lambda: run_markdown_skill(skill_name, arg_str, self.tool_context)
            )
            out = result.output if isinstance(result.output, dict) else {}
            prompt = out.get("prompt")
            if result.is_error or not (isinstance(prompt, str) and prompt.strip()):
                self._reply(request_id, {
                    "ok": False,
                    "error": str(out.get("error") or f"unknown skill '{skill_name}'"),
                })
                return
            self._reply(request_id, {
                "ok": True,
                "name": skill_name,
                "prompt": prompt,
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] skill_command failed")
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

    def _do_set_logo_color(self, request_id: object, name: object) -> None:
        """Persist the startup-logo palette (the TUI's /logo — openclaude's
        ``saveGlobalConfig(c => ({...c, logoColor: chosen}))``). A pure global
        config write with no agent/system-prompt impact, so unlike
        ``set_output_style`` there is no idle-only gate."""
        from src.utils.logo_palettes import LOGO_PALETTE_NAMES, is_logo_palette_name

        if not is_logo_palette_name(name):
            self._reply(
                request_id,
                {
                    "ok": False,
                    "error": f"invalid palette (valid: {', '.join(LOGO_PALETTE_NAMES)})",
                    "available_palettes": LOGO_PALETTE_NAMES,
                },
            )
            return
        try:
            from src.config import set_logo_color

            set_logo_color(str(name))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_logo_color failed")
            self._reply(request_id, {"ok": False, "error": f"failed to persist: {exc}"})
            return
        self._reply(request_id, {"ok": True, "logo_color": name})

    def _do_set_recap(self, request_id: object, value: object) -> None:
        """Flip + persist the end-of-turn recap toggle (the TUI's /recap).

        Delegates to :func:`src.config.set_recap_enabled` — the
        ``set_effort`` write pattern: ``settings.recap_enabled`` in the
        GLOBAL config (project/local overrides still win at merge) plus a
        settings-cache invalidation so the very next post-turn check sees
        it — no restart, no session rebuild.
        """
        mode = str(value or "").strip().lower()
        if mode not in ("on", "off"):
            self._reply(
                request_id,
                {"ok": False, "error": "usage: /recap [on|off|status]"},
            )
            return
        try:
            from src.config import set_recap_enabled

            set_recap_enabled(mode == "on")
        except Exception as exc:  # noqa: BLE001
            logger.exception("[agent-server] set_recap failed")
            self._reply(
                request_id, {"ok": False, "error": f"failed to persist: {exc}"}
            )
            return
        # Reply with the EFFECTIVE post-write state, not the request: the
        # write is global, and a project/local settings override wins at
        # merge — "/recap off" must not print "off" while the feature stays
        # on. When they disagree, say why.
        effective = "on" if _recap_setting_enabled() else "off"
        payload: dict[str, Any] = {"ok": True, "value": effective}
        if effective != mode:
            payload["note"] = (
                "global preference saved, but a project/local settings "
                "override keeps it " + effective
            )
        self._reply(request_id, payload)

    def _rebuild_system_prompt(self) -> bool:
        """Rebuild the cached system prompt from the live tool context.

        The single idiom for every mid-session cache-boundary event
        (output-style switch, coordinator-mode flip on resume, /clear,
        /resume): resolve the active style, run
        ``build_effective_system_prompt`` (whose memory_store section
        reloads the bounded-memory snapshot from disk inline — the
        donor's ``invalidate_system_prompt → load_from_disk`` coupling),
        and re-compose the /plan section. Returns False when the rebuild
        wasn't possible (no tool context / build failure); the previous
        prompt stays in place.
        """
        tc = self.tool_context
        if tc is None:
            return False
        try:
            from src.outputStyles import resolve_output_style
            from src.query.agent_loop_compat import build_effective_system_prompt

            style_prompt = resolve_output_style(
                getattr(tc, "output_style_name", None),
                getattr(tc, "output_style_dir", None),
            ).prompt
            self._base_system_prompt = build_effective_system_prompt(
                style_prompt, tc, provider=self.provider,
                mcp_servers=self._mcp_server_infos(),
            )
            self.system_prompt = self._compose_with_plan(self._base_system_prompt)
            return True
        except Exception:  # noqa: BLE001 — keep the old prompt on failure
            logger.debug("[agent-server] system prompt rebuild failed",
                         exc_info=True)
            return False

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
            # Rebuild the system prompt so the style section takes effect next
            # turn (keeps the style set even if the rebuild is unavailable).
            self._rebuild_system_prompt()
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
            # Same reasoning as /clear: an image attached but never sent belongs
            # to the conversation being switched away from. Carrying it over
            # would attach it to the first prompt of the resumed session.
            with self._lock:
                self._pending_images = []
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
            # Conversation identity changed — kill any in-flight recap (the
            # re-seeded odometer can coincide with the captured serial).
            self._recap_serial += 1
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
            # /resume is a cache-boundary event regardless of a mode flip: a
            # different conversation is loaded (the request prefix restarts),
            # and the rebuilt prompt recaptures the bounded-memory snapshot —
            # memory written since this prompt was last built (e.g. by a
            # background review in the previous conversation) becomes visible
            # (design-critic M2: "future sessions start corrected" must hold
            # for in-process session switches, not just new processes).
            self._rebuild_system_prompt()
            # Re-hydrate the review cadence from the RESUMED session's
            # odometer on its next completed turn (donor issue #22357) —
            # the previous conversation's counter is meaningless here.
            self._turns_since_memory = 0
            self._memory_counter_hydrated = False
            if mode_banner:
                # A mode flip also changes the advertised tool set. The tool
                # list was sent once in system/init — re-emit so the client's
                # cached list tracks the mode (the client's init handler
                # re-sets session info idempotently).
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
            # Scheduled-task restore (docs/en/scheduled-tasks §Limitations:
            # resuming restores recurring tasks within 7 days of creation
            # and one-shots whose time hasn't passed; the scheduler's
            # restore() applies those rules). The resumed conversation
            # REPLACES the current one, so its tasks do too — clear the
            # live scheduler first or the discarded conversation's jobs
            # and wakeup would merge into the resumed session.
            try:
                for job in self.cron_scheduler.list_jobs():
                    self.cron_scheduler.delete(job.id)
                self.cron_scheduler.clear_wakeup()
                saved_sched = data.get("scheduled_tasks")
                restored_n = (
                    self.cron_scheduler.restore(saved_sched)
                    if isinstance(saved_sched, dict) else 0
                )
                if restored_n:
                    self._push_cron_state(
                        f"⏰ Restored {restored_n} scheduled task(s) "
                        "from the saved session."
                    )
                else:
                    self._push_cron_state()
            except Exception:  # noqa: BLE001 — must not break resume
                logger.debug("[agent-server] scheduled-tasks restore failed",
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
                # Injected context (plan-mode / task-reminder attachments) is
                # not a rewind boundary — without this, /rewind 1 lands on the
                # most recent reminder instead of the user's actual message.
                if getattr(m, "isMeta", False):
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
            # `is_prompt` and `_count_prompt_turns` now apply the same isMeta
            # exclusion, so the recount matches the boundaries rewind saw.
            self._stats_turns = _count_prompt_turns(msgs)
            # Conversation identity changed — kill any in-flight recap.
            self._recap_serial += 1
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
                clawcodex_md_content="",
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
            # First writer wins. A duplicate or racing response must not be able
            # to overwrite a reply between event.set() and the waiter's read --
            # in the permission lane that turns a deny into an allow whose
            # chosen_updates get persisted.
            if pending.event.is_set():
                return
            pending.reply = inner if isinstance(inner, dict) else {"behavior": "deny"}
            pending.event.set()

    # ─── shared control round-trip (worker thread; BLOCKS) ─────────────────

    def _round_trip(self, request: dict, timeout: float) -> tuple[str, dict | None]:
        """Emit a ``control_request`` and block for the client's reply.

        The single implementation behind both synchronous lanes (permission and
        ask-user). They had drifted: only one guarded against registering after
        shutdown, and only one popped its slot in a ``finally`` -- so a raising
        ``_emit`` leaked a pending slot in the other, which the shutdown sweep
        would then try to release forever.

        Returns ``(status, reply)`` where status is ``"closed"`` (shutting down,
        nothing emitted), ``"timeout"``, or ``"replied"``. Callers own the
        interpretation of ``reply`` -- the two lanes read different shapes and
        must not be collapsed.

        (``_make_elicitation_handler`` deliberately stays separate: it is
        ``async`` and awaits via ``run_in_executor`` on the MCP runtime loop, so
        it cannot share this synchronous body.)
        """
        request_id = str(_uuid.uuid4())
        pending = _Pending(event=threading.Event())

        with self._lock:
            # Registering after shutdown() took its release snapshot would park
            # this thread for the full timeout with nobody left to answer --
            # _emit silently drops on a closed loop, so the client would never
            # even see the request. ``_stop`` is set at the top of shutdown(),
            # so checking it under the same lock closes that window.
            if self._stop.is_set():
                return "closed", None
            self._pending[request_id] = pending

        try:
            self._emit({
                "type": "control_request",
                "request_id": request_id,
                "request": request,
            })
            got = pending.event.wait(timeout=timeout)
            with self._lock:
                reply = pending.reply
        finally:
            # Pop on EVERY exit path, including a raising _emit.
            with self._lock:
                self._pending.pop(request_id, None)

        return ("replied", reply) if got else ("timeout", None)

    # ─── permission-mode push (any thread; _emit is thread-safe) ───────────

    def _notify_permission_mode(self, mode: str) -> None:
        """Push a live permission-mode change to the client + app state.

        The print.ts:1054-1073 analog (`{type:'system', subtype:'status',
        permissionMode}`): fires on mid-turn flips the client can't learn
        from an RPC reply — a plan-approval `chosen_updates` setMode, an
        EnterPlanMode/ExitPlanMode transition. The client maps it to its
        `permission.mode` event → footer badge. `_dispatch_app_state` is
        best-effort (store-gated on some transports)."""
        try:
            self._emit({
                "type": "system",
                "subtype": "status",
                "session_id": self.session_id,
                "permission_mode": mode,
            })
        except Exception:  # noqa: BLE001 — a push must never break the caller
            logger.debug("[agent-server] permission-mode push failed", exc_info=True)
        _dispatch_app_state(self, permission_mode=mode)

    # ─── permission handler (worker thread; BLOCKS) ────────────────────────

    def permission_handler(self, request: Any) -> Any:
        from src.permissions.types import PermissionAskReply

        # ch13 round-4 — forward the permission SUGGESTIONS (the "always
        # allow Bash(ls:*)" rule options) so the TUI can offer a persistable
        # choice. Previously only tool_name/input crossed the wire, so the
        # client hardcoded a generic "always allow" with no rule attached
        # and the choice was dropped — the user re-approved every turn AND
        # session while the UI falsely reported "approved (always)".
        wire_request: dict[str, Any] = {
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
        }
        # Plan-mode port: ExitPlanMode's ask renders the plan-approval dialog
        # — the client needs the plan body (read from the session plan FILE,
        # the V2 contract), its path, and whether the elevated option should
        # read "bypass permissions" instead of "auto-accept edits"
        # (ExitPlanModePermissionRequest.tsx buildPlanApprovalOptions).
        if wire_request["tool_name"] == "ExitPlanMode":
            try:
                from src.utils.plans import get_plan, get_plan_file_path

                pc = getattr(self.tool_context, "permission_context", None)
                wire_request["plan"] = get_plan()
                wire_request["plan_file_path"] = str(get_plan_file_path())
                # Same disjunction as the set_permission_mode gate — NOT the
                # engine availability flag alone. This drives which elevated
                # option the approval box offers (prompts.tsx: "Yes, and bypass
                # permissions" vs "Yes, auto-accept edits"). With availability
                # alone, a session that started in Full Access and ran /plan
                # would be offered only "auto-accept edits", whose chosen_updates
                # setMode pre-empts ExitPlanMode's pre_plan_mode restore — so
                # approving a plan silently DOWNGRADED the session out of full
                # access with no notice and no way back but /permissions.
                wire_request["bypass_available"] = bool(
                    getattr(pc, "is_bypass_permissions_mode_available", False)
                ) or bool(self.config.bypass_selectable)
            except Exception:  # noqa: BLE001 — degrade to the generic box
                logger.debug("[agent-server] plan payload failed", exc_info=True)
        status, reply = self._round_trip(wire_request, self.config.permission_timeout_s)

        if status == "closed":
            return PermissionAskReply(behavior="deny", message="session closed")
        if status == "timeout":
            return PermissionAskReply(
                behavior="deny", message="permission request timed out"
            )
        return self._permission_reply(reply or {"behavior": "deny"})

    def _permission_reply(self, reply: dict) -> Any:
        """Turn a client's permission-ask reply into a :class:`PermissionAskReply`.

        Extracted from :meth:`permission_handler` so the ``chosen_updates`` gate
        below is directly testable — it is a privilege boundary, and the only
        alternative was driving it through a blocking wire round-trip.
        """
        from src.permissions.types import PermissionAskReply

        if reply.get("behavior") != "allow":
            return PermissionAskReply(
                behavior="deny",
                message=str(reply.get("message", "")) or "denied by user",
            )
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
            chosen = tuple(
                u for u in deserialized
                if u is not None and self._permission_update_allowed(u)
            )
        return PermissionAskReply(
            behavior="allow",
            updated_input=updated if isinstance(updated, dict) else None,
            chosen_updates=chosen,
        )

    # ─── AskUserQuestion handler (worker thread; BLOCKS) ───────────────────

    def ask_user(self, questions: list[dict]) -> dict[str, str] | None:
        """Collect answers to AskUserQuestion's questions from the client.

        Deliberately NOT routed through the permission lane, even though
        ExitPlanMode -- the other blocking interactive tool -- is.
        ``PermissionAskReply`` carries only behavior/updated_input/
        chosen_updates, with nowhere to put structured answers; ExitPlanMode
        gets away with smuggling its edited plan through ``updatedInput``.
        This borrows the MCP-elicitation shape instead (``_make_elicitation_handler``):
        the same ``_pending`` round-trip, but a free-form reply. That also
        keeps AskUserQuestion in ``NO_PERMISSION_TOOLS`` -- the questions ARE
        the gate, and stacking an "Answer questions?" prompt on top of them
        would be absurd.

        Blocks the WORKER thread; the main asyncio loop keeps pumping stdin and
        delivers the reply (see the module header). Returns ``None`` when the
        user declines -- distinct from an empty dict, which is a submit with
        nothing filled in.
        """
        status, reply = self._round_trip(
            {"subtype": "ask_user_question", "questions": questions},
            self.config.ask_user_timeout_s,
        )

        # Shutting down: nobody is left to answer, and a decline is the honest
        # reading -- the question was never put to anyone.
        if status == "closed":
            return None

        if status == "timeout":
            # Nobody answered. Deny would be actively misleading here (the user
            # did not refuse, they walked away), and an empty answer makes the
            # model flail -- so hand back the same "proceed autonomously"
            # instruction the headless surface uses.
            # Local import: this module is the agent-server entry and the tool
            # package pulls in the whole registry, so importing at module scope
            # would put that on every cold start for a branch most turns never
            # reach.
            from src.tool_system.tools.ask_user_question import TIMED_OUT_ANSWER

            return {
                q["question"]: TIMED_OUT_ANSWER
                for q in questions
                if isinstance(q, dict) and isinstance(q.get("question"), str)
            }

        reply = reply or {}
        # Anything that is not an explicit submit counts as a decline. That
        # deliberately includes the generic {"behavior": "deny"} that the ESC
        # sweep (_handle_control_request, subtype "interrupt") and shutdown()
        # write into every pending slot -- an interrupted question was not
        # answered, and this is what makes ESC release the block immediately
        # instead of at ask_user_timeout_s.
        if reply.get("action") != "submit":
            return None
        answers = reply.get("answers")
        if not isinstance(answers, dict):
            return {}
        # Only keys we actually ASKED about survive. The reply is client-shaped
        # and its values end up in prose the model reads as authoritative user
        # speech, so an unfiltered map lets a compromised or buggy client
        # attribute statements to the user for questions that were never put to
        # them.
        asked = {
            q["question"]
            for q in questions
            if isinstance(q, dict) and isinstance(q.get("question"), str)
        }
        return {
            str(q): str(a)
            for q, a in answers.items()
            if isinstance(q, str) and q in asked and a is not None
        }

    def _may_persist_mode(self, mode: str) -> bool:
        """Whether a wire request may write ``permissions.defaultMode`` to disk.

        The SINGLE policy for both doors into persistence: this control's
        ``persist`` flag and a ``chosen_updates`` setMode with a non-session
        destination. Persisting is a HOST-WIDE, durable change — the value is
        read at every future launch, in every project, including headless — so
        it is restricted to sessions with ``bypass_selectable``.

        The exact predicate, since it is broader than "an interactive launcher
        owns this process": ``bypass_selectable`` is set by ``src/cli.py`` /
        ``tui_launcher`` AND is False under a ``disableBypassPermissionsMode``
        lockdown or when running as root outside a sandbox. So in a locked-down
        or elevated interactive session a level choice applies but does not
        persist. That is an accidental coupling rather than a designed one, but
        it fails safe — those sessions already floor at ``default``, so the only
        thing lost is persisting a LOOSENING — and splitting a separate
        settings-writable carrier is not worth the wire surface today.

        Without this, a ``--print-connect`` / ``--http`` client could answer with
        ``{mode: "acceptEdits", persist: true}`` and silently overwrite a user
        who had deliberately chosen "Ask for approval", or install a durable
        ``dontAsk`` the user has to find in a settings file to undo.

        Only modes the READER accepts are written: ``EXTERNAL_PERMISSION_MODES``
        excludes ``auto``, which ``set_permission_mode`` otherwise allows — so
        persisting it would clobber a real prior choice with a value
        ``read_settings_default_mode`` silently ignores.
        """
        from src.permissions.types import EXTERNAL_PERMISSION_MODES

        if mode not in EXTERNAL_PERMISSION_MODES:
            logger.debug("[agent-server] refusing to persist non-external mode %r", mode)
            return False
        if not self.config.bypass_selectable:
            logger.warning(
                "[agent-server] refusing to persist permissions.defaultMode=%r — "
                "this session was not launched by an interactive client", mode,
            )
            return False
        return True

    def _permission_update_allowed(self, update: Any) -> bool:
        """Gate a permission update that arrived inside a permission-ask REPLY.

        ``chosen_updates`` is the "always allow Bash(ls:*)" channel, but it also
        carries ``setMode`` (the plan-approval dialog's "Yes, and bypass
        permissions" arm). That made it a SECOND door to ``bypassPermissions``,
        bypassing the ``set_permission_mode`` gate entirely — a client could
        answer any prompt with a setMode and take Full Access in a session where
        ``bypass_selectable`` is False. The honest TUI never does this, but the
        agent-server also serves ``--print-connect`` / ``--http`` clients, which
        is exactly the population the selectability split exists to bound.

        Two rules for a wire-supplied ``setMode``:

        * ``bypassPermissions`` needs the same capability ``set_permission_mode``
          requires;
        * a persisted destination needs :meth:`_may_persist_mode` — the same
          policy the ``set_permission_mode`` ``persist`` flag goes through.
          Writing ``permissions.defaultMode`` into the HOST's settings file makes
          one message from a remote client a permanent host-wide setting, since
          that value is read at every launch.

        Non-``setMode`` updates (rule grants) are unaffected.
        """
        from src.permissions.types import PermissionUpdateSetMode

        if not isinstance(update, PermissionUpdateSetMode):
            return True
        if getattr(update, "destination", "session") != "session" and not (
            self._may_persist_mode(update.mode)
        ):
            logger.warning(
                "[agent-server] refusing chosen_updates setMode with "
                "destination=%r", getattr(update, "destination", None),
            )
            return False
        if update.mode != "bypassPermissions":
            return True
        pc = getattr(self.tool_context, "permission_context", None)
        allowed = bool(
            getattr(pc, "is_bypass_permissions_mode_available", False)
            or self.config.bypass_selectable
        )
        if not allowed:
            logger.warning(
                "[agent-server] refusing chosen_updates setMode:bypassPermissions "
                "— Full Access is not available in this session",
            )
        return allowed

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
        """Total tokens across the cost snapshot's model_usage.

        Includes the cache fields. ``input_tokens`` counts only the UNCACHED
        prompt — providers report the cached prefix separately, and the
        snapshot already carries both — so summing input+output alone
        under-reported every cached turn, by 98% of the prompt on a warm
        prefix cache.
        """
        total = 0
        try:
            for usage in (snapshot.get("model_usage") or {}).values():
                total += int(usage.get("input_tokens", 0) or 0)
                total += int(usage.get("output_tokens", 0) or 0)
                total += int(usage.get("cache_read_input_tokens", 0) or 0)
                total += int(usage.get("cache_creation_input_tokens", 0) or 0)
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

    def _worktree_env_session(self):
        """The --worktree session advertised by the launcher, if any.

        Read from ``CLAWCODEX_WORKTREE_*`` (set by src/cli.py / tui_launcher
        for the worktree THIS process tree was launched into; cli.py strips
        any inherited block at entry, so nested sessions never see one).
        Gated on ``single_session``: the env block is process-wide, so on the
        multi-session --http transport it must not be visible — any WS client
        could otherwise remove a worktree it doesn't own.
        """
        if not self.config.single_session or self._worktree_done:
            return None
        from src.utils.worktree_session import WorktreeSession

        return WorktreeSession.from_env()

    async def _do_worktree_status(self, request_id: object) -> None:
        """Exit-time snapshot for the client's keep/remove decision.

        ``git_ok: False`` means the counts are placeholders, not measurements
        — the client must fail closed (treat as has-changes, render no
        numbers, never silent-remove).
        """
        ws = self._worktree_env_session()
        if ws is None:
            self._reply(request_id, {"ok": True, "active": False})
            return

        from src.utils.worktree_session import worktree_changes

        changes = await asyncio.to_thread(worktree_changes, ws)
        self._reply(request_id, {
            "ok": True,
            "active": True,
            "name": ws.worktree_name,
            "path": ws.worktree_path,
            "branch": ws.worktree_branch,
            "original_cwd": ws.original_cwd,
            "git_ok": changes.git_ok,
            "dirty_files": changes.dirty_files,
            "commits": changes.commits,
        })

    async def _do_worktree_exit(self, request_id: object, action: object) -> None:
        """Perform the exit-time keep/remove the client chose.

        The client is about to terminate this process either way; ``remove``
        chdirs to the original cwd first so the dying process doesn't hold
        its cwd inside the directory being deleted. Runs in a thread — a
        ``git worktree remove --force`` of a large tree can take minutes and
        must not block the control loop (the client uses a long RPC timeout
        for exactly this call).
        """
        ws = self._worktree_env_session()
        if ws is None:
            self._reply(request_id, {"ok": False, "error": "no active worktree session"})
            return
        if action == "keep":
            from src.utils.worktree_session import keep_message

            self._worktree_done = True
            self._reply(request_id, {"ok": True, "message": keep_message(ws)})
            return
        if action != "remove":
            self._reply(request_id, {
                "ok": False,
                "error": f"invalid worktree_exit action: {action!r} (keep | remove)",
            })
            return

        # Idle-only, like every destructive control (clear/resume/rewind/…):
        # the turn runs on the worker thread while this handler runs on the
        # main loop, so without the guard a mid-turn exit could `git worktree
        # remove --force` the directory out from under live tool calls
        # (critic: worktree_exit was the only file-deleting control missing
        # it). The client degrades the refusal to keep-and-exit.
        with self._lock:
            active = self._current_abort is not None
        if active:
            self._reply(request_id, {
                "ok": False,
                "error": "cannot remove the worktree during an active turn",
            })
            return

        from src.utils.worktree_session import (
            cleanup_worktree,
            removal_message,
            worktree_changes,
        )

        def _remove() -> dict:
            import os

            # Measure BEFORE removal — the message reports what was discarded.
            changes = worktree_changes(ws)
            try:
                os.chdir(ws.original_cwd)
            except OSError:
                pass  # removal runs with explicit cwd=repo_root regardless
            ok, error = cleanup_worktree(ws)
            if not ok:
                return {"ok": False, "error": error}
            return {"ok": True, "message": removal_message(ws, changes)}

        result = await asyncio.to_thread(_remove)
        if result.get("ok"):
            self._worktree_done = True
        self._reply(request_id, result)

    def _do_eco_command(self, request_id: object, arg: object) -> None:
        """Control handler for /eco — toggle Bash-output token compression.

        Bridges to the command-system implementation (eco_command_call),
        which owns the grammar (toggle / on / off / status). The state is
        process-global session state (src/eco/state.py, the ultracode
        shape) — NOT persisted user settings — so no ``single_session``
        gate: flipping it affects this worker process only, exactly like
        ``/effort ultracode``.
        """
        try:
            from src.command_system.eco_command import eco_command_call
            from src.command_system.types import CommandContext

            ctx = CommandContext(
                workspace_root=Path(self.cwd),
                cwd=Path(self.cwd),
                conversation=getattr(self.session, "conversation", None),
                cost_tracker=None,
                history=None,
            )
            result = eco_command_call(str(arg or ""), ctx)
            from src.eco import is_eco_session

            self._reply(request_id, {
                "ok": True,
                "enabled": is_eco_session(),
                "text": str(getattr(result, "value", "") or ""),
            })
        except Exception as exc:  # noqa: BLE001 — must not kill the control channel
            logger.exception("[agent-server] eco command failed")
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

    def _do_vision_command(self, request_id: object, arg: object) -> None:
        """Control handler for /vision — set the vision_analyze model.

        Bridges to ``vision_command.vision_command_call``, which owns the
        grammar (``<provider>:<model>`` | on | off).

        ``single_session``-gated for the same reason as /fusion and /advisor:
        this persists USER-level config (``~/.clawcodex/config.json`` ->
        ``vision``), so on the multi-session WS transport one client's
        ``/vision off`` would disable the tool for every other session on the
        host.

        Reply shape mirrors /fusion: transport-level ``ok`` is True whenever
        the command ran; command-level rejections ride ``text``.
        """
        if not self.config.single_session:
            self._reply(request_id, {
                "ok": False,
                "error": "/vision is only available on single-session "
                         "(stdio) transports — it persists user-level "
                         "settings.",
            })
            return
        try:
            from src.command_system.types import CommandContext
            from src.command_system.vision_command import vision_command_call

            ctx = CommandContext(
                workspace_root=Path(self.cwd),
                cwd=Path(self.cwd),
                conversation=getattr(self.session, "conversation", None),
                cost_tracker=None,
                history=None,
                app_state_store=None,
                provider=self.provider,
            )
            result = vision_command_call(str(arg or ""), ctx)
            self._reply(request_id, {
                "ok": True,
                "text": str(getattr(result, "value", "") or ""),
            })
        except Exception as exc:  # noqa: BLE001 — must not kill the control channel
            logger.exception("[agent-server] /vision command failed")
            self._reply(request_id, {"ok": False, "error": str(exc)})

    def _do_fusion_command(self, request_id: object, arg: object) -> None:
        """Control handler for /fusion — manage fusion models.

        Bridges to the command-system implementation
        (``fusion_command.fusion_command_call``), which owns the grammar
        (list / create / delete / enable / disable / help).

        ``single_session``-gated for the same reason as /advisor: fusion
        models are persisted USER-level config (``~/.clawcodex/config.json``
        → ``fusionModels``), so on the multi-session WS transport one
        client's ``/fusion delete`` would remove a model out from under
        every other session on the host.

        Reply shape mirrors /advisor: transport-level ``ok`` is True
        whenever the command ran, and command-level rejections (bad
        selector, unknown name, duplicate) ride ``text``.
        """
        if not self.config.single_session:
            self._reply(request_id, {
                "ok": False,
                "error": "/fusion is only available on single-session "
                         "(stdio) transports — it persists user-level "
                         "settings.",
            })
            return
        try:
            from src.command_system.fusion_command import fusion_command_call
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
            result = fusion_command_call(str(arg or ""), ctx)
            self._reply(request_id, {
                "ok": True,
                "text": str(getattr(result, "value", "") or ""),
            })
        except Exception as exc:  # noqa: BLE001 — must not kill the control channel
            logger.exception("[agent-server] fusion command failed")
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
            if not outcome:
                return
            # A turn the AGENT LOOP cut short (the tool-failure-loop guard,
            # max_turns, an empty response) carries no real output, so there is
            # nothing for the judge to weigh — feeding it the "[Stopped: …]"
            # sentinel is how a cut-short turn gets mistaken for progress.
            #
            # But it must not END the goal either. Before the subtype was
            # derived, such a turn arrived as "success", was judged not-done,
            # and the loop simply RETRIED — so bailing out here would silently
            # kill /goal loops that used to recover on their own. Instead:
            # skip the judge and apply a synthetic ``continue``.
            #
            # Deliberately still routed through ``apply_verdict`` rather than
            # enqueuing a continuation directly: that is what ticks
            # ``turns_used`` and lets the goal's own cap decide. Short-
            # circuiting it would let a turn that keeps stopping early retry
            # forever.
            # WHICH non-success outcomes continue the goal, and which end it.
            #
            # Only the ones the AGENT LOOP itself produced — the values in
            # EARLY_STOP_SUBTYPES. Those mean "the model did not finish", so
            # retrying is right and matches what happened before the subtype
            # was derived at all (the turn arrived as "success", was judged
            # not-done, and the loop retried).
            #
            # ``cancelled`` and ``error`` are NOT those. ``_run_turn`` returns
            # ``cancelled`` on AbortError and ``error`` on an exception, so a
            # blanket ``!= "success"`` made ESC during a /goal loop re-enqueue
            # the work the user just killed, and made a provider 5xx retry up
            # to the goal's whole turn budget. Same category as the hook stops
            # deliberately excluded from the map: the USER or the PROVIDER
            # ended the turn, not the harness cutting it short.
            from src.query.transitions import EARLY_STOP_SUBTYPES

            _loop_early_stops = frozenset(EARLY_STOP_SUBTYPES.values())
            early_stop_subtype = str(outcome.get("subtype") or "")
            if (
                early_stop_subtype != "success"
                and early_stop_subtype not in _loop_early_stops
            ):
                return
            early_stop = early_stop_subtype in _loop_early_stops
            response_text = str(outcome.get("response_text") or "")
            if not early_stop and not response_text.strip():
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

            if early_stop:
                # No judge call: there is no output to judge, and asking a
                # model whether "[Stopped: …]" satisfies the goal only invites
                # a wrong answer. "continue" is also the fail-open verdict
                # ``judge_goal`` itself returns on error, so this takes a path
                # the loop already handles.
                verdict, reason, parse_failed = (
                    "continue",
                    f"the last turn stopped early ({early_stop_subtype}) "
                    "and produced no result to evaluate",
                    False,
                )
            else:
                from src.goals import collect_turn_evidence, judge_goal

                evidence = ""
                try:
                    evidence = collect_turn_evidence(
                        list(self.session.conversation.messages)
                    )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "[agent-server] goal evidence failed", exc_info=True
                    )
                if not evidence:
                    evidence = response_text

                # ── judge OUTSIDE the lock (bounded network call) ─────
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

    def _maybe_spawn_memory_review(
        self, outcome: dict | None, pre_turn_len: int
    ) -> None:
        """Post-turn self-improvement hook (worker thread) — the port of
        hermes' nudge-counter + background-review spawn
        (``turn_context.py:289-297`` + ``turn_finalizer.py:444-454``,
        via ``src/memory``).

        Counts completed REAL user turns only (never btw/internal/goal
        continuations — matching the donor's per-user-turn cadence).
        Every ``memory_review_interval``-th turn spawns a daemon-thread
        fork that replays the conversation snapshot with the parent's
        pinned system prompt + registry (warm prefix cache) and may save
        durable facts via the Memory tool; the fork's summary is emitted
        as ONE ``review_summary`` frame. A foreground Memory call resets
        the counter (organic writes postpone the nudge); at most one
        fork runs at a time. Never raises; learning must never compete
        with the user's task.
        """
        try:
            if not outcome or outcome.get("subtype") != "success":
                return
            if not str(outcome.get("response_text") or "").strip():
                return

            from src.memory.review import (
                REVIEW_TOOL_NAME,
                hydrate_turns_since_memory,
                turn_used_memory_tool,
            )
            from src.settings.settings import get_settings

            settings = get_settings()
            if not bool(getattr(settings, "memory_store_enabled", True)):
                return
            interval = int(getattr(settings, "memory_review_interval", 10) or 0)
            if interval <= 0:
                return
            # Coordinator mode narrows the main loop's tool surface and
            # excludes Memory — the filtered view is what the parent's
            # requests advertise, so it is also what the fork must use
            # (tools[] byte-parity, design-critic M4). With Memory absent
            # the guard below skips the review entirely.
            from src.coordinator.mode import coordinator_main_loop_registry

            registry = (
                coordinator_main_loop_registry(self.tool_registry)
                if self.tool_registry is not None else None
            )
            if registry is None or registry.get(REVIEW_TOOL_NAME) is None:
                return

            messages = list(self.session.conversation.messages)

            # Lazy resume hydration: continue the cadence from the restored
            # history instead of restarting at zero (donor issue #22357).
            # ``_stats_turns`` is the session's canonical completed-turn
            # odometer (resume-seeded, /clear-zeroed, /rewind-recomputed) and
            # at this point already includes THIS turn — the increment below
            # counts it, so hydrate from the prior turns only.
            if not self._memory_counter_hydrated:
                self._turns_since_memory = hydrate_turns_since_memory(
                    max(0, self._stats_turns - 1), interval
                )
                self._memory_counter_hydrated = True

            # An organic foreground Memory call this turn postpones the
            # nudge (donor tool_executor.py:318-322).
            if turn_used_memory_tool(messages[pre_turn_len:]):
                self._turns_since_memory = 0
                return

            self._turns_since_memory += 1
            if self._turns_since_memory < interval:
                return
            self._turns_since_memory = 0

            prev = self._memory_review_thread
            if prev is not None and prev.is_alive():
                return  # one fork at a time — never stack reviews
            # (No manual clear needed: a finished/crashed thread reports
            # is_alive() False, so the guard self-heals.)

            tool_context = self.tool_context
            system_prompt = self.system_prompt
            if self.provider is None or tool_context is None:
                return
            provider_name = self.provider_name
            model = getattr(self.provider, "model", None) or self.config.model
            mode_raw = str(
                getattr(settings, "memory_notifications", "on") or "on"
            ).lower()
            notification_mode = (
                mode_raw if mode_raw in {"off", "on", "verbose"} else "on"
            )

            def _review_target() -> None:
                # The fork constructs its OWN provider instance (same
                # provider/model/credentials as the session) instead of
                # sharing ``self.provider`` — the next foreground turn runs
                # concurrently with this thread, and provider objects carry
                # per-request mutable state (lazy client, client kwargs,
                # subscription token). Mirrors the donor's fork, which
                # re-resolves its runtime rather than sharing the parent's
                # client (background_review.py:608-655; design-critic B1).
                # Resolution failure skips the review — never share.
                from src.memory.review_fork import run_memory_review

                try:
                    from src.config import get_provider_config
                    from src.providers import get_provider_class, resolve_api_key

                    provider_cfg = get_provider_config(provider_name)
                    fork_provider = get_provider_class(provider_name)(
                        api_key=resolve_api_key(provider_name, provider_cfg),
                        base_url=provider_cfg.get("base_url"),
                        model=model or provider_cfg.get("default_model"),
                    )
                except Exception:  # noqa: BLE001 — no fork without its own client
                    logger.debug(
                        "[agent-server] review provider resolution failed",
                        exc_info=True,
                    )
                    return

                summary = run_memory_review(
                    provider=fork_provider,
                    tool_registry=registry,
                    parent_tool_context=tool_context,
                    system_prompt=system_prompt,
                    conversation_snapshot=messages,
                    notification_mode=notification_mode,
                )
                if summary:
                    self._emit({
                        "type": "system",
                        "subtype": "review_summary",
                        "session_id": self.session_id,
                        "message": summary,
                    })

            thread = threading.Thread(
                target=_review_target, name="bg-review", daemon=True
            )
            self._memory_review_thread = thread
            thread.start()
        except Exception:  # noqa: BLE001 — the review must never kill the worker
            logger.debug("[agent-server] memory review hook failed",
                         exc_info=True)

    def _maybe_spawn_recap(self, outcome: dict | None) -> None:
        """Post-turn recap hook (worker thread) — CC's end-of-turn "✻ recap:"
        line plus the tab-acceptable composer suggestion.

        Fires only when the session actually returns to idle awaiting the
        user: a completed REAL user turn (the caller runs this for neither
        btw nor internal turns), subtype ``success`` with a non-empty
        response, no active /goal loop (the agent keeps working), and an
        empty inbox (a queued prompt makes the recap stale on arrival).
        Spawns a daemon fork that builds its OWN provider (same reasoning as
        the memory-review fork: provider objects carry per-request mutable
        state and the next foreground turn may run concurrently), generates
        via the small-fast-model side query, and emits ONE ``system/recap``
        frame — dropped if the turn odometer moved or a turn is running by
        the time it finishes. Never raises.
        """
        try:
            if not outcome or outcome.get("subtype") != "success":
                return
            if not str(outcome.get("response_text") or "").strip():
                return

            from src.settings.settings import get_settings

            if not bool(getattr(get_settings(), "recap_enabled", True)):
                return
            with self._lock:
                if self._goal_mgr is not None and self._goal_mgr.is_active():
                    return
            if not self._inbox.empty():
                return
            prev = self._recap_thread
            if prev is not None and prev.is_alive():
                return  # one recap fork at a time
            if self.provider is None or self.session is None:
                return

            messages = list(self.session.conversation.messages)
            if not messages:
                return
            provider_name = self.provider_name
            model = getattr(self.provider, "model", None) or self.config.model
            serial = self._stats_turns
            recap_serial = self._recap_serial

            def _recap_target() -> None:
                try:
                    from src.config import get_provider_config
                    from src.providers import (
                        get_provider_class,
                        resolve_api_key,
                    )

                    provider_cfg = get_provider_config(provider_name)
                    fork_provider = get_provider_class(provider_name)(
                        api_key=resolve_api_key(provider_name, provider_cfg),
                        base_url=provider_cfg.get("base_url"),
                        model=model or provider_cfg.get("default_model"),
                    )
                except Exception:  # noqa: BLE001 — no fork without its own client
                    logger.debug(
                        "[agent-server] recap provider resolution failed",
                        exc_info=True,
                    )
                    return

                from src.services.turn_recap import generate_turn_recap

                result = asyncio.run(
                    generate_turn_recap(messages, fork_provider)
                )
                if not result or not result.get("recap"):
                    return
                # Staleness gate: only the turn this recap describes may
                # surface it. Two keys must BOTH be unmoved: the user-turn
                # odometer (fails closed on a bare /clear with no follow-up
                # turn) and the monotonic ``_recap_serial`` (closes the
                # odometer's ABA hole — /clear zeroes it, /rewind recounts
                # it down, /resume re-seeds it, so a slow fork could watch
                # the count leave and come back over a replaced
                # conversation). Plus: no in-flight turn, no queued prompt.
                if self._stop.is_set() or not self._inbox.empty():
                    return
                with self._lock:
                    if self._current_abort is not None:
                        return
                if self._stats_turns != serial or self._recap_serial != recap_serial:
                    return
                self._emit({
                    "type": "system",
                    "subtype": "recap",
                    "session_id": self.session_id,
                    "recap": str(result.get("recap") or ""),
                    "suggestion": str(result.get("suggestion") or ""),
                })

            thread = threading.Thread(
                target=_recap_target, name="bg-recap", daemon=True
            )
            self._recap_thread = thread
            thread.start()
        except Exception:  # noqa: BLE001 — recaps must never kill the worker
            logger.debug("[agent-server] recap hook failed", exc_info=True)

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
                # …then fire any due scheduled task (/loop, Cron*): the
                # 0.5s inbox poll doubles as the scheduler tick, well under
                # CC's "checks every second" contract.
                self._fire_due_scheduled()
                continue
            if item is _SHUTDOWN or self._stop.is_set():
                break
            if isinstance(item, dict) and item.get("__btw__"):  # side question (/btw)
                self._run_turn(item.get("content"), btw=True)
                self._push_cron_state()
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
                self._push_cron_state()
                continue
            if not isinstance(item, (str, list)):  # str prompt, or multimodal blocks
                continue
            pre_turn_len = len(self.session.conversation.messages) if self.session else 0
            outcome = self._run_turn(item)
            self._maybe_continue_goal(outcome)
            # Self-improvement review — runs AFTER the response is delivered
            # so it never competes with the user's task for model attention.
            self._maybe_spawn_memory_review(outcome, pre_turn_len)
            # End-of-turn recap + composer suggestion — same post-delivery
            # slot, real user turns only (never btw/internal/goal turns).
            self._maybe_spawn_recap(outcome)
            # A task that finished while the turn ran is delivered right after.
            self._deliver_task_notifications()
            # Reflect any Cron*/ScheduleWakeup change the turn made (deduped
            # push — costs a frame only when the state differs).
            self._push_cron_state()
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

    # ─── scheduled tasks (/loop + Cron* + ScheduleWakeup firing) ───────────

    def _cron_state_payload(self) -> dict:
        """Client-facing scheduled-state snapshot. Prompts ride truncated —
        the TUI indicator only needs a preview; full prompts persist in the
        session file via ``_save_session``."""
        snap = self.cron_scheduler.snapshot()
        jobs = []
        for job in snap.get("jobs") or []:
            prompt = str(job.get("prompt") or "")
            jobs.append({
                "id": job.get("id"),
                "cron": job.get("cron"),
                "human_schedule": SessionCronScheduler.human_schedule(
                    str(job.get("cron") or "")
                ),
                "prompt_preview": prompt[:120] + ("…" if len(prompt) > 120 else ""),
                "recurring": bool(job.get("recurring")),
                "next_fire_at": job.get("next_fire_at"),
                "expires_at": job.get("expires_at"),
            })
        wakeup = snap.get("wakeup")
        return {
            "jobs": jobs,
            "wakeup": (
                {
                    "fire_at": wakeup.get("fire_at"),
                    "reason": str(wakeup.get("reason") or ""),
                    "is_fallback": bool(wakeup.get("is_fallback", False)),
                }
                if isinstance(wakeup, dict)
                else None
            ),
        }

    def _push_cron_state(self, message: str = "") -> None:
        """Emit a ``cron_status`` event carrying the scheduled-state
        snapshot. Message-less pushes dedupe against the last emitted
        snapshot, so the routine post-turn call only costs a frame when a
        tool actually changed something."""
        try:
            payload = self._cron_state_payload()
            encoded = json.dumps(payload, sort_keys=True, default=str)
            if not message and encoded == self._cron_push_json:
                return
            self._cron_push_json = encoded
            self._emit({
                "type": "system",
                "subtype": "cron_status",
                "session_id": self.session_id,
                "message": message,
                "scheduled": payload,
            })
        except Exception:  # noqa: BLE001 — status pushes must never break the worker
            logger.debug("[agent-server] cron state push failed", exc_info=True)

    @staticmethod
    def _scheduled_turn_content(task: Any) -> str:
        """Wrap a fired prompt in the envelope the model acts on. A prompt
        that starts with a slash command is re-dispatched through the Skill
        tool BY THE MODEL (the same rule the /loop skill body states), so
        the server needs no skill-expansion path of its own here."""
        prompt = task.prompt or ""
        if task.kind == "wakeup":
            reason = (task.reason or "").replace('"', "'")
            fallback_attr = ' fallback="true"' if task.is_fallback else ""
            lines = [
                f'<scheduled-wakeup reason="{reason}"{fallback_attr}>',
                prompt,
                "</scheduled-wakeup>",
                "",
                "The wakeup you scheduled just fired. Run one iteration of the task above.",
                "- If the prompt starts with a slash command (e.g. /loop), invoke it via the Skill tool (skill = the word after '/', args = the rest).",
                "- Otherwise act on it directly.",
                "When the iteration finishes, either schedule the next wakeup with ScheduleWakeup or end the loop with ScheduleWakeup stop: true.",
            ]
            if task.is_fallback:
                lines.append(
                    "This is the FALLBACK wakeup: the previous iteration neither "
                    "rescheduled nor stopped. If this iteration doesn't "
                    "reschedule, the loop ends."
                )
            return "\n".join(lines)
        human = SessionCronScheduler.human_schedule(task.cron) if task.cron else ""
        lines = [
            f'<scheduled-task id="{task.id}" schedule="{task.cron}">',
            prompt,
            "</scheduled-task>",
            "",
            f"This scheduled prompt ({human or task.cron}) just came due. Run it now.",
            "- If it starts with a slash command, invoke it via the Skill tool (skill = the word after '/', args = the rest).",
            "- Otherwise act on it directly.",
        ]
        if task.recurring and not task.deleted:
            lines.append(
                "Do not reschedule it yourself — the recurring job re-fires "
                "on its own schedule."
            )
        elif task.recurring and task.deleted:
            lines.append(
                "This recurring task reached its 7-day expiry: this was its "
                "final run and it has been removed."
            )
        else:
            lines.append(
                "This was a one-shot task; it has been removed after this run."
            )
        return "\n".join(lines)

    def _fire_due_scheduled(self) -> bool:
        """Idle-branch consumer: pop every due scheduled task and run each
        prompt as one internal turn. Runs on the worker thread with an
        empty inbox, so a fire can never interleave with a user turn (CC:
        "a scheduled prompt fires between your turns, not while Claude is
        mid-response"; a task that came due mid-turn fires once now — no
        catch-up). Returns whether anything fired."""
        if self.init_error is not None or self._stop.is_set():
            return False
        try:
            fired = self.cron_scheduler.pop_due()
        except Exception:  # noqa: BLE001 — scheduler failure must not kill the worker
            logger.debug("[agent-server] scheduled pop_due failed", exc_info=True)
            return False
        if not fired:
            return False
        for task in fired:
            if self._stop.is_set():
                break
            if task.kind == "wakeup":
                label = "Fallback loop wakeup" if task.is_fallback else "Loop wakeup"
                self._push_cron_state(
                    f"⟳ {label} fired — {task.reason or 'running the next iteration'}."
                )
                self.cron_scheduler.begin_turn_window()
                outcome = self._run_turn(
                    self._scheduled_turn_content(task), internal=True
                )
                self._after_wakeup_turn(task, outcome)
            else:
                human = SessionCronScheduler.human_schedule(task.cron)
                if task.deleted and task.recurring:
                    note = (f"⏰ Scheduled task {task.id} fired ({human}) — "
                            "7-day expiry reached; final run, task removed.")
                elif task.deleted:
                    note = f"⏰ One-time scheduled task {task.id} fired."
                else:
                    note = f"⏰ Scheduled task {task.id} fired ({human})."
                self._push_cron_state(note)
                self._run_turn(self._scheduled_turn_content(task), internal=True)
            # A task that finished while the scheduled turn ran is delivered
            # right after, matching the real-turn path.
            self._deliver_task_notifications()
        self._push_cron_state()
        self._save_session()  # persist advanced next_fire_at / removals
        return True

    def _after_wakeup_turn(self, task: Any, outcome: dict | None = None) -> None:
        """Fallback semantics (§Stop a loop, CC ≥2.1.202): an iteration
        that neither reschedules nor stops gets ONE fallback wakeup ~20
        minutes out; when the fallback iteration doesn't reschedule either,
        the loop ends. An iteration that did not complete (user interrupt,
        provider error) ends the loop instead of arming the fallback —
        rescheduling work the user just killed, or retrying into a broken
        provider every 20 minutes, are both worse than stopping (mirrors
        the goal loop's no-continuation-on-error guard)."""
        try:
            action = self.cron_scheduler.wakeup_action_since()
            if action == "set":
                return  # rescheduled — the loop continues
            if action == "stopped":
                self._push_cron_state("⟳ Loop ended.")
                return
            if not outcome or outcome.get("subtype") != "success":
                self._push_cron_state(
                    "⟳ Loop ended — the iteration was interrupted or errored "
                    "before it could reschedule."
                )
                return
            if task.is_fallback:
                self._push_cron_state(
                    "⟳ Loop ended — the fallback iteration didn't reschedule."
                )
                return
            self.cron_scheduler.set_wakeup(
                FALLBACK_WAKEUP_DELAY_SECONDS,
                task.prompt,
                "fallback — the last iteration didn't reschedule or stop",
                is_fallback=True,
            )
            self._push_cron_state(
                "⟳ The iteration ended without rescheduling — one fallback "
                "wakeup in ~20 minutes, then the loop ends."
            )
        except Exception:  # noqa: BLE001 — fallback bookkeeping must never raise
            logger.debug("[agent-server] wakeup fallback handling failed",
                         exc_info=True)

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

        # /effort: route the level to whichever parameter this provider
        # family actually accepts (default off ⇒ real provider, no effort).
        turn_provider, turn_thinking_effort = self._turn_effort_routing()
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

            # Cost is read as a DELTA of the tracker's running total rather
            # than recomputed from ``result.usage`` below — see the note at
            # the ``_cost`` assignment for why the aggregate cannot be priced.
            from src.bootstrap.state import get_total_cost_usd

            _cost_before = get_total_cost_usd()
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
                # /effort for BOTH provider families: resolved at the wire
                # boundary into ``output_config.effort`` (Anthropic, with the
                # per-model gating — xhigh clamps to high where unsupported)
                # or a top-level ``reasoning_effort`` body field
                # (OpenAI-compatible, no clamp).
                thinking_effort=turn_thinking_effort,
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
                # Plan-mode attachments: persist WITHOUT emitting an SDK
                # envelope (on_message would render them as user text in the
                # TUI — _sdk_envelope has no meta filter). Persistence is what
                # keeps the instructions in later turns' context and lets the
                # cadence scan find prior attachments.
                on_attachment=lambda m: self.session.conversation.add_message(
                    m.role, m.content, isMeta=getattr(m, "isMeta", False)
                ),
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
        # The turn's cost is the tracker's delta, NOT ``compute_cost`` over
        # ``result.usage``. That dict is the sum across every loop turn, and
        # ``get_pricing`` selects a tier from a PER-REQUEST threshold
        # (gpt-5.6-luna at 272K, MiniMax-M3 at 512K). Cache reads sum to
        # roughly turns x conversation-size, so a long loop of small requests
        # crosses a boundary no single request came near: a 25-turn loop over
        # a 15K conversation prices at 1.76x the true cost.
        #
        # ``cost_tracker.record_api_usage`` already prices each response as it
        # arrives, with the tier chosen from that one request, so the running
        # total is correct by construction. Reading its delta also drops a
        # duplicate cost implementation rather than patching one.
        _cost = 0.0
        try:
            _cost = max(0.0, get_total_cost_usd() - _cost_before)
        except Exception:  # noqa: BLE001 — cost is best-effort, never break the turn
            _cost = 0.0
        # EVERY completed turn (btw/internal included) invalidates any
        # in-flight recap fork — monotonic, so the odometer's ABA hole
        # (/clear → 0 → back up; /rewind recount) can't resurrect one.
        self._recap_serial += 1
        # One more completed user turn. Internal (notification) and btw
        # (ephemeral, rolled-back) turns don't move the odometer — same rule
        # as the deleted REPL, which only counted real prompt→response rounds.
        if not internal and not btw:
            self._stats_turns += 1
        # A turn the AGENT LOOP ended is not a success. This used to be
        # hardcoded to "success" regardless of why the loop stopped, so a
        # guard-killed or empty turn looked identical to a completed one on
        # this surface — and three consumers gate on exactly this field:
        # ``_maybe_judge_goal`` fed a cut-short turn to the /goal judge as
        # evidence of progress, ``_maybe_review_memories`` learned from it,
        # and the cron loop rearmed on it. Same map headless uses, so the two
        # surfaces cannot drift.
        from src.query.transitions import EARLY_STOP_SUBTYPES

        _stop = (
            result.terminal.reason
            if getattr(result, "terminal", None) is not None
            else None
        )
        _subtype = EARLY_STOP_SUBTYPES.get(_stop or "", "success")
        _is_error = _subtype != "success"
        self._emit(_result_message(
            self.session_id,
            permission_mode=_current_mode(self.tool_context, self.config.permission_mode),
            subtype=_subtype,
            num_turns=result.num_turns,
            result=result.response_text,
            is_error=_is_error,
            usage=_usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            total_cost_usd=_cost,
            session_turns=self._stats_turns,
        ))
        self._save_session()  # persist for /resume
        return {"subtype": _subtype, "response_text": result.response_text or ""}

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
        # Under the lock and honoring the latch, same as the interrupt sweep:
        # a reply that already landed must not be overwritten on the way out.
        with self._lock:
            for pending in pendings:
                if pending.event.is_set():
                    continue
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
        # Wire the permission + ask-user handlers now that tool_context exists.
        if sess.tool_context is not None and sess.init_error is None:
            sess.tool_context.permission_handler = sess.permission_handler
            # Without this AskUserQuestion falls through to its outbox branch
            # and returns its own questions back as a "pending" result -- the
            # tool looked registered but could never actually ask.
            #
            # single_session only: that is the --stdio transport, one session
            # owned by the Ink client we know renders the dialog. On the
            # multi-session --http transport there is no capability
            # negotiation, so a client that ignores the ask_user_question
            # subtype would park that session's sole worker thread for the full
            # ask_user_timeout_s. Substitute the non-interactive answer there
            # instead, which is what the headless surface does.
            if sess.config.single_session:
                sess.tool_context.ask_user = sess.ask_user
            else:
                sess.tool_context.ask_user = _non_interactive_ask_user
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
            provider_has_credentials,
            resolve_api_key,
        )
        from src.agent import Session
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry
        from src.utils.startup_profiler import profile_checkpoint

        profile_checkpoint("agent_server_build_runtime_start")

        # ``--effort`` seeds the session's /effort level, so the launch flag
        # reaches the INTERACTIVE path too (it used to be plumbed only into
        # HeadlessOptions, so `clawcodex --model X --effort xhigh` without
        # -p parsed the flag and silently discarded it). A later /effort
        # overrides this; /effort auto clears it back to settings.effort.
        #
        # Validated and lowercased HERE, not just in argparse: this server
        # is also driven programmatically (--stdio from the vscode
        # extension, --print-connect), so a bad value can arrive without
        # passing a parser. Seeding it verbatim would resurrect the exact
        # trap _do_set_effort rejects — an off-ladder level like "minimal"
        # is not in VALID_THINKING_EFFORT_LEVELS, so resolve_thinking_effort
        # treats it as "nothing requested" and silently substitutes
        # settings.effort, while the init frame's badge displays the value
        # the user asked for. Unnormalized case has the same shape on the
        # OpenAI-compat side, which sends the level verbatim.
        # ``isinstance`` rather than ``or ""``: a non-str effort from a
        # programmatic caller would raise inside this try block, and
        # _build_runtime converts any raise into init_error — killing the
        # whole session over a cosmetic setting. Truthiness is checked
        # before membership because VALID_EFFORT_VALUES includes the ""
        # auto sentinel, which must not seed a level.
        raw_effort = sess.config.effort
        seed = raw_effort.strip().lower() if isinstance(raw_effort, str) else ""
        if not sess._effort:
            from src.settings.constants import VALID_EFFORT_VALUES

            if seed and seed in VALID_EFFORT_VALUES:
                sess._effort = seed
            elif raw_effort:
                logger.warning(
                    "[agent-server] ignoring --effort %r: not one of %s",
                    raw_effort,
                    [v for v in VALID_EFFORT_VALUES if v],
                )

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
        # ch02 round-4 WI-2 — warm the context caches (CLAWCODEX.md walk,
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

        # Model precedence, mirroring TS ``main.tsx:1984``
        # (``userSpecifiedModel ?? getUserSpecifiedModelSetting() ?? null``):
        # an explicit --model wins, then the persisted /model choice, then
        # the provider default (applied below). Without the middle term a
        # /model switch never survived a restart — the write side has always
        # persisted (model, model_provider); nothing ever read it back.
        from src.settings.settings import get_persisted_model

        #
        # single_session ONLY, deliberately. The persisted choice lives in
        # the HOST's user settings, and the old post-construction restore
        # sat inside the ``if cfg.single_session:`` block below — so reading
        # it here unguarded would newly apply the server operator's model to
        # every client session on the multi-session --http transport. Same
        # shape as the bypass-availability refusal further down ("would let
        # the server host's own settings unlock bypass for every client
        # session"); milder consequence, but a scope change should be a
        # decision rather than a side effect of moving the read earlier.
        model_choice = cfg.model or (
            get_persisted_model(
                provider_name, provider_is_explicit=bool(cfg.provider_name)
            )
            if cfg.single_session
            else ""
        )

        # ``model_choice`` may name a FUSION model, which is not a real model
        # id on any provider: handing it to the provider constructor would
        # put it on the wire and 400. Resolved BEFORE the credential gate
        # below, because a fusion model overrides the session's provider —
        # gating on the session default first would refuse to start when that
        # unrelated provider happens to be unconfigured, even though the
        # fusion model's own providers are fine.
        fusion = None
        try:
            from src.providers.fusion_models import get_fusion_model

            candidate = get_fusion_model(model_choice) if model_choice else None
            fusion = candidate if (candidate and candidate.enabled) else None
        except Exception:  # noqa: BLE001 — never block startup on this
            logger.debug("[agent-server] fusion lookup at init failed", exc_info=True)

        if fusion is not None:
            # Selecting a fusion model IS a provider choice: its record
            # names the base provider, which replaces the session's.
            provider_name = fusion.base.provider

        provider_cfg = get_provider_config(provider_name)
        api_key = resolve_api_key(provider_name, provider_cfg)
        if not provider_has_credentials(provider_name, api_key):
            sess.init_error = (
                f"fusion model '{fusion.name}' needs credentials for its base "
                f"provider '{provider_name}'. Run `clawcodex login` to set it up."
                if fusion is not None else
                f"API key for provider '{provider_name}' is not configured. "
                "Run `clawcodex login` to set it up."
            )
            sess.provider_name = provider_name
            return

        if fusion is not None:
            from src.providers.fusion_provider import build_fusion_provider

            # The VISION half is credential-checked too — the same check
            # /model and headless perform. Starting fused is the primary
            # entry point, so it must not be the one that skips it: without
            # this the session starts and then every image degrades to a
            # "vision model failed" note, discovered mid-task.
            vision_ref = fusion.vision
            vision_cfg = get_provider_config(vision_ref.provider)
            if not provider_has_credentials(
                vision_ref.provider, resolve_api_key(vision_ref.provider, vision_cfg)
            ):
                sess.init_error = (
                    f"fusion model '{fusion.name}' needs credentials for its vision "
                    f"provider '{vision_ref.provider}'. Run `clawcodex login` to set it up."
                )
                sess.provider_name = provider_name
                return
            model = fusion.base.model
            provider = build_fusion_provider(fusion)
        else:
            provider_cls = get_provider_class(provider_name)
            model = model_choice or provider_cfg.get("default_model")
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
        # The persisted /model choice is applied ABOVE, pre-construction,
        # via ``model_choice``; the seed no longer writes to the provider
        # (see the note where the store is built). The initial state
        # carries the session's real launch permission mode (critic n5:
        # seeding the default then dispatching the true mode would fire a
        # spurious first mode-change notification). Gated single_session
        # (same rule as ch02's env apply): user-level settings writes must
        # not fire from client-supplied --http sessions.
        if cfg.single_session:
            try:
                from src.state.app_state import (
                    create_app_state_store,
                    replace_state,
                    seed_app_state_from_settings,
                    set_active_provider_supplier,
                )

                set_active_provider_supplier(lambda: sess.provider_name)
                # The persisted-model restore now happens ONCE, above, via
                # ``model_choice`` — before construction, because a fusion
                # name decides which provider gets built at all. The old
                # post-construction ``provider.model = main_loop_model``
                # assignment is therefore gone, not merely guarded: it read
                # ``settings.model`` RAW, so a persisted fusion name that
                # ``get_persisted_model`` had correctly declined (disabled,
                # or not matching an explicit --provider) would still be
                # assigned here and reach the wire as a bogus model id.
                # One rule, one place.
                #
                # The store's ``main_loop_model`` is likewise pinned to the
                # resolution actually in force, so the state the client reads
                # cannot disagree with the provider that was built.
                seeded_state = replace_state(
                    seed_app_state_from_settings(provider_name),
                    permission_mode=mode,
                    main_loop_model=(fusion.name if fusion is not None else model),
                )
                sess.app_state_store = create_app_state_store(seeded_state)
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
        #
        # NOT `or mode == "bypassPermissions"`: availability also relaxes PLAN
        # mode (check.py `should_bypass`), so deriving it from the launch mode
        # meant a session started in Full Access — now the interactive default —
        # had /plan silently permitting every edit and command. Availability is
        # flag/settings-derived only, matching TS isBypassPermissionsModeAvailable
        # and the headless path (headless.py), which never had a mode-implies-
        # availability rule. A session may still START in bypass without it: the
        # engine's own `mode == "bypassPermissions"` clause covers that.
        bypass = cfg.is_bypass_available
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
        # Agent-server sessions are driven by an interactive TUI/direct-connect
        # client.  The transport is non-TTY, but the session is not headless
        # (notably, it must expose TaskV2 rather than TodoWrite).
        tool_context.options.is_non_interactive_session = False
        # Scheduled tasks (/loop, Cron*, ScheduleWakeup): the tools write to
        # the session's scheduler; the worker's idle branch fires due prompts.
        tool_context.cron_scheduler = sess.cron_scheduler
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
        # Plan-mode port: mid-turn mode flips (plan-approval setMode,
        # EnterPlanMode/ExitPlanMode) push the new mode to the TUI footer.
        tool_context.on_permission_mode_change = sess._notify_permission_mode
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
    """Drop every tool for which ``keep(name)`` is False.

    Backs ``--allowed-tools`` / ``--disallowed-tools``: removing the tool from
    the registry keeps its schema out of the ``tools=`` param sent to the
    model, not just blocked at execution time.
    """
    try:
        entries = list(registry.list_tools())
    except Exception:  # noqa: BLE001
        return
    for tool in entries:
        name = getattr(tool, "name", "")
        if not keep(name):
            try:
                registry.remove_tool(name)
            except Exception:  # noqa: BLE001
                continue


# ─── message shaping ─────────────────────────────────────────────────────────


def _non_interactive_ask_user(questions: list[dict]) -> dict[str, str]:
    """AskUserQuestion substitute for transports with no dialog-capable client.

    Mirrors headless's ``_noop_ask_user``: NOT ``None`` (that is the decline
    signal) and not empty strings (which make the model flail) -- an explicit
    "proceed autonomously" so the turn keeps moving.
    """
    from src.tool_system.tools.ask_user_question import NON_INTERACTIVE_ANSWER

    return {
        q["question"]: NON_INTERACTIVE_ANSWER
        for q in questions
        if isinstance(q, dict) and isinstance(q.get("question"), str)
    }


def _display_tool_result(value: Any) -> dict | None:
    """Trim a rich tool output for the wire (display data only).

    Recognizes self-describing shapes rather than a tool name so mid-turn
    clients can render without tool_use bookkeeping:

    * Edit/Write (``type``/``filePath``/``structuredPatch``) — deliberate
      delta from real CC's full-parity ``tool_use_result``: ``originalFile``
      is dropped (the display renderer never reads it) and update-type
      ``content`` (the full post-edit file) is reduced to ``firstLine``
      (language/shebang detection only; the original uses the pre-edit first
      line — differs only when line 1 itself changed); create-type keeps
      ``content`` for the file preview.
    * Read-an-image (``type: "image"`` + ``file``) — reduced to ``originalSize``,
      the only input the one-line render needs ("Read image (12.3KB)", UI.tsx
      renderToolResultMessage). The base64 payload is dropped: it belongs on the
      model-facing tool_result content, never on a display envelope.
    * AskUserQuestion (``type: "ask_user_question"``) — reduced to ``answers``
      or ``declined``. The ``questions`` bodies are deliberately dropped:
      ``answers`` is already keyed by question text, and the option/description
      bodies are dialog INPUT, not transcript output.
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
    from src.tool_system.tools.ask_user_question import RESULT_TYPE

    if value.get("type") == RESULT_TYPE:
        # Answered/declined AskUserQuestion: forward just what the transcript
        # renders ("· question → answer" rows, or the declined line). The
        # ``questions`` list is deliberately dropped -- ``answers`` is already
        # keyed by question text, and the option/description bodies are dialog
        # input, not transcript output.
        if value.get("declined"):
            return {"type": RESULT_TYPE, "declined": True}
        answers = value.get("answers")
        if not isinstance(answers, dict):
            return None
        return {
            "type": RESULT_TYPE,
            "answers": {
                str(q): str(a) for q, a in answers.items() if isinstance(q, str)
            },
        }
    if value.get("type") == "image":
        # Read-an-image: forward ONLY the byte count the one-line render needs
        # (UI.tsx renderToolResultMessage: "Read image (12.3KB)"). The base64
        # payload is deliberately dropped -- it belongs on the tool_result
        # content bound for the model, never on the display envelope. Without
        # this branch the function returned None, the client had no display
        # data to key on, and its content fallback JSON-dumped the whole
        # base64 image into the transcript.
        file_data = value.get("file")
        if not isinstance(file_data, dict):
            return None
        size = file_data.get("originalSize")
        # ``int`` only, and never ``bool`` (an int subclass -- a True here means a
        # buggy producer, not 1 byte). Deliberately not accepting ``float``:
        # ``int(nan)``/``int(inf)`` RAISE, and this runs inside ``_sdk_envelope``
        # whose caller ``on_message`` has no guard, so a stray non-finite would
        # be turn-fatal. Every other malformed input here declines with None; a
        # float size does too. The real producer passes ``stat.st_size``
        # (read.py), already an int.
        if not isinstance(size, int) or isinstance(size, bool):
            return None
        return {"type": "image", "originalSize": size}
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


def _sessions_dir() -> Path:
    # Honors $CLAWCODEX_CONFIG_DIR (default ~/.clawcodex/sessions).
    from src.utils.clawcodex_dirs import get_sessions_dir

    return get_sessions_dir()


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


#: The reference's reference-pattern, narrowed to image refs
#: (``history.ts:66`` parseReferences). ``[Image #3]`` in the prompt text is what
#: keeps image #3 attached.
_IMAGE_REF_RE = re.compile(r"\[Image #(\d+)\]")


def _parse_image_refs(text: str) -> set[int]:
    """Image ids still referenced by an ``[Image #N]`` chip in ``text``.

    Ids start at 1, so ``#0`` is never real — dropped here rather than left to
    coincidentally not match, mirroring the reference's ``id > 0`` filter
    (history.ts:74).
    """
    ids = {int(m.group(1)) for m in _IMAGE_REF_RE.finditer(text)}
    return {i for i in ids if i > 0}


def _content_text(content) -> str:
    """Flatten prompt content to text for chip scanning."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


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


def _current_logo_color() -> str | None:
    """The persisted /logo palette name, or None for the default/unset (the
    ``get_settings`` rider; the TUI's startup read is its own synchronous
    config.json read — see ui-tui/src/lib/logoPalettes.ts)."""
    try:
        from src.config import load_config
        from src.utils.logo_palettes import is_logo_palette_name

        value = load_config().get("logoColor")
        return value if is_logo_palette_name(value) else None
    except Exception:  # noqa: BLE001
        return None


def _recap_setting_enabled() -> bool:
    """The live end-of-turn-recap toggle (the ``get_settings`` rider for
    /recap status). Defaults on — matching CC, where recaps ship enabled
    with an opt-out."""
    try:
        from src.settings.settings import get_settings

        return bool(getattr(get_settings(), "recap_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def _current_mode(tool_context: Any, default: str) -> str:
    if tool_context is None:
        return default
    pc = getattr(tool_context, "permission_context", None)
    return getattr(pc, "mode", default) if pc is not None else default


def _set_mode(tool_context: Any, mode: str) -> None:
    """Set the live permission mode, running the plan-mode transition seam.

    ``transition_permission_mode`` (the transitionPermissionMode port) arms
    the plan enter/exit attachment flags and manages the ``pre_plan_mode``
    stash; the returned context is REBOUND (functional-update contract shared
    with ``registry._apply_and_persist_updates``) with the new mode applied.
    """
    pc = getattr(tool_context, "permission_context", None)
    if pc is None:
        return
    try:
        from dataclasses import replace as _dc_replace

        from src.permissions.plan_transitions import transition_permission_mode

        next_pc = transition_permission_mode(pc.mode, mode, pc)
        tool_context.permission_context = _dc_replace(next_pc, mode=mode)
    except Exception:  # noqa: BLE001 — fall back to the bare mode set
        try:
            pc.mode = mode  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "AgentServerConfig",
    "PROTOCOL_VERSION",
    "make_spawn_agent",
]
