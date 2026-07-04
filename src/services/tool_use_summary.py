"""Tool-use summary generator — SERVICES-1 (services-folder parity).

Port of ``typescript/src/services/toolUseSummary/toolUseSummaryGenerator.ts``:
a small-fast-model side query that writes a ~30-char git-commit-subject
label for a completed tool round. It appears as a single mobile-app row
(the SDK ``streamlined_tool_use_summary`` message). Non-critical — every
failure path returns ``None`` and never raises (TS: "summaries are
non-critical").

The plumbing was present but DEAD: ``QueryState.pending_tool_use_summary``
(query/transitions.py), ``config.tool_use_summary_enabled`` /
``emit_tool_use_summaries`` — all carried, never produced or consumed. This
module is the faithful port of the missing GENERATOR.

Query-loop wiring is DEFERRED (critic round, distinct from the sweep's other
inert-plumbing fixes): unlike PermissionRequest hooks or the `if`-filter —
whose consumers existed once wired — the Haiku-label consumer is a mobile /
external SDK app that does NOT exist in this port. TS emits the label
SDK-only (``createToolUseSummaryMessage`` → ``{type:'tool_use_summary',
summary, precedingToolUseIds, …}``, "SDK-only, ignore in stream handling")
and Python's ``sdk_types`` carries only the UNRELATED drop-filtered
``streamlined_tool_use_summary`` (a tool-COUNT feature, not this label).
Wiring a per-tool-round small-model call here would be cost-without-benefit
until such a consumer lands. This generator is complete and tested, ready to
wire (fire-and-forget after the tool round → carry on
``pending_tool_use_summary`` → await bounded → yield a ``tool_use_summary``
SDK message with ``preceding_tool_use_ids``) the moment one does.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# VERBATIM from toolUseSummaryGenerator.ts (model-facing, eval-tuned —
# mechanically extracted, do not paraphrase).
TOOL_USE_SUMMARY_SYSTEM_PROMPT = (
    "Write a short summary label describing what these tool calls "
    "accomplished. It appears as a single-line row in a mobile app and "
    "truncates around 30 characters, so think git-commit-subject, not "
    "sentence.\n\nKeep the verb in past tense and the most distinctive "
    "noun. Drop articles, connectors, and long location context first."
    "\n\nExamples:\n- Searched in auth/\n- Fixed NPE in UserService\n"
    "- Created signup endpoint\n- Read config.json\n- Ran failing tests"
)


def _truncate_json(value: Any, max_len: int) -> str:
    """Port of ``truncateJson``: JSON-stringify then slice. BYTE-EXACT to TS
    (critic B3): ``str.slice(0, maxLength - 3) + '...'`` — three ASCII dots,
    and the slice stops 3 short so the result length == ``max_len``. None-safe;
    non-serializable values fall back to ``str()``."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _resolve_summary_model(provider: Any) -> str | None:
    """The small-fast-model pin for this side query. Reuses the memdir
    selector's resolver — the SAME concern (don't pay the session model's
    price for a cheap side call; the shipped small_fast_model default is an
    Anthropic id, so pin only when the session provider is Anthropic, else
    fall back to the session model). Returns None to signal fallback.
    Never raises."""
    try:
        from src.memdir.find_relevant_memories import _resolve_recall_model

        return _resolve_recall_model(provider)
    except Exception:  # noqa: BLE001
        return None


async def generate_tool_use_summary(
    tools: list[dict[str, Any]],
    provider: Any,
    *,
    last_assistant_text: str | None = None,
) -> str | None:
    """Generate a short label for a completed tool round, or None.

    ``tools`` — list of ``{"name", "input", "output"}``. Empty → None.
    Mirrors ``generateToolUseSummary``: build the truncated per-tool
    representation, prepend the optional last-assistant-intent context,
    query the small fast model, return the stripped label (or None).
    """
    if not tools:
        return None
    try:
        tool_summaries = "\n\n".join(
            "Tool: {name}\nInput: {inp}\nOutput: {out}".format(
                name=t.get("name", ""),
                inp=_truncate_json(t.get("input"), 300),
                out=_truncate_json(t.get("output"), 300),
            )
            for t in tools
        )
        context_prefix = (
            "User's intent (from assistant's last message): "
            f"{last_assistant_text[:200]}\n\n"
            if last_assistant_text
            else ""
        )
        user_prompt = (
            f"{context_prefix}Tools completed:\n\n{tool_summaries}\n\nLabel:"
        )

        if provider is None or not hasattr(provider, "chat_async"):
            return None
        kwargs: dict[str, Any] = {
            "system": TOOL_USE_SUMMARY_SYSTEM_PROMPT,
            "max_tokens": 64,
        }
        model = _resolve_summary_model(provider)
        if model:
            kwargs["model"] = model
        response = await provider.chat_async(
            [{"role": "user", "content": user_prompt}], **kwargs
        )
        if response is None:
            return None
        summary = (getattr(response, "content", "") or "").strip()
        return summary or None
    except Exception:  # noqa: BLE001 — summaries are non-critical
        logger.debug("tool-use summary generation failed", exc_info=True)
        return None
