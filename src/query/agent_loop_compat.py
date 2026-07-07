"""Adapter from ``query.query`` → ``AgentLoopResult`` shape.

Headless and TUI production paths invoke ``run_query_as_agent_loop``
(async) to drive the canonical ``query()`` loop while keeping the
legacy ``AgentLoopResult`` return contract. Tests inherited from the
pre-consolidation era call the sync wrapper ``run_query_as_agent_loop_sync``
which mimics the deleted ``run_agent_loop`` signature exactly so per-
test churn is just an import swap. The original
``src.tool_system.agent_loop`` module is gone (Stage 4 of the
consolidation, PR #N).

Event-loop ownership patterns the callers use:

  * **Headless callers** (single-shot ``claude -p``) wrap the adapter
    in ``asyncio.run()`` — the entry already starts its own event
    loop and runs to completion.
  * **TUI callers** (Textual ``@work(thread=True)`` workers) invoke
    the adapter via ``asyncio.new_event_loop()`` inside the worker
    thread so the UI's event loop stays free. Do NOT use
    ``@work(thread=False)`` — that would put the loop on Textual's
    main event loop and block UI rendering during model streams.

Also exports ``build_effective_system_prompt`` (CLAUDE.md + style +
git status assembly) so the cutover code can pre-build the system
prompt before calling the adapter — ``query()`` expects it pre-built.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..tool_system.context import ToolContext
from ..tool_system.registry import ToolRegistry
from ..providers.base import BaseProvider
from ..types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock
from ..types.messages import (
    AssistantMessage,
    Message,
    UserMessage,
)
from ..utils.abort_controller import AbortController, AbortError, AbortSignal

from .query import QueryParams, StreamEvent, query
from .transitions import Terminal, TerminalHolder


# Renderer types are now canonically in ``src.tool_system.renderers``
# (per the F.4 extraction in PR #N). ``src/tui/__init__.py`` doesn't
# load the renderers module on import, so no circular hazard.
from ..tool_system.renderers import (
    ToolEvent,
    ToolEventHandler,
    TextChunkHandler,
    summarize_tool_use,
    summarize_tool_result,
)


def _is_tool_result_block(block: Any) -> bool:
    """True for a tool_result block in EITHER shape it can arrive in.

    ``run_tool_use`` (services/tool_execution/tool_execution.py) emits
    tool_result blocks in TWO forms: the normal-success path and the
    user-cancel REJECT path build typed ``ToolResultBlock`` instances,
    but the permission-denied / generic-abort / tool-error paths build
    RAW DICTS (``{"type": "tool_result", ...}``). The adapter must
    persist and surface BOTH — an earlier ``isinstance(block,
    ToolResultBlock)``-only check silently dropped the dict form, which
    left the assistant's ``tool_use`` with no matching ``tool_result``
    in the conversation. The NEXT API call then 400s with "tool_use ids
    were found without tool_result blocks immediately after" — exactly
    the failure seen when ESC-rejecting a tool (e.g. a permission
    prompt) and then resuming with "please continue".
    """
    if isinstance(block, ToolResultBlock):
        return True
    return isinstance(block, dict) and block.get("type") == "tool_result"


def _tool_result_fields(block: Any) -> tuple[str, Any, bool]:
    """Read (tool_use_id, content, is_error) from either block shape."""
    if isinstance(block, ToolResultBlock):
        return block.tool_use_id, block.content, bool(block.is_error)
    return (
        block.get("tool_use_id", ""),
        block.get("content", ""),
        bool(block.get("is_error")),
    )


def run_query_as_agent_loop_sync(
    conversation: Any,
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    max_turns: int = 20,
    stream: bool = True,  # kept for signature compat; adapter always streams
    verbose: bool = False,  # kept for signature compat; ignored
    on_event: ToolEventHandler | None = None,
    on_text_chunk: TextChunkHandler | None = None,
    on_thinking_chunk: TextChunkHandler | None = None,
    cancel_signal: AbortSignal | None = None,
) -> "AgentLoopResult":
    """Sync wrapper around :func:`run_query_as_agent_loop` with the
    signature of the legacy ``run_agent_loop``.

    The async adapter is the canonical API; this wrapper exists so
    sync call sites (mainly tests inherited from the pre-cutover
    era) don't need to thread their own asyncio.run + initial_messages
    + on_message persistence boilerplate at every call site. The
    semantics match legacy ``run_agent_loop``:

    * Pre-built effective system prompt (CLAUDE.md + style + git status).
    * In-place conversation mutation (legacy contract — multi-prompt
      sessions need this so subsequent turns see prior history).
    * Returns a legacy ``AgentLoopResult`` shape.

    Use the async adapter (:func:`run_query_as_agent_loop`) directly
    for new code that owns its event loop.
    """
    import asyncio as _asyncio
    from ..outputStyles import resolve_output_style
    from ..tool_system.renderers import AgentLoopResult

    style_prompt = resolve_output_style(
        getattr(tool_context, "output_style_name", None),
        getattr(tool_context, "output_style_dir", None),
    ).prompt
    effective_system_prompt = build_effective_system_prompt(
        style_prompt, tool_context, provider=provider,
    )

    def _persist(msg: Any) -> None:
        # Mirror the legacy contract: append assistant text + tool
        # result blocks to the conversation in place so the next call
        # in a multi-turn test sequence sees prior history.
        # Stage 4 critic S3: match the production policy (log + raise
        # on failure). Swallowing here would mask a corrupted
        # conversation; the next API call would 400 with
        # ``tool_use IDs must match tool_result IDs`` and the
        # proximate cause would be invisible.
        try:
            conversation.add_message(msg.role, msg.content)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to persist message into conversation: role=%s",
                getattr(msg, "role", "?"),
            )
            raise

    compat_result = _asyncio.run(run_query_as_agent_loop(
        initial_messages=list(conversation.messages),
        provider=provider,
        tool_registry=tool_registry,
        tool_context=tool_context,
        system_prompt=effective_system_prompt,
        max_turns=max_turns,
        on_event=on_event,
        on_text_chunk=on_text_chunk,
        on_thinking_chunk=on_thinking_chunk,
        on_message=_persist,
        cancel_signal=cancel_signal,
    ))
    return AgentLoopResult(
        response_text=compat_result.response_text,
        usage=(
            compat_result.usage
            if compat_result.num_turns > 0
            else None
        ),
        num_turns=compat_result.num_turns,
    )


def build_effective_system_prompt(
    style_prompt: str,
    tool_context: ToolContext,
    *,
    provider: Any | None = None,
    mcp_servers: list[Any] | None = None,
    query_source: str = "main",
) -> list[dict[str, Any]]:
    """Assemble the cold-start system prompt for the headless+TUI cutover.

    Returns the FULL system prompt as a **block list** (``list[dict]``) so the
    ``query()`` loop still engages prompt caching: the canonical base sections
    (``build_full_system_prompt_blocks`` — intro / # Doing tasks / # Executing
    actions / # Using your tools / # Tone / output-efficiency / env / memory /
    skills / …) with the resolved output-style prompt **appended**, then the
    existing workspace + git + CLAUDE.md context preserved as a trailing
    (uncached) block.

    Why this exists: the TUI (``tui/agent_bridge.py``) and headless
    (``entrypoints/headless.py``) cutover routes through ``query()``, which
    passes ``params.system_prompt`` **verbatim** to the model — it has no base
    build of its own. The engine/REPL path (``engine.py:124-188``) does the
    canonical build when ``system_prompt`` is unset, but the cutover pre-sets
    it, so before this fix the live TUI/headless agent received **no base
    instructions** at all (only the style line + context). This helper restores
    them, mirroring the engine's ``build_full_system_prompt_blocks`` +
    ``append_system_prompt`` shape.

    CLAUDE.md note: ``build_full_system_prompt_blocks``' memory section is
    *auto-memory* (``MEMORY.md`` via ``load_memory_prompt``), **not** CLAUDE.md.
    On the engine path CLAUDE.md is injected into the *messages* via
    ``prepend_user_context``; the cutover does not do that, so we keep
    ``build_context_prompt`` (which emits ``## Project Instructions``) to
    preserve CLAUDE.md — option (b) in
    ``my-docs/get-parity-by-folder/live-base-system-prompt-gap-analysis.md``.
    This overlaps the base ``# Environment`` section on CWD/date (a benign,
    documented duplication).

    ``tools``/``tool_registry`` are deliberately NOT passed to
    ``build_full_system_prompt_blocks`` (matching ``engine.py:167``): tool
    schemas reach the model via the API ``tools=`` param, so emitting a prose
    tool-docs section here would double-send them. ``provider`` feeds the
    global cache-scope gate; ``mcp_servers`` ALSO feeds the REQUEST-scoped
    ``_build_mcp_instructions_section`` (C2 — server-authored instructions),
    so it is no longer inert here; ``None`` is safe (no instructions section
    + disables the cross-user global scope, which TUI/headless should not use).

    Coordinator mode (``CLAUDE_CODE_COORDINATOR_MODE`` truthy): the
    coordinator orchestration prompt REPLACES the base blocks entirely,
    ``style_prompt`` still appends, and the trailing context block is kept
    and extended with the ``workerToolsContext`` entry — mirrors
    ``utils/systemPrompt.ts:63-75`` + ``QueryEngine.ts:300-306``. This
    builder only ever serves the MAIN loop (subagents build their prompts
    via ``get_agent_system_prompt``), so the branch cannot leak into worker
    prompts — the structural equivalent of TS's
    ``!mainThreadAgentDefinition`` guard.
    """
    # Local imports — context_system is a heavier dep; only the cutover
    # callers need it, no need to drag it into agent_loop_compat's import time.
    from ..context_system import build_context_prompt
    from ..context_system.prompt_assembly import build_full_system_prompt_blocks
    from ..context_system.system_prompt_cache import CacheScope
    from ..coordinator.mode import is_coordinator_mode

    cwd = str(tool_context.cwd or tool_context.workspace_root)

    coordinator = is_coordinator_mode()
    if coordinator:
        # Coordinator mode: the orchestration prompt REPLACES the entire base
        # block set — no # Doing tasks, no tool guidance, no tone (mirrors
        # ``utils/systemPrompt.ts:63-75``, where the coordinator prompt swaps
        # in for defaultSystemPrompt while appendSystemPrompt is preserved).
        # ``style_prompt`` is this builder's append-channel, so it survives;
        # the trailing workspace/git/CLAUDE.md context block below is also
        # kept — TS coordinator sessions keep userContext/systemContext
        # (``QueryEngine.ts:300-306`` replaces only the default prompt).
        from ..coordinator import get_coordinator_system_prompt
        from ..state.cache_state import should_1h_cache_ttl

        blocks: list[dict[str, Any]] = [{
            "type": "text",
            "text": get_coordinator_system_prompt(),
            "_cache_scope": CacheScope.SESSION.value,
        }]
        if style_prompt:
            blocks.append({
                "type": "text",
                "text": style_prompt,
                "_cache_scope": CacheScope.SESSION.value,
            })
        # One cache marker on the LAST stable block — the same convention
        # build_full_system_prompt_blocks applies to each scope-group's
        # final block (prompt_assembly.py:699-761), reusing its TTL selector.
        blocks[-1]["cache_control"] = {
            "type": "ephemeral",
            "ttl": "1h" if should_1h_cache_ttl(query_source) else "5m",
        }
    else:
        # Skills listing (best-effort; mirrors engine.py:183).
        try:
            from ..command_system import get_skill_tool_commands
            skills = get_skill_tool_commands(cwd)
        except Exception:
            skills = None

        blocks = build_full_system_prompt_blocks(
            cwd=cwd,
            output_style="default",          # style is appended below (mirror engine.py:169)
            append_system_prompt=style_prompt,
            query_source=query_source,
            provider=provider,
            mcp_servers=mcp_servers,
            skills=skills,
        )

    # Preserve the existing workspace + git + CLAUDE.md context verbatim as a
    # trailing uncached block (CLAUDE.md is NOT in the base blocks above).
    #
    # Tag it REQUEST-scope. This block is a *live workspace snapshot*: it embeds
    # ``git status`` (and file counts / top-level entries) that mutate the moment
    # the agent edits a file — which, for a coding agent, is essentially every
    # turn. For DeepSeek, ``query._split_system_prompt_blocks`` relocates
    # REQUEST-scope sections out of the byte-stable ``system + tools + history``
    # prefix into the trailing tail; without the tag this snapshot would sit in
    # the prefix and a single mid-session file edit would bust DeepSeek's
    # automatic prefix cache for the entire prefix. The tag honours the block's
    # already-intended "uncached" status while keeping the prefix stable. It is a
    # strict no-op for every other provider: relocation only fires for DeepSeek,
    # and the Anthropic path strips ``_cache_scope`` before the wire.
    try:
        context_prompt = build_context_prompt(
            tool_context.workspace_root,
            cwd=tool_context.cwd,
        )
    except Exception:
        context_prompt = ""

    if coordinator:
        # workerToolsContext — TS merges this into the per-session userContext
        # (``QueryEngine.ts:300-306``); this port's userContext channel on the
        # live path is the trailing context block (same route CLAUDE.md / git
        # status already take), rendered with the ``# {key}\n{value}`` entry
        # idiom of prepend_user_context (prompt_assembly.py:255-256). MCP
        # server names come from the ToolContext's connected-client catalog
        # (agent_server.py publishes ``tool_context.mcp_clients``); the
        # scratchpad line is surfaced whenever the dir resolves — TS gates it
        # on Statsig ``tengu_scratch``, which this port does not have (the
        # module-documented divergence).
        from ..coordinator.mode import get_coordinator_user_context

        try:
            from ..permissions.filesystem import get_scratchpad_dir
            scratchpad_dir: str | None = get_scratchpad_dir()
        except Exception:
            scratchpad_dir = None
        worker_ctx = get_coordinator_user_context(
            getattr(tool_context, "mcp_clients", None),
            scratchpad_dir=scratchpad_dir,
        ).get("workerToolsContext", "")
        if worker_ctx:
            entry = f"# workerToolsContext\n{worker_ctx}"
            context_prompt = (
                f"{context_prompt}\n\n{entry}" if context_prompt.strip() else entry
            )

    if context_prompt.strip():
        blocks = blocks + [{
            "type": "text",
            "text": context_prompt,
            "_cache_scope": CacheScope.REQUEST.value,
        }]

    return blocks


@dataclass(frozen=True)
class AgentLoopRunResult:
    """Adapter result shape. Mirrors the
    :class:`src.tool_system.renderers.AgentLoopResult` shape for
    callers that previously consumed ``run_agent_loop``, AND adds a
    typed ``terminal`` so wrappers can discriminate exit reason.
    """
    response_text: str
    usage: dict[str, int]
    num_turns: int
    terminal: Terminal | None = None


def _last_user_text(messages: list[Message]) -> str:
    """The current user turn's text — the query for memory recall."""
    for msg in reversed(messages):
        if getattr(msg, "role", None) != "user":
            continue
        if getattr(msg, "isMeta", False):
            continue  # skip injected system-reminders
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(b.get("text", "")) for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if parts:
                return "\n".join(parts)
    return ""


