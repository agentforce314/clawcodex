"""Background self-improvement review — pure logic (prompts + summarizer).

Port of ``reference_projects/hermes-agent/agent/background_review.py``,
memory-channel only (clawcodex has no skill write engine yet; hermes'
skill-review channel lands with one). The agent-server worker owns the
trigger counter and the daemon-thread fork
(``src/server/agent_server.py``); this module owns:

* the review prompt (donor ``_MEMORY_REVIEW_PROMPT``, extended with the
  do-NOT-capture list from the donor's skill prompt — the transferable
  part per ``08-lessons-for-clawcodex.md`` rec 2);
* :func:`summarize_review_actions` — walk the fork's transcript for
  *successful* ``Memory`` tool results and build the one-line
  ``💾 Self-improvement review: …`` summary (donor issue #14944: results
  already present in the inherited snapshot are skipped by tool_use id so
  stale prior-conversation writes aren't re-announced as fresh);
* :func:`hydrate_turns_since_memory` — resume-time counter hydration
  (donor issue #22357: a rebuilt session must not restart the cadence
  from zero).

Messages here are clawcodex-shaped (Anthropic content blocks: assistant
``tool_use`` blocks, user ``tool_result`` blocks), not the donor's
OpenAI-shaped dicts — the walking logic is adapted accordingly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Tool whitelisted for the review fork (denied-at-dispatch for the rest).
REVIEW_TOOL_NAME = "Memory"

#: Fork iteration bound (donor: max_iterations=16).
REVIEW_MAX_TURNS = 16

#: The user message the forked review agent receives after the replayed
#: conversation snapshot. Donor ``_MEMORY_REVIEW_PROMPT`` + the
#: do-NOT-capture list (donor skill prompt's transferable half) + the
#: runtime-whitelist warning appended by the spawner.
MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, desires, "
    "preferences, or personal details worth remembering?\n"
    "2. Has the user expressed expectations about how you should behave, their work "
    "style, or ways they want you to operate?\n"
    "3. Did you learn a stable fact about their environment, conventions, or "
    "workflow that would reduce future steering?\n\n"
    "Do NOT capture (these become persistent self-imposed constraints that bite "
    "later when the environment changes):\n"
    "  • Environment-dependent failures: missing binaries, fresh-install errors, "
    "'command not found', unconfigured credentials, uninstalled packages. The "
    "user can fix these — they are not durable facts.\n"
    "  • Negative claims about tools or features ('X tool is broken', 'cannot "
    "use Y'). These harden into refusals cited against yourself for months "
    "after the actual problem was fixed.\n"
    "  • Session-specific transient errors that resolved before the conversation "
    "ended. If retrying worked, the lesson is the retry pattern, not the "
    "original failure.\n"
    "  • One-off task narratives, task progress, or completed-work logs.\n\n"
    "Write declarative facts, not instructions to yourself "
    "('User prefers concise responses' ✓ — 'Always respond concisely' ✗).\n\n"
    "If something stands out, save it using the Memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

#: Appended to the review prompt so the model doesn't burn iterations on
#: denied tools (donor background_review.py:750-756).
REVIEW_TOOL_WARNING = (
    "\n\nYou can only call the Memory tool. Other tools will be denied at "
    "runtime — do not attempt them."
)

#: Dispatch-denial message for non-whitelisted tools inside the fork
#: (donor set_thread_tool_whitelist deny_msg_fmt).
REVIEW_DENY_MESSAGE = (
    "Background review denied non-whitelisted tool: {tool_name}. "
    "Only the Memory tool is allowed."
)


# ── message-block helpers (clawcodex Anthropic-shaped messages) ───────


def _block_get(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _msg_role(msg: Any) -> str | None:
    if isinstance(msg, dict):
        return msg.get("role")
    return getattr(msg, "role", None)


def _msg_content(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("content")
    return getattr(msg, "content", None)


def _iter_blocks(msg: Any) -> list[Any]:
    content = _msg_content(msg)
    if isinstance(content, list):
        return content
    return []


def _flatten_result_text(content: Any) -> str:
    """tool_result content → text (str, or list of text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            t = _block_get(b, "text")
            if isinstance(t, str) and t:
                parts.append(t)
        return "\n".join(parts)
    return str(content or "")


def collect_tool_use_ids(messages: list[Any]) -> set[str]:
    """All tool_use ids present in ``messages`` (the pre-fork snapshot) —
    used to skip inherited results in the summary (donor issue #14944)."""
    ids: set[str] = set()
    for msg in messages or []:
        for block in _iter_blocks(msg):
            btype = _block_get(block, "type")
            if btype == "tool_use":
                bid = _block_get(block, "id")
                if bid:
                    ids.add(str(bid))
            elif btype == "tool_result":
                bid = _block_get(block, "tool_use_id")
                if bid:
                    ids.add(str(bid))
    return ids


# ── summary construction ──────────────────────────────────────────────


def summarize_review_actions(
    review_messages: list[Any],
    prior_ids: set[str],
    notification_mode: str = "on",
) -> list[str]:
    """Build the human-facing action list for a completed review pass.

    Walks the fork's messages and collects *successful, committed* Memory
    tool results (``success`` true and ``staged`` not true — a staged
    write is not a committed write). Results whose tool_use id appears in
    ``prior_ids`` (the inherited snapshot) are skipped.

    ``notification_mode``: ``off`` → no actions; ``on`` → generic
    "Memory updated" lines; ``verbose`` → compact content previews built
    from the tool-call *arguments* (the result JSON only says
    "Entry added").
    """
    mode = str(notification_mode or "on").lower()
    if mode == "off":
        return []
    verbose = mode == "verbose"

    # Map tool_use id → call arguments for the Memory tool.
    call_details: dict[str, dict[str, Any]] = {}
    for msg in review_messages or []:
        if _msg_role(msg) != "assistant":
            continue
        for block in _iter_blocks(msg):
            if _block_get(block, "type") != "tool_use":
                continue
            if _block_get(block, "name") != REVIEW_TOOL_NAME:
                continue
            bid = _block_get(block, "id")
            args = _block_get(block, "input") or {}
            if bid and isinstance(args, dict):
                call_details[str(bid)] = {
                    "action": args.get("action", "?"),
                    "target": args.get("target", "memory"),
                    "content": args.get("content", "") or "",
                    "old_text": args.get("old_text", "") or "",
                    "operations": args.get("operations") or [],
                }

    actions: list[str] = []
    for msg in review_messages or []:
        for block in _iter_blocks(msg):
            if _block_get(block, "type") != "tool_result":
                continue
            bid = str(_block_get(block, "tool_use_id") or "")
            if bid and bid in prior_ids:
                continue  # inherited from the snapshot — not fresh work
            if bid not in call_details:
                continue  # not a Memory call from this review
            try:
                data = json.loads(_flatten_result_text(_block_get(block, "content")))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict) or not data.get("success"):
                continue
            if data.get("staged"):
                continue  # staged-for-approval — nothing committed yet
            detail = call_details.get(bid, {})
            target = data.get("target", "") or detail.get("target", "memory")
            label = "User profile" if target == "user" else "Memory"

            if not verbose:
                actions.append(f"{label} updated")
                continue

            max_preview = 120
            operations = detail.get("operations") or []
            action = detail.get("action", "")
            content = detail.get("content", "")
            old_text = detail.get("old_text", "")
            if operations:
                for op in operations:
                    op = op or {}
                    op_act = op.get("action", "")
                    op_content = op.get("content") or ""
                    op_old = op.get("old_text") or ""
                    if op_act == "add" and op_content:
                        preview = op_content[:max_preview] + ("…" if len(op_content) > max_preview else "")
                        actions.append(f"{label} ➕ {preview}")
                    elif op_act == "replace" and op_content:
                        preview = op_content[:max_preview] + ("…" if len(op_content) > max_preview else "")
                        actions.append(f"{label} ✏️ {preview}")
                    elif op_act == "remove" and op_old:
                        preview = op_old[:60] + ("…" if len(op_old) > 60 else "")
                        actions.append(f"{label} ➖ {preview}")
            elif action == "add" and content:
                preview = content[:max_preview] + ("…" if len(content) > max_preview else "")
                actions.append(f"{label} ➕ {preview}")
            elif action == "replace" and content:
                preview = content[:max_preview] + ("…" if len(content) > max_preview else "")
                actions.append(f"{label} ✏️ {preview}")
            elif action == "remove" and old_text:
                preview = old_text[:60] + ("…" if len(old_text) > 60 else "")
                actions.append(f"{label} ➖ {preview}")
            else:
                actions.append(f"{label} updated")

    return actions


