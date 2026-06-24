"""Bridge between the agent loop and the Textual UI.

The canonical agent loop (:func:`src.query.query.query`) is async and
performs blocking HTTP calls under the hood, so this module runs it on
a worker thread via the F.1 adapter
(:func:`src.query.agent_loop_compat.run_query_as_agent_loop`). The
worker thread owns its own fresh asyncio loop (NOT Textual's main
loop, which would block UI rendering during model streams). This
module owns that thread plus the translation layer that marshals
events back to the Textual screen:

* ``on_event(ToolEvent)``   → :class:`ToolEventMessage`.
* ``on_text_chunk(str)``    → :class:`AssistantChunk` (live streaming).
* permission request        → :class:`PermissionRequested` + blocking
  wait on a :class:`threading.Event`, letting the worker thread unblock
  only when the user has interacted with
  :class:`src.tui.screens.permission_modal.PermissionModal`.

Keeping this logic out of :class:`src.tui.app.ClawCodexTUI` lets unit
tests drive :class:`AgentBridge` with a fake agent loop (see
``tests/test_esc_cancel_propagation.py`` and
``tests/test_session_persistence.py``, which construct the bridge with
stub dependencies).
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.permissions.types import PermissionAskReply, PermissionAskRequest

from src.agent import Session
from src.tool_system.renderers import AgentLoopResult, ToolEvent
from src.query.agent_loop_compat import (
    build_effective_system_prompt,
    run_query_as_agent_loop,
)
from src.tool_system.context import ToolContext
from src.tool_system.registry import ToolRegistry
from src.utils.abort_controller import AbortController, AbortError

from .messages import (
    AdvisorEventMessage,
    AgentRunFinished,
    AgentRunStarted,
    AssistantChunk,
    AssistantMessage,
    PermissionRequested,
    QueuedPromptReady,
    ToolEventMessage,
)
from .state import AppState


class AgentBridge:
    """Owns the agent-loop worker thread on behalf of the TUI."""

    def __init__(
        self,
        *,
        post_message: Callable[[Any], None],
        session: Session,
        provider: Any,
        tool_registry: ToolRegistry,
        tool_context: ToolContext,
        app_state: AppState,
        run_worker: Callable[..., Any],
        max_turns: int = 20,
        stream: bool = True,
    ) -> None:
        self._post = post_message
        self._session = session
        self._provider = provider
        self._tool_registry = tool_registry
        self._tool_context = tool_context
        self._state = app_state
        self._run_worker = run_worker
        self._max_turns = max_turns
        self._stream = stream
        self._busy_lock = threading.Lock()
        self._busy = False
        # Per-run abort controller. Created fresh in :meth:`submit` and
        # tripped by :meth:`cancel` (ESC from the prompt). The agent
        # loop checks the signal at safe boundaries; the streaming
        # callback also raises :class:`AbortError` on the worker thread
        # to tear down an in-flight HTTP stream cleanly.
        self._abort_controller: AbortController | None = None
        # Session-persistence producer: writes the metadata + JSONL transcript
        # that resume_session / the resume screen consume. Best-effort (never
        # raises); start() initializes metadata only when absent so a
        # /rename-set title survives restarts.
        from src.services.session_persistence import SessionPersister

        self._persister = SessionPersister(session_id=session.session_id)
        self._persister.start(
            model=getattr(provider, "model", "") or "", cwd=os.getcwd()
        )
        # Wire permission handler: the tool dispatcher calls this from
        # the worker thread, we post to the UI and block on an Event.
        tool_context.permission_handler = self._permission_handler
        # Advisor IDs we've already mirrored to the UI. The high-level
        # SDK stream doesn't give us per-event hooks for server tools,
        # so the bridge inspects the assembled conversation after each
        # turn — without dedup, every turn would re-emit the same
        # events for blocks that landed in earlier turns.
        #
        # ``_last_scanned_msg_index`` is an optimization: iterating the
        # full message list on every turn is O(N*B). Tracking how far
        # we've scanned lets us start at the new tail instead. Reset
        # together with ``_emitted_advisor_ids`` via
        # ``reset_advisor_dedup`` whenever the message list is
        # truncated or replaced.
        self._emitted_advisor_ids: set[str] = set()
        self._last_scanned_msg_index: int = 0
        # C3b /thinking session override. None = auto (query gates on
        # model support); True/False = explicit user toggle (TS
        # ThinkingToggle: "Enable or disable thinking for this session").
        self.extended_thinking: bool | None = None
        # C4 bash-mode: user texts that arrived while a run was in flight.
        # Appending mid-run can interleave between a tool_use and its
        # tool_result in the conversation (durably, via the persister) —
        # so they defer and drain in _finish() after the run's appends
        # are over.
        self._deferred_user_texts: list[str] = []

    def reset_advisor_dedup(self) -> None:
        """Drop the advisor-dedup state.

        Call this when conversation history is wiped or summarized so
        the IDs we tracked no longer correspond to anything in the
        live message list — keeping them around leaks memory (bounded:
        one UUID per advisor call ever made) and risks suppressing a
        legitimate re-render if a post-compact replay reuses an ID.
        The TUI wires this on ``/clear`` (``tui/app.py:__clear__`` and
        the idle-return "clear" choice). Other reset paths
        (``/compact``, programmatic message wipe) don't yet trigger it;
        the leak is harmless until they do.
        """
        self._emitted_advisor_ids.clear()
        self._last_scanned_msg_index = 0

    # ---- public API ----
    @property
    def busy(self) -> bool:
        return self._busy

    def append_user_texts(self, texts: tuple[str, ...] | list[str]) -> None:
        """Append plain user texts to the conversation + session store.

        Safe at any time: while a run is in flight the texts DEFER and
        drain in ``_finish()`` after the run's own appends — appending
        mid-run could land between an assistant ``tool_use`` and its
        ``tool_result``, making the next turn's pairing repair tell the
        model a successful tool failed (C4 review B1). Idle → immediate
        append + persist.
        """

        texts = [t for t in texts if t]
        if not texts:
            return
        with self._busy_lock:
            if self._busy:
                self._deferred_user_texts.extend(texts)
                return
        for text in texts:
            try:
                self._session.conversation.add_user_message(text)
                self._persister.record({"role": "user", "content": text})
            except Exception:
                pass
        try:
            self._persister.flush()
        except Exception:
            pass

    def _drain_deferred_user_texts(self) -> None:
        """Append parked texts (call only while no run is appending).

        Residual window (C4 review note 1): a bash completion landing
        while ``_finish`` is mid-drain/flush still defers — busy cannot
        clear earlier without re-opening the interleave hazard for a
        racing ``submit`` (its appends + controller swap). Those parked
        texts land via the ``submit()`` drain below, BEFORE the next
        prompt, so they are never lost and order stays correct.
        """

        with self._busy_lock:
            pending, self._deferred_user_texts = self._deferred_user_texts, []
        for text in pending:
            try:
                self._session.conversation.add_user_message(text)
                self._persister.record({"role": "user", "content": text})
            except Exception:
                pass

    def resume_session(self, session_id: str) -> list[Any] | None:
        """Swap the live conversation to a persisted session (C2 /resume).

        Mirrors TS resume semantics: the picked session BECOMES the active
        session — its id is installed via ``bootstrap.state.switch_session``
        (the designated resume path, fires ``session_switched``), the
        persister re-targets it so subsequent turns append to the SAME
        store, and the in-memory conversation is replaced by the stored
        transcript. Returns the loaded typed messages for the UI to
        re-render, or ``None`` when refused (worker busy) / nothing
        stored. Must be called from the UI thread while idle — the guard
        is the same ``_busy_lock`` gate ``submit`` uses.
        """

        with self._busy_lock:
            if self._busy:
                return None

            from src.services.session_persistence import SessionPersister
            from src.services.session_resume import resume_session

            # The full TS-parity reader: malformed-line recovery, orphaned
            # tool_use repair, snip boundaries, cross-project path
            # adjustment (session/resume.ts). Synchronous on the UI thread
            # — acceptable for the degraded C2 scope (TS reads async);
            # revisit if multi-MB transcripts make the freeze noticeable.
            try:
                result = resume_session(session_id, current_cwd=os.getcwd())
            except Exception:
                # Unreadable transcript (permissions, dir-shaped file…)
                # must refuse, not crash the Textual callback chain.
                return None
            if not result.success or not result.messages:
                return None
            messages = result.messages

            from src.bootstrap.state import switch_session
            from src.services.cost_restore import restore_cost_state_for_session

            # TS ResumeConversation.tsx:224-227: switchSession then
            # restoreCostStateForSession, in lockstep. (TS also passes the
            # session's project dir to switchSession; nothing consumes
            # get_session_project_dir() in Python yet, so it is omitted.)
            switch_session(session_id)
            # Best-effort: TUI-born sessions don't write the flat cost
            # snapshot yet, so this is usually a no-op today; it exists so
            # resume of snapshot-bearing sessions restores accumulators.
            restore_cost_state_for_session(session_id)

            conversation = self._session.conversation
            conversation.messages.clear()
            conversation.messages.extend(messages)
            self._session.session_id = session_id

            # Advisor dedup: clear the emitted-ID set (old IDs are gone),
            # but point the scan cursor at the END of the repopulated list
            # — index 0 (the /clear semantics of reset_advisor_dedup)
            # would make the first post-resume scan re-emit every
            # HISTORICAL advisor event as fresh UI rows.
            self._emitted_advisor_ids.clear()
            self._last_scanned_msg_index = len(conversation.messages)

            # Re-target persistence; start() only initializes metadata when
            # absent, so the resumed session's existing metadata (title,
            # counts) is preserved. NOTE: AppState.usage token counters are
            # deliberately NOT reset/hydrated here — the status line keeps
            # counting from the live process (decision recorded in the C2
            # review; revisit with the C3 context/status work).
            self._persister = SessionPersister(session_id=session_id)
            self._persister.start(
                model=getattr(self._provider, "model", "") or "",
                cwd=os.getcwd(),
            )
            return list(messages)

    def submit(self, prompt: str) -> bool:
        """Start an agent run for ``prompt``. Returns False if busy.

        When a run is already in flight the prompt is **enqueued** onto
        ``app_state.queued_prompts`` *under* ``_busy_lock``, so the
        enqueue is atomic with the busy check. This pairs with
        :meth:`_finish`, which clears ``busy`` and reads the queue under
        the same lock: a run can therefore never finish in the gap
        between "is it busy?" and "enqueue", which would otherwise leave
        the prompt stranded with no drain ever posted. The REPL drains
        the queue one-per-turn (see ``QueuedPromptReady``).
        """

        with self._busy_lock:
            if self._busy:
                self._state.queued_prompts.append(prompt)
                return False
            self._busy = True
            self._abort_controller = AbortController()
            # Plumb the controller onto the tool context BEFORE we spawn
            # the worker. Tools (Bash supervisor, Agent subagents, the
            # streaming executor, tool hooks) read
            # ``context.abort_controller`` to learn whether the user has
            # asked to interrupt; without this assignment they see
            # ``None`` and run to completion regardless of ESC. The Agent
            # subagent path is the worst case — ``run_agent`` inherits
            # the parent's controller via ``parent_context.abort_controller``,
            # so a missing field here forces the subagent to mint a fresh
            # controller that is never tripped by ESC.
            self._tool_context.abort_controller = self._abort_controller

        # C4: texts parked during a previous run's teardown land BEFORE
        # this prompt. We hold the run slot (busy=True) so new bash
        # completions defer rather than racing this append; the worker
        # hasn't spawned yet, so nothing else writes the conversation.
        self._drain_deferred_user_texts()
        self._session.conversation.add_user_message(prompt)
        self._persister.record_user(prompt)
        self._post(AgentRunStarted(prompt=prompt))
        # Empty verb → AppState.set_thinking samples a random SPINNER_VERBS
        # entry (TS Spinner.tsx:166), instead of a fixed "Synthesizing".
        self._state.set_thinking(True)
        self._run_worker(
            self._run_agent_in_thread,
            thread=True,
            exclusive=True,
            name="agent-loop",
        )
        return True

    def cancel(self, reason: str = "user_interrupt") -> bool:
        """Trip the active run's abort signal. Returns True if a run was cancelled.

        Safe to call from any thread. The agent loop checks the signal
        at the next safe boundary (next turn, next tool call, next
        streaming chunk) and unwinds; ``_run_agent_in_thread`` then
        posts an ``AgentRunFinished`` and clears the busy flag.
        """

        with self._busy_lock:
            controller = self._abort_controller if self._busy else None
        if controller is None:
            return False
        controller.abort(reason)
        return True

    # ---- worker implementation ----
    def _run_agent_in_thread(self) -> None:
        controller = self._abort_controller

        def _on_event(event: ToolEvent) -> None:
            # Keep the app_state in sync so StatusLine / overlays can
            # observe in-progress tool ids.
            if event.kind == "tool_use" and event.tool_use_id:
                self._state.mark_tool_started(event.tool_use_id)
            elif event.kind in ("tool_result", "tool_error") and event.tool_use_id:
                self._state.mark_tool_finished(event.tool_use_id)
            self._post(
                ToolEventMessage(
                    kind=event.kind,
                    tool_name=event.tool_name,
                    tool_input=_safe_copy(event.tool_input),
                    tool_output=_safe_copy(event.tool_output),
                    tool_use_id=event.tool_use_id,
                    is_error=event.is_error,
                    error=event.error,
                )
            )

        def _on_text(chunk: str) -> None:
            # Bail out of the provider stream as soon as the user hits
            # ESC; raising from the callback breaks out of the
            # Anthropic SDK's ``with client.messages.stream(...)``
            # context manager and tears down the HTTP connection.
            if controller is not None and controller.signal.aborted:
                raise AbortError(controller.signal.reason or "user_interrupt")
            self._state.append_streaming_text(chunk)
            self._post(AssistantChunk(text=chunk))

        try:
            # Ch5/F.3 cutover: route TUI through the canonical query()
            # loop via the F.1 adapter. The Textual ``@work(thread=True)``
            # worker doesn't have an asyncio loop, so we spin up a fresh
            # one INSIDE the worker thread (NOT on the main loop —
            # ``@work(thread=False)`` would block Textual's UI rendering
            # during model streams). Pre-build the effective system
            # prompt (CLAUDE.md, style, git status) — legacy
            # run_agent_loop did this internally; the adapter doesn't.
            import asyncio as _asyncio
            from src.outputStyles import resolve_output_style
            _style_prompt = resolve_output_style(
                getattr(self._tool_context, "output_style_name", None),
                getattr(self._tool_context, "output_style_dir", None),
            ).prompt
            effective_system_prompt = build_effective_system_prompt(
                _style_prompt, self._tool_context, provider=self._provider,
            )

            def _persist(msg: Any) -> None:
                # BLOCKING #2 fix: persist FULL message (tool_use /
                # tool_result blocks included) so subsequent turns can
                # pair tool_use IDs to results. Plain
                # ``add_assistant_message`` loses block structure.
                # Critic S3: log failures instead of swallowing — a
                # persist failure means the conversation is corrupted
                # for the next turn (tool_use without tool_result will
                # 400 at the API). Surface it now, not later.
                try:
                    self._session.conversation.add_message(msg.role, msg.content)
                    # Mirror into the session transcript (best-effort, never raises).
                    self._persister.record(msg)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "Failed to persist message into conversation: "
                        "role=%s; next-turn API may reject the call.",
                        getattr(msg, "role", "?"),
                    )

            _loop = _asyncio.new_event_loop()
            try:
                compat_result = _loop.run_until_complete(run_query_as_agent_loop(
                    initial_messages=list(self._session.conversation.messages),
                    provider=self._provider,
                    tool_registry=self._tool_registry,
                    tool_context=self._tool_context,
                    system_prompt=effective_system_prompt,
                    max_turns=self._max_turns,
                    on_event=_on_event,
                    on_text_chunk=_on_text if self._stream else None,
                    on_message=_persist,
                    # Critic C2: pass the OWNING controller (not just
                    # its signal) so the provider sees the same signal
                    # ESC trips. See same fix in headless.py for why.
                    abort_controller=controller,
                    # C3b /thinking session override (None = auto).
                    extended_thinking=self.extended_thinking,
                ))
            finally:
                _loop.close()
            result = AgentLoopResult(
                response_text=compat_result.response_text,
                usage=(
                    compat_result.usage
                    if compat_result.num_turns > 0
                    else None
                ),
                num_turns=compat_result.num_turns,
            )
        except AbortError:
            self._post(
                AgentRunFinished(
                    response_text="",
                    num_turns=0,
                    usage=None,
                    error="Cancelled by user",
                )
            )
            self._finish()
            return
        except Exception as exc:  # pragma: no cover — surfaced to UI
            self._post(
                AgentRunFinished(
                    response_text="",
                    num_turns=0,
                    usage=None,
                    error=str(exc),
                )
            )
            self._finish()
            return

        self._post(AssistantMessage(text=result.response_text))
        # Surface any advisor activity from this run as transcript rows.
        # The Python provider path doesn't emit per-event hooks for
        # server tools (the SDK's high-level ``messages.stream`` only
        # signals text deltas to ``on_text_chunk``), so the bridge
        # inspects the final assembled assistant content. This is
        # idempotent across turns: ``_emit_advisor_events`` only posts
        # events for advisor blocks added during the current run.
        try:
            self._emit_advisor_events()
        except Exception:
            # Never let an advisor-rendering issue mask the model's
            # actual response.
            pass
        if result.usage:
            try:
                self._state.usage.update(
                    {
                        "input_tokens": self._state.usage.get("input_tokens", 0)
                        + int(result.usage.get("input_tokens", 0) or 0),
                        "output_tokens": self._state.usage.get("output_tokens", 0)
                        + int(result.usage.get("output_tokens", 0) or 0),
                    }
                )
                # C3a: live-context measure = the LAST response's
                # prompt-side tokens (incl. cache reads/creation), via the
                # compat layer's last-wins ``last_*`` keys — NOT the
                # cumulative sums, which double-count across multi-tool
                # runs and drop the cache components (TS tokens.ts:407-420).
                last_ctx = (
                    int(result.usage.get("last_input_tokens", 0) or 0)
                    + int(
                        result.usage.get("last_cache_read_input_tokens", 0)
                        or 0
                    )
                    + int(
                        result.usage.get(
                            "last_cache_creation_input_tokens", 0
                        )
                        or 0
                    )
                )
                if last_ctx:
                    self._state.last_turn_input_tokens = last_ctx
            except Exception:
                pass
        # Mirror the client-side advisor token counts from tool_context
        # into ``state.usage`` so the StatusLine widget can surface them
        # next to the worker tokens. ``tool_context.advisor_*`` is
        # accumulated by ``AdvisorTool._advisor_call`` (PR #190) on every
        # client-side consultation; this is the TUI-side parity of the
        # legacy REPL's ``_bottom_toolbar`` reading the same ctx fields.
        # NOTE: server-side advisor attribution (Anthropic API's
        # ``usage.iterations[]`` with ``type="advisor_message"``) is NOT
        # currently surfaced — the Python SDK 0.88.0 we depend on only
        # defines ``"message"`` and ``"compaction"`` discriminators
        # (see anthropic/types/beta/beta_message_iteration_usage.py).
        # Add a separate accumulator + parser when the SDK gains the
        # advisor discriminator OR when we move to direct HTTP parsing.
        try:
            adv_in = int(getattr(self._tool_context, "advisor_input_tokens", 0) or 0)
            adv_out = int(getattr(self._tool_context, "advisor_output_tokens", 0) or 0)
            if adv_in or adv_out:
                self._state.usage["advisor_input_tokens"] = adv_in
                self._state.usage["advisor_output_tokens"] = adv_out
        except Exception:
            pass
        self._post(
            AgentRunFinished(
                response_text=result.response_text,
                num_turns=result.num_turns,
                usage=result.usage,
            )
        )
        self._finish()

    def _finish(self) -> None:
        # C4: user texts that arrived mid-run append AFTER the run's own
        # conversation writes are over (and before the durable flush
        # below carries them to disk in the correct order).
        self._drain_deferred_user_texts()
        # Durable persistence at the end of EVERY run (completion, abort, error).
        self._persister.flush()
        self._state.set_thinking(False)
        self._state.clear_streaming_text()
        with self._busy_lock:
            self._busy = False
            # Replace the per-run controller on the shared tool context
            # with a fresh one so the next ``submit()`` starts from a
            # clean state. Leaving an aborted controller in place would
            # cause the next prompt's first tool dispatch to see
            # ``signal.aborted == True`` and short-circuit before the
            # user has even pressed ESC.
            #
            # The dataclass field is non-optional, so we can't simply
            # clear to ``None`` — we install an untripped controller
            # that mirrors the dataclass default.
            #
            # Safety note: ``_finish`` runs on the worker thread *after*
            # ``run_agent_loop`` has returned or raised, so no in-flight
            # tool can be reading ``context.abort_controller`` from this
            # thread at the moment we replace it. Detached background
            # processes (e.g. ``spawn_background_bash``) capture their
            # own controller reference at spawn time rather than re-
            # reading the context field, so replacing here doesn't
            # orphan them either.
            self._tool_context.abort_controller = AbortController()
            # Drain decision, made under the same lock as the enqueue in
            # ``submit`` so the two can't interleave. ``busy`` is already
            # False above, so a ``QueuedPromptReady`` posted now is
            # guaranteed to find the bridge idle on the UI thread.
            has_queued = bool(self._state.queued_prompts)
        # Post OUTSIDE the lock (``_post`` marshals to the UI thread). The
        # worker-side non-empty check is only a filter — the REPL handler
        # re-checks idle + non-empty before popping, so a queue cleared by
        # ESC in the meantime is a harmless no-op. Fires on every exit
        # path (completion, abort, error) because all route through here.
        if has_queued:
            self._post(QueuedPromptReady())

    # ---- advisor rendering ----
    def _emit_advisor_events(self) -> None:
        """Scan the conversation for advisor blocks and post UI events for new ones.

        Iterates the assembled assistant messages (which now persist
        advisor blocks via ``ChatResponse.raw_content_blocks`` — see
        ``src/providers/anthropic_provider.py:_build_chat_response``)
        and posts one ``AdvisorEventMessage(kind="start")`` and one
        ``AdvisorEventMessage(kind="result")`` per advisor pair. Both
        emit in order so the transcript can mount the row in its
        running state before flipping it to done.

        Starts iteration at ``self._last_scanned_msg_index`` and
        advances the cursor at the end. This avoids the O(N×B) per-
        turn cost that grows over a long session — we only scan
        messages that landed since the last scan, which on a healthy
        agentic loop is just the latest assistant turn.
        """
        from src.utils.advisor import (
            extract_advisor_error_code,
            extract_advisor_result_text,
        )
        messages = getattr(self._session.conversation, "messages", None)
        if not messages:
            return
        # Defend against a wipe (e.g. /clear): if the conversation
        # shrank since last scan, the index is stale. Start over.
        start_idx = self._last_scanned_msg_index
        if start_idx > len(messages):
            start_idx = 0
        # Read the active advisor model from settings; ``server_tool_use``
        # blocks don't include it (it's parameterized on the schema, not
        # echoed back), but the user-facing label is much friendlier
        # with the model name attached.
        try:
            from src.settings.settings import get_settings
            advisor_model = (get_settings().advisor_model or "") or None
        except Exception:
            advisor_model = None
        # Client-side advisor results land on the NEXT (user-role)
        # message after the assistant's tool_use, since the dispatcher
        # routes the call through the tool registry. Build a lookup
        # of ``tool_use_id → (text, is_error)`` from user messages so
        # the inner loop can pair without an O(N²) walk.
        client_side_results: dict[str, tuple[str, bool]] = {}
        for msg in messages[start_idx:]:
            mcontent = getattr(msg, "content", None)
            if not isinstance(mcontent, list):
                continue
            for blk in mcontent:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") != "tool_result":
                    continue
                use_id = blk.get("tool_use_id")
                if not isinstance(use_id, str) or not use_id:
                    continue
                rc = blk.get("content")
                # tool_result.content can be a string or a list of
                # content blocks (multimodal tools). The advisor only
                # ever emits string content (advisor's reply text).
                if isinstance(rc, str):
                    text = rc
                elif isinstance(rc, list):
                    parts: list[str] = []
                    for b in rc:
                        if isinstance(b, dict) and isinstance(b.get("text"), str):
                            parts.append(b["text"])
                    text = "\n".join(parts)
                else:
                    text = str(rc) if rc is not None else ""
                client_side_results[use_id] = (text, bool(blk.get("is_error")))

        for msg in messages[start_idx:]:
            content = getattr(msg, "content", None)
            if not isinstance(content, list):
                continue
            # Pair-finding pass: collect (use_id → use_block_index) and
            # (use_id → result_content) within this assistant message.
            for i, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                bname = block.get("name")
                bid = block.get("id") or block.get("tool_use_id")
                if not isinstance(bid, str) or not bid:
                    continue
                if bid in self._emitted_advisor_ids:
                    continue
                # Client-side advisor: regular tool_use(name="advisor")
                # on an assistant message; the result is on a later
                # user-role message. We've already indexed those above.
                if btype == "tool_use" and bname == "advisor":
                    self._post(
                        AdvisorEventMessage(
                            kind="start",
                            tool_use_id=bid,
                            advisor_model=advisor_model,
                        )
                    )
                    pair = client_side_results.get(bid)
                    if pair is None:
                        # The dispatcher hasn't produced a result yet
                        # (turn in flight, or interrupted). Synthesize
                        # the interrupted event so the row doesn't spin
                        # forever. If the result lands later, the
                        # ``_emitted_advisor_ids`` guard prevents
                        # double-rendering.
                        self._post(
                            AdvisorEventMessage(
                                kind="result",
                                tool_use_id=bid,
                                advisor_model=advisor_model,
                                error_code="interrupted",
                            )
                        )
                    else:
                        result_text, is_err = pair
                        self._post(
                            AdvisorEventMessage(
                                kind="result",
                                tool_use_id=bid,
                                advisor_model=advisor_model,
                                text=None if is_err else result_text,
                                error_code=(
                                    result_text[:120] if is_err and result_text
                                    else ("error" if is_err else None)
                                ),
                            )
                        )
                    self._emitted_advisor_ids.add(bid)
                    continue
                if btype == "server_tool_use" and bname == "advisor":
                    self._post(
                        AdvisorEventMessage(
                            kind="start",
                            tool_use_id=bid,
                            advisor_model=advisor_model,
                        )
                    )
                    # Look for a matching advisor_tool_result anywhere
                    # later in the same assistant message.
                    result_block = None
                    for later in content[i + 1:]:
                        if (
                            isinstance(later, dict)
                            and later.get("type") == "advisor_tool_result"
                            and later.get("tool_use_id") == bid
                        ):
                            result_block = later
                            break
                    if result_block is None:
                        # No result on this assistant turn — the use was
                        # interrupted. Synthesize an error event so the
                        # UI doesn't leave the row spinning forever.
                        self._post(
                            AdvisorEventMessage(
                                kind="result",
                                tool_use_id=bid,
                                advisor_model=advisor_model,
                                error_code="interrupted",
                            )
                        )
                        self._emitted_advisor_ids.add(bid)
                        continue
                    rcontent = result_block.get("content")
                    text = extract_advisor_result_text(rcontent)
                    err_code = extract_advisor_error_code(rcontent)
                    self._post(
                        AdvisorEventMessage(
                            kind="result",
                            tool_use_id=bid,
                            advisor_model=advisor_model,
                            text=text,
                            error_code=err_code,
                        )
                    )
                    self._emitted_advisor_ids.add(bid)
        # Cursor forward so the next scan only inspects new messages.
        self._last_scanned_msg_index = len(messages)

    # ---- permission bridge ----
    def _permission_handler(
        self, request: "PermissionAskRequest"
    ) -> "PermissionAskReply":
        """Called from the worker thread whenever the tool dispatcher
        wants user approval.

        Receives a :class:`src.permissions.types.PermissionAskRequest`
        and returns a :class:`~src.permissions.types.PermissionAskReply`.
        Posts a :class:`PermissionRequested` to the UI (with the REAL
        ``tool_input`` so the modal's per-tool previews render, and the
        derived rule ``suggestions`` so the always-allow option shows);
        blocks the worker until the modal resolves via
        :class:`PermissionResolved`.
        """

        from src.permissions.types import PermissionAskReply

        done = threading.Event()
        outcome: dict[str, Any] = {"reply": PermissionAskReply(behavior="deny")}

        def _decide(reply: Any) -> None:
            outcome["reply"] = reply
            done.set()

        tool_input = _safe_copy(request.tool_input)
        pending = self._state.enqueue_permission(
            tool_name=request.tool_name,
            message=request.message,
            suggestions=tuple(request.suggestions),
            tool_input=tool_input,
            decide=_decide,
        )
        self._post(
            PermissionRequested(
                request_id=pending.request_id,
                tool_name=request.tool_name,
                message=request.message,
                suggestions=tuple(request.suggestions),
                tool_input=tool_input,
            )
        )
        # Wait for the UI to call ``_decide``. No timeout — the UI is
        # expected to always resolve the request (defaulting to deny on
        # Escape / Ctrl+C). A stuck permission will hold the worker
        # thread, which is the same failure mode as the legacy REPL's
        # ``input()`` call.
        done.wait()
        # Remove the entry from the state queue; the modal already
        # dismissed itself and emitted ``PermissionResolved``.
        self._state.resolve_permission(pending.request_id)
        return outcome["reply"]


def _safe_copy(value: Any) -> Any:
    """Best-effort clone so the UI thread doesn't mutate tool-thread memory."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _safe_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_copy(v) for v in value]
    return value


__all__ = ["AgentBridge"]