async def _maybe_recall_memories(
    messages: list[Message],
    provider: Any,
    tool_context: ToolContext,
    memory_surfaced: set[str] | None,
) -> Message | None:
    """ch11 round-4 WI-1 — the gated memory-relevance recall. Returns a
    <system-reminder> UserMessage to APPEND after the user turn (not prepend
    — TS surfaces memory after the user message, and normalize_messages
    merges consecutive user messages so there is no 400), or None. Never
    raises."""
    try:
        from src.settings.settings import get_settings

        if not getattr(get_settings(), "memory_relevance_prefetch_enabled", False):
            return None
    except Exception:  # noqa: BLE001
        return None

    query_text = _last_user_text(messages)
    if not query_text.strip():
        return None
    # R5 (ch11 N1) — unwrap /effort's _EffortProvider so the recall SELECTOR
    # runs on the raw provider: (a) _resolve_recall_model's
    # isinstance(AnthropicProvider) check sees through the wrapper → the
    # small_fast_model cost pin applies in effort mode too (it was bypassed —
    # a bare wrapper class isn't an AnthropicProvider); (b) the wrapper's
    # reasoning_effort injection doesn't leak into the cheap selector call.
    # Safe: _inner is _EffortProvider-exclusive, so this is a no-op otherwise.
    provider = getattr(provider, "_inner", provider)
    try:
        from src.memdir import get_auto_mem_path
        from src.memdir.surface_memories import get_relevant_memory_reminder
        from src.types.messages import create_user_message

        memdir = str(get_auto_mem_path())
        surfaced = memory_surfaced if memory_surfaced is not None else set()
        reminder = await get_relevant_memory_reminder(
            query_text, memdir, provider=provider, already_surfaced=surfaced,
        )
        if not reminder:
            return None
        return create_user_message(content=reminder, isMeta=True)
    except Exception:  # noqa: BLE001 — recall must never block a turn
        return None


