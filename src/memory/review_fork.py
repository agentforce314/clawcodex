"""The background-review fork runner — an agent with its hands tied.

Port of the fork half of
``reference_projects/hermes-agent/agent/background_review.py``
(``_run_review_in_thread``), adapted to clawcodex's query loop. Runs on
a daemon thread the agent-server worker spawns after a completed turn;
never on the turn path.

Cache-parity engineering (the donor's ~26% measured saving, issue #25322):

* ``system_prompt`` is the parent session's **already-built** prompt,
  passed verbatim — byte-identical to every foreground request;
* ``tool_registry`` is the parent's registry unchanged, so the request's
  ``tools[]`` array is byte-identical (``query()`` derives it from
  ``registry.list_tools()``); the whitelist is enforced at the
  *permission* layer instead: the fork's ToolContext carries deny rules
  for every tool except ``Memory``, resolved through the production
  ``can_use_tool`` lane;
* the replayed ``initial_messages`` are the parent's live conversation
  snapshot, so the request prefix (system + tools + history) matches the
  parent's warm cache entry exactly — only the appended review prompt is
  new tokens.

Containment (donor invariants):

* fresh ToolContext + fresh AbortController — the parent session's
  conversation, todos, stats, and SDK stream are never touched;
* no permission handler → any tool that would *ask* fails closed to deny
  (the donor's auto-deny approval callback);
* no compaction pipeline (``pipeline_config=None``) and no memory recall;
* ``max_turns=16``; provenance ContextVar bound to ``background_review``
  for the whole fork so staged writes carry the right origin;
* every failure is swallowed and logged — the fork returns ``None`` and
  the user's session never notices.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .provenance import (
    BACKGROUND_REVIEW,
    reset_current_write_origin,
    set_current_write_origin,
)
from .review import (
    MEMORY_REVIEW_PROMPT,
    REVIEW_MAX_TURNS,
    REVIEW_TOOL_NAME,
    REVIEW_TOOL_WARNING,
    collect_tool_use_ids,
    count_staged_actions,
    format_review_summary,
    format_staged_notice,
    summarize_review_actions,
)

logger = logging.getLogger(__name__)

#: Wall-clock bound on one review pass. ``REVIEW_MAX_TURNS`` bounds
#: iterations, not hangs — a wedged provider stream would otherwise pin
#: the daemon thread (and the one-fork-at-a-time guard) forever. The
#: donor has no wall-clock bound (its forks die with the CLI process);
#: the agent-server is long-lived, so this port adds one. On expiry the
#: fork's own AbortController fires and the pass ends summary-less.
#: Caveat (round-2 critic): this bounds the abort *request*, not the
#: thread — the loop honors the signal between chunks/iterations, so a
#: provider stream wedged with no traffic and no socket timeout can keep
#: the daemon thread alive (and reviews paused for the session) until
#: the transport gives up. No user-session impact; self-heals on the
#: next process start.
REVIEW_TIMEOUT_S = 600.0

#: Last completed review's stats, surfaced by ``/memory status`` so the
#: fork's token spend is not invisible (it never enters the session /cost
#: odometer — the fork must not touch session accounting).
_last_review_stats: dict[str, Any] | None = None


def get_last_review_stats() -> dict[str, Any] | None:
    """Stats of the most recent review pass in this process, or None."""
    return _last_review_stats


def _build_fork_tool_context(
    parent_context: Any, tool_registry: Any
) -> Any:
    """A fresh ToolContext for the fork: parent's roots, nobody's state.

    The permission context denies every registered tool except ``Memory``
    (``always_deny_rules``, resolved by the production permission lane) —
    the clawcodex analog of the donor's thread tool whitelist. ``Memory``
    itself auto-allows via NO_PERMISSION_TOOLS. Anything that would
    *ask* hits the no-handler fail-closed deny in
    ``services/tool_execution/can_use_tool_adapter``.
    """
    from src.permissions.types import ToolPermissionContext
    from src.tool_system.context import ToolContext
    from src.utils.abort_controller import AbortController

    deny_names = sorted(
        {
            t.name
            for t in tool_registry.list_tools()
            if t.name != REVIEW_TOOL_NAME
        }
    )
    permission_context = ToolPermissionContext.from_iterables(deny_names=deny_names)

    workspace_root = getattr(parent_context, "workspace_root", None) or Path.cwd()
    context = ToolContext(
        workspace_root=workspace_root,
        permission_context=permission_context,
        cwd=getattr(parent_context, "cwd", None),
        abort_controller=AbortController(),
    )
    context.options.is_non_interactive_session = True
    return context


def run_memory_review(
    *,
    provider: Any,
    tool_registry: Any,
    parent_tool_context: Any,
    system_prompt: Any,
    conversation_snapshot: list[Any],
    notification_mode: str = "on",
    query_source: str = "repl_main_thread",
) -> str | None:
    """Run one memory review pass to completion (blocking — call on the
    dedicated daemon thread). Returns the ``💾 Self-improvement review: …``
    summary line when the fork committed writes (mode-gated), else None.

    Never raises.
    """
    origin_token = set_current_write_origin(BACKGROUND_REVIEW)
    try:
        from src.query.agent_loop_compat import run_query_as_agent_loop

        prior_ids = collect_tool_use_ids(conversation_snapshot)

        from src.types.messages import create_user_message

        review_prompt = MEMORY_REVIEW_PROMPT + REVIEW_TOOL_WARNING
        initial_messages = list(conversation_snapshot) + [
            create_user_message(review_prompt)
        ]

        fork_context = _build_fork_tool_context(parent_tool_context, tool_registry)

        review_messages: list[Any] = []

        def _collect(message: Any) -> None:
            review_messages.append(message)

        # Wall-clock watchdog: abort the fork's own controller on expiry.
        # Daemon Timer — dies with the process; cancelled on normal exit.
        watchdog = threading.Timer(
            REVIEW_TIMEOUT_S,
            lambda: fork_context.abort_controller.abort("memory review timeout"),
        )
        watchdog.daemon = True
        watchdog.start()
        started = time.monotonic()
        try:
            result = asyncio.run(
                run_query_as_agent_loop(
                    initial_messages=initial_messages,
                    provider=provider,
                    tool_registry=tool_registry,
                    tool_context=fork_context,
                    system_prompt=system_prompt,
                    max_turns=REVIEW_MAX_TURNS,
                    on_message=_collect,
                    abort_controller=fork_context.abort_controller,
                    # No compaction: the fork is single-lifecycle and needs the
                    # full context to review; the parent owns context management
                    # (donor: compression_enabled=False, issue #38727).
                    pipeline_config=None,
                    # No LLM memory recall side-query inside the fork.
                    memory_recall_enabled=False,
                    query_source=query_source,
                )
            )
        finally:
            watchdog.cancel()

        global _last_review_stats
        usage = getattr(result, "usage", None) or {}
        _last_review_stats = {
            "at": time.time(),
            "duration_s": round(time.monotonic() - started, 1),
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
        }

        actions = summarize_review_actions(
            review_messages, prior_ids, notification_mode=notification_mode
        )
        summary = format_review_summary(actions)
        if summary is not None:
            return summary
        if str(notification_mode or "on").lower() == "off":
            return None
        # Gate-on runs commit nothing — surface the accumulating pending
        # records instead of staying silent (design-critic M5).
        return format_staged_notice(
            count_staged_actions(review_messages, prior_ids)
        )
    except Exception:  # noqa: BLE001 — the fork must never surface failures
        logger.warning("Background memory review failed", exc_info=True)
        return None
    finally:
        reset_current_write_origin(origin_token)


__all__ = ["REVIEW_TIMEOUT_S", "get_last_review_stats", "run_memory_review"]