def format_review_summary(actions: list[str]) -> str | None:
    """The one-line transcript summary, or None when nothing happened."""
    if not actions:
        return None
    deduped = " · ".join(dict.fromkeys(actions))
    return f"💾 Self-improvement review: {deduped}"


def count_staged_actions(review_messages: list[Any], prior_ids: set[str]) -> int:
    """Fresh Memory-tool results the write-approval gate STAGED (success +
    staged, id not inherited). With the gate on, every fork write stages —
    the user still deserves a signal that pending records are accumulating
    (design-critic M5), even though nothing was committed."""
    staged = 0
    for msg in review_messages or []:
        for block in _iter_blocks(msg):
            if _block_get(block, "type") != "tool_result":
                continue
            bid = str(_block_get(block, "tool_use_id") or "")
            if bid and bid in prior_ids:
                continue
            try:
                data = json.loads(_flatten_result_text(_block_get(block, "content")))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and data.get("success") and data.get("staged"):
                staged += 1
    return staged


def format_staged_notice(staged: int) -> str | None:
    """The gate-on notification line, or None when nothing was staged."""
    if staged <= 0:
        return None
    plural = "s" if staged != 1 else ""
    return (
        f"💾 Self-improvement review: {staged} memory write{plural} staged "
        f"for review — /memory pending"
    )


# ── nudge-counter helpers ─────────────────────────────────────────────


def hydrate_turns_since_memory(prior_user_turns: int, interval: int) -> int:
    """Resume-time hydration: a rebuilt session continues the cadence from
    the persisted history instead of restarting at zero (donor issue
    #22357): ``turns_since_memory = prior_user_turns % interval``."""
    if interval <= 0 or prior_user_turns <= 0:
        return 0
    return prior_user_turns % interval


def turn_used_memory_tool(turn_messages: list[Any]) -> bool:
    """Whether the foreground turn called the Memory tool — an organic
    write postpones the nudge (donor tool_executor.py:318-322)."""
    for msg in turn_messages or []:
        if _msg_role(msg) != "assistant":
            continue
        for block in _iter_blocks(msg):
            if (
                _block_get(block, "type") == "tool_use"
                and _block_get(block, "name") == REVIEW_TOOL_NAME
            ):
                return True
    return False


__all__ = [
    "MEMORY_REVIEW_PROMPT",
    "REVIEW_DENY_MESSAGE",
    "REVIEW_MAX_TURNS",
    "REVIEW_TOOL_NAME",
    "REVIEW_TOOL_WARNING",
    "collect_tool_use_ids",
    "count_staged_actions",
    "format_review_summary",
    "format_staged_notice",
    "hydrate_turns_since_memory",
    "summarize_review_actions",
    "turn_used_memory_tool",
]