async def run_query_as_agent_loop(
    *,
    initial_messages: list[Message],
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
    system_prompt: str | list[dict[str, Any]] = "You are a helpful assistant.",
    max_turns: int = 20,
    on_event: ToolEventHandler | None = None,
    on_text_chunk: TextChunkHandler | None = None,
    on_thinking_chunk: TextChunkHandler | None = None,
    on_message: Callable[[Message], None] | None = None,
    cancel_signal: AbortSignal | None = None,
    abort_controller: AbortController | None = None,
    extended_thinking: bool | None = None,
    fallback_model: str | None = None,
    # ch11 round-4 WI-1 — session-scoped set of already-surfaced memory
    # file paths (de-dups the LLM recall across turns). The agent-server
    # passes its per-session set; headless a per-run set. None disables
    # de-dup (a fresh set is used per call).
    memory_surfaced: set[str] | None = None,
    # ch11 round-4 WI-1 (critic #8) — set False to skip the recall entirely
    # (internal/notification turns). The settings gate still applies on top.
    memory_recall_enabled: bool = True,
    # ch05 round-4 GAP A — the production compaction pipeline. When None
    # (the pre-round-4 default) Phase-0 (tool-result budget/snip/
    # microcompact/collapse/auto-compact) is inert and only the blocking
    # guards + reactive recovery run. Callers build it via
    # services.compact.pipeline.build_production_pipeline_config with a
    # SESSION-scoped AutoCompactTracking.
    pipeline_config: Any | None = None,
    # TS querySource per surface: 'repl_main_thread' (interactive turns),
    # 'sdk' (headless/print), 'agent:*' (subagents — ch08).
    query_source: str = "repl_main_thread",
    # ch05 round-4 GAP B — the '+500k' auto-continue budget parsed from
    # the user's prompt (query/token_budget.parse_token_budget).
    token_budget: int | None = None,
    # Plan-mode port — persistence hook for injected conversation
    # attachments (plan_mode / plan_mode_exit system reminders). Unlike
    # ``on_message`` (which persists AND emits an SDK envelope — rendering
    # the attachment as user text in the TUI), this callback must persist
    # ONLY (agent-server: session.conversation.add_message; headless: its
    # conversation). None → attachments are injected ephemerally into this
    # turn's working set (degraded: the cadence scan can't see them next
    # turn, so the full reminder repeats — both live surfaces wire this).
    on_attachment: Callable[[Message], None] | None = None,
) -> AgentLoopRunResult:
    """Drive the canonical query() loop and adapt to AgentLoopResult.

    Parameters mirror the existing ``run_agent_loop`` signature where
    practical, but the adapter takes ``initial_messages`` directly
    instead of a ``Conversation`` — the typed messages are the same
    shape query() consumes natively.

    The ``on_event`` callback receives :class:`ToolEvent` instances
    for every tool_use observed in the model's responses and every
    tool_result yielded by the loop.

    ``on_text_chunk`` is forwarded into the QueryParams so the
    provider's streaming layer fires text chunks LIVE (per-delta).
    Callers MUST provide this if they need real-time text rendering
    (TUI live streaming, ESC-mid-stream cancel teardown).

    ``on_message`` is fired for EVERY :class:`Message` yielded by the
    loop (Anthropic-shape AssistantMessage with full content blocks
    including tool_use, UserMessage with tool_result blocks, etc.).
    Use this to persist the full conversation transcript faithfully —
    `response_text` alone loses tool_use/tool_result structure across
    multi-turn sessions.

    ``cancel_signal`` is bridged into the loop's abort_controller so
    user-initiated cancels (Ctrl+C, /exit) propagate cleanly. When
    not supplied, the function constructs its own AbortController.
    """
    # Critic C2 fix: do NOT mint a fresh controller when the caller
    # provided one. The provider's chat_stream_response listens on
    # ``QueryParams.abort_controller.signal`` to tear down HTTP streams
    # mid-flight on ESC. A fresh controller breaks that wiring — ESC
    # would flip the user's signal but the provider would never see it
    # because the per-message bridge below only fires when query()
    # yields a message, and a tool-use-only turn yields nothing during
    # the multi-second generation. Caller's controller IS the user's
    # signal source; reuse it.
    if abort_controller is None:
        if cancel_signal is not None:
            # We received only the signal, not its owning controller.
            # Mint a new controller and bridge cancellation into it
            # both pre- and per-iteration (legacy fallback path).
            abort_controller = AbortController()
            if cancel_signal.aborted:
                abort_controller.abort(
                    cancel_signal.reason or "user_interrupt"
                )
        else:
            abort_controller = AbortController()

    # ch11 round-4 WI-1 — LLM memory-relevance recall (gated, default off).
    # Fire once at turn start on the current user message: select up to 5
    # relevant memory files, read their bodies, and APPEND them as a
    # <system-reminder> AFTER the user turn (TS also surfaces memory after
    # the user message, never before — query.ts:1810-1831; do NOT move this
    # before the user turn). The model then recalls the RIGHT memory content
    # (not just the MEMORY.md index pointer). Never blocks the turn on
    # failure. Ephemeral: appended to the query's working set only, never
    # persisted to the conversation (matches TS attachments).
    messages_for_query = list(initial_messages)
    try:
        recall_msg = (
            await _maybe_recall_memories(
                messages_for_query, provider, tool_context, memory_surfaced,
            )
            if memory_recall_enabled else None
        )
        if recall_msg is not None:
            messages_for_query.append(recall_msg)
    except Exception:  # noqa: BLE001 — recall must never block a turn
        logging.getLogger(__name__).debug("memory recall wiring failed",
                                          exc_info=True)

    # R5 round-5 (ch17) — date-change (midnight-rollover) companion to the
    # memoized env date. On a real turn where the date has rolled over since
    # the last, append a <system-reminder> with today's date at the tail (the
    # cached prefix keeps the memoized start date, so this doesn't bust it).
    # Reuses the "real user turn" gate (memory_recall_enabled == not internal).
    if memory_recall_enabled:
        try:
            from src.context_system.date_change import get_date_change_reminder
            from src.types.messages import create_user_message

            dc = get_date_change_reminder()
            if dc:
                messages_for_query.append(
                    create_user_message(content=dc, isMeta=True)
                )
        except Exception:  # noqa: BLE001 — must never block a turn
            logging.getLogger(__name__).debug("date-change wiring failed",
                                              exc_info=True)

    # Plan-mode attachments (port of getPlanModeAttachments +
    # getPlanModeExitAttachment, typescript/src/utils/attachments.ts:882-883,
    # 1187-1274). Same real-user-turn gate as the recall/date-change blocks
    # (internal __goal__/notification turns must not consume the one-shot
    # flags or burn the cadence). Unlike those EPHEMERAL reminders, plan
    # attachments are PERSISTED via on_attachment — TS keeps them in the
    # transcript, and both the throttle scan and the model's context across
    # turns 2..5 depend on them surviving into later turns' initial_messages.
    if memory_recall_enabled:
        try:
            from src.context_system.plan_mode import (
                build_plan_mode_attachments,
                build_plan_mode_exit_attachment,
                wrap_in_system_reminder,
            )
            from src.types.messages import create_user_message

            pc = getattr(tool_context, "permission_context", None)
            mode = str(getattr(pc, "mode", "default")) if pc is not None else "default"
            agent_id = getattr(tool_context, "agent_id", None)
            texts = build_plan_mode_attachments(
                messages_for_query, mode, agent_id=agent_id,
            )
            texts += build_plan_mode_exit_attachment(mode, agent_id=agent_id)
            for text in texts:
                attachment_msg = create_user_message(
                    content=wrap_in_system_reminder(text), isMeta=True,
                )
                messages_for_query.append(attachment_msg)
                if on_attachment is not None:
                    on_attachment(attachment_msg)
        except Exception:  # noqa: BLE001 — must never block a turn
            logging.getLogger(__name__).debug("plan-mode attachment wiring failed",
                                              exc_info=True)

    params = QueryParams(
        messages=messages_for_query,
        system_prompt=system_prompt,
        tools=tool_registry.list_tools(),
        tool_registry=tool_registry,
        tool_use_context=tool_context,
        provider=provider,
        abort_controller=abort_controller,
        max_turns=max_turns,
        # ch04 round-4 GAP B — capacity-relief model switch after repeated
        # 529s (see QueryParams.fallback_model).
        fallback_model=fallback_model,
        # ch05 round-4 GAP A/B — production pipeline + surface label +
        # +500k budget.
        pipeline_config=pipeline_config,
        query_source=query_source,
        token_budget=token_budget,
        # C3b /thinking: None = auto (model-gated default), True/False =
        # explicit session override (TS ThinkingToggle semantics).
        extended_thinking=extended_thinking,
        # Critic-flagged: forward on_text_chunk into QueryParams so
        # the provider's chat_stream_response fires chunks LIVE. The
        # adapter must NOT call on_text_chunk(full_text) once at the
        # end — that breaks TUI live streaming AND the
        # ESC-mid-stream-cancel path which relies on the chunk
        # callback raising AbortError from inside the SDK stream.
        on_text_chunk=on_text_chunk,
        on_thinking_chunk=on_thinking_chunk,
    )

    holder = TerminalHolder()
    response_text_parts: list[str] = []
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    num_turns = 0
    last_assistant_text = ""
    last_api_error_text = ""

    async for msg in query(params, terminal_holder=holder):
        if isinstance(msg, StreamEvent):
            continue

        # Bridge cancel_signal: if it fires mid-stream, propagate to
        # the loop's abort_controller. The loop checks signal.aborted
        # at iteration boundaries so the next check will exit.
        if cancel_signal is not None and cancel_signal.aborted:
            if not abort_controller.signal.aborted:
                abort_controller.abort(
                    cancel_signal.reason or "user_interrupt"
                )

        if isinstance(msg, AssistantMessage):
            # Critic S1 fix: skip persisting API-error messages and
            # meta messages. The legacy run_agent_loop never added
            # these to the user's Conversation — letting them through
            # poisons multi-prompt sessions (the model sees prior
            # error text as its own past output, and PTL errors
            # persisted as assistant messages fertilize a PTL death
            # spiral on the next turn).
            _is_api_error = bool(getattr(msg, "isApiErrorMessage", False))
            _is_meta = bool(getattr(msg, "isMeta", False))
            if on_message is not None and not _is_api_error and not _is_meta:
                on_message(msg)
            if _is_api_error:
                # Don't count error turns or surface their text as the
                # "response" — those are exit signals, not output. The
                # text is still captured locally so graceful guard stops
                # (tool_failure_loop) can surface it as response_text
                # below — without persisting it via on_message (the S1
                # no-poisoning contract stands). Content may be a plain
                # str or a TextBlock list depending on the producer.
                _err_content = msg.content
                if isinstance(_err_content, str):
                    last_api_error_text = _err_content.strip()
                elif isinstance(_err_content, list):
                    _err_parts = [
                        block.text
                        for block in _err_content
                        if isinstance(block, TextBlock)
                    ]
                    if _err_parts:
                        last_api_error_text = " ".join(_err_parts).strip()
                continue
            num_turns += 1
            # Sum usage across turns.
            mu = getattr(msg, "usage", None) or {}
            usage["input_tokens"] += mu.get("input_tokens", 0)
            usage["output_tokens"] += mu.get("output_tokens", 0)
            # C3a: also keep the LAST response's FULL usage (all four
            # keys, last-wins — TS getCurrentUsage, utils/tokens.ts:
            # 152-171). The cumulative sum above double-counts context
            # across a multi-tool-call run and drops the cache keys, so
            # it must NOT be used as a live-context measure
            # (tokens.ts:407-420 warning). Last-wins also covers the
            # parallel-tool-call case where N assistant records share
            # one usage object.
            if mu:
                usage["last_input_tokens"] = int(mu.get("input_tokens", 0) or 0)
                usage["last_output_tokens"] = int(mu.get("output_tokens", 0) or 0)
                usage["last_cache_read_input_tokens"] = int(
                    mu.get("cache_read_input_tokens", 0) or 0
                )
                usage["last_cache_creation_input_tokens"] = int(
                    mu.get("cache_creation_input_tokens", 0) or 0
                )
            # Capture text content and tool_use events.
            text_parts: list[str] = []
            content = msg.content
            if isinstance(content, str):
                text_parts.append(content)
            else:
                for block in content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock) and on_event is not None:
                        on_event(ToolEvent(
                            kind="tool_use",
                            tool_name=block.name,
                            tool_input=block.input,
                            tool_use_id=block.id,
                        ))
            if text_parts:
                last_assistant_text = " ".join(text_parts).strip()
                # NB: do NOT fire on_text_chunk here. It was already
                # fired LIVE by the provider's chat_stream_response
                # via QueryParams.on_text_chunk threading. Firing
                # again would duplicate the entire response into the
                # caller's stream.
            continue

        if isinstance(msg, UserMessage):
            # Tool result(s) arrive as UserMessages with ToolResultBlock
            # content. Persist the full message (so the next turn's
            # API call can pair tool_use IDs to their results) AND
            # dispatch tool_result events. Skip meta (interruption /
            # cancellation synthesized by query.py) — those are loop
            # bookkeeping, not real user turns.
            if bool(getattr(msg, "isMeta", False)):
                continue
            content = msg.content
            if isinstance(content, list):
                # Accept tool_result blocks in BOTH typed and raw-dict
                # form (see _is_tool_result_block). The denial/abort/error
                # paths in run_tool_use emit dicts; dropping them here
                # orphans the matching tool_use and 400s the next turn.
                has_tool_result = any(
                    _is_tool_result_block(block) for block in content
                )
                if has_tool_result and on_message is not None:
                    on_message(msg)
                if on_event is not None:
                    for block in content:
                        if _is_tool_result_block(block):
                            tool_use_id, tool_output, is_error = (
                                _tool_result_fields(block)
                            )
                            on_event(ToolEvent(
                                kind="tool_result",
                                tool_name="",
                                tool_use_id=tool_use_id,
                                tool_output=tool_output,
                                is_error=is_error,
                                error=str(tool_output) if is_error else None,
                            ))

    # Critic C1 fix: surface terminal abort/error reasons as exceptions
    # so callers' existing ``except AbortError`` / ``except Exception``
    # paths fire. Without this, ESC during the loop sets a Terminal but
    # the adapter returns normally — headless reports exit 0 instead of
    # 130 with subtype:cancelled, TUI shows a blank response instead of
    # "Cancelled by user". Mirrors the AbortError raise legacy
    # run_agent_loop did at agent_loop.py:345-347.
    terminal = holder.value
    if terminal is not None:
        reason = getattr(terminal, "reason", None) or ""
        if reason in ("aborted_streaming", "aborted_tools", "interrupted"):
            raise AbortError(
                getattr(abort_controller.signal, "reason", None)
                or reason
                or "user_interrupt"
            )
        # Same pattern for upstream model failures (connection errors, 4xx,
        # 5xx, prompt_too_long, etc.). ``query.py`` catches the SDK
        # exception, yields ``_create_assistant_api_error_message`` into
        # the message stream (so the TUI can render "API error: ..." in
        # the transcript), and sets ``Terminal(reason="model_error",
        # error=<original_exception>)``. Without re-raising here the
        # adapter returns ``response_text=""`` with a happy
        # ``AgentLoopRunResult`` and headless ships ``is_error: false,
        # num_turns: 0, result: ""`` — a silent success that downstream
        # eval scripts (SWE-bench, batch runners) cannot distinguish
        # from a legitimately empty completion. Re-raising the original
        # exception routes it into headless's ``except Exception``
        # branch which sets ``exit_code=1`` and emits subtype:error /
        # is_error:true with the error string as ``result``.
        if reason == "model_error":
            original_error = getattr(terminal, "error", None)
            if isinstance(original_error, BaseException):
                raise original_error
            raise RuntimeError(str(original_error) if original_error else "model_error")

    # When the loop exited because of max_turns, surface the legacy
    # ``[Max tool turns reached]`` sentinel as response_text so callers
    # (CLI accounting / TUI display) match the historical contract.
    # Tests pin this — see test_max_turns_respected.
    if terminal is not None and getattr(terminal, "reason", None) == "max_turns":
        response_text = "[Max tool turns reached]"
    elif (
        terminal is not None
        and getattr(terminal, "reason", None) == "tool_failure_loop"
    ):
        # Graceful guard stop: the loop yielded the trip explanation as an
        # isApiErrorMessage assistant message, which the S1 filter above
        # deliberately keeps out of on_message/last_assistant_text. Surface
        # it as response_text (same shape as the max_turns sentinel) so the
        # TUI/headless caller shows WHY the run stopped instead of a silent
        # empty success.
        response_text = (
            last_api_error_text or "[Stopped: repeated tool failures detected]"
        )
    else:
        response_text = last_assistant_text or " ".join(response_text_parts).strip()

    # SendUserMessage fallback (Stage 4 critic S2): if the final turn
    # ended with empty assistant text BUT the model used
    # ``SendUserMessage`` as its visible output (the tool's prompt
    # advertises itself as the "primary visible output channel"), pull
    # the last SendUserMessage's content into ``response_text``. Legacy
    # ``agent_loop`` tracked this as ``last_user_visible_message``;
    # without preserving the fallback here, an agent that obeys the
    # SendUserMessage prompt's primary-channel guidance silently
    # surfaces "" as its visible output.
    if not response_text:
        outbox = getattr(tool_context, "outbox", None) or []
        for entry in reversed(outbox):
            if (
                isinstance(entry, dict)
                and entry.get("tool") == "SendUserMessage"
                and isinstance(entry.get("message"), str)
                and entry["message"]
            ):
                response_text = entry["message"]
                break
    return AgentLoopRunResult(
        response_text=response_text,
        usage=usage,
        num_turns=num_turns,
        terminal=holder.value,
    )
