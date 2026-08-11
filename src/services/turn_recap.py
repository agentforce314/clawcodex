"""End-of-turn recap + suggested-next-prompt generator (CC parity).

Current Claude Code ends a completed turn with a dim ``✻ recap: …`` transcript
line (goal → where things stand → "Next: …") and pre-fills the composer with a
short suggested follow-up prompt the user accepts with Tab. The vendored
``typescript/`` snapshot predates the feature (its closest relative is
``services/awaySummary.ts`` — same small-fast-model side query over the recent
transcript), so this module is ported from the observed behavior, reusing the
repo's established side-query conventions:

* small-fast-model pin via the memdir resolver (Anthropic sessions only,
  ``find_relevant_memories._resolve_recall_model``) — same concern as
  ``tool_use_summary.py``: never pay the session model's price for a side call;
* bounded recent-transcript window, mirroring awaySummary's
  ``RECENT_MESSAGE_WINDOW = 30``;
* non-critical contract: every failure path returns ``None`` and never raises.

The wire format is two tagged lines (``RECAP:`` / ``SUGGESTION:``) rather than
JSON — the call is served by a small model, and line tags survive sloppy output
(stray prose, fences) that would kill ``json.loads``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors awaySummary.ts RECENT_MESSAGE_WINDOW: recaps only need recent
# context, and a bounded window avoids "prompt too long" on large sessions.
RECENT_MESSAGE_WINDOW = 30
# Per-message and total character budgets for the serialized tail. The window
# above bounds message COUNT; these bound message SIZE (a single pasted log
# would otherwise blow the side-query budget on its own).
PER_MESSAGE_CHARS = 700
TOTAL_CHARS = 8000
# A suggestion is a one-line composer prefill, not an essay.
MAX_SUGGESTION_CHARS = 200

TURN_RECAP_SYSTEM_PROMPT = (
    "You write the end-of-turn recap for a coding-agent CLI. The user just "
    "finished a turn and may step away; the recap tells them where they are, "
    "and the suggestion pre-fills their next prompt (they press Tab to "
    "accept it).\n\n"
    "Reply with exactly two lines and nothing else:\n"
    "RECAP: 1-3 short sentences. Lead with the high-level goal (what the "
    "user is building, debugging, or asking about — not implementation "
    "minutiae), then where things stand after this turn, and end with "
    "'Next: <the concrete next step>.' Skip pleasantries, status-report "
    "filler, and commit recaps.\n"
    "SUGGESTION: the follow-up prompt the user would most likely type next, "
    "as one short imperative under 12 words (e.g. 'fix all four issues and "
    "re-run'). It must make sense sent verbatim as the next user message. "
    "If the work is fully complete and no follow-up makes sense, write NONE."
)


def _block_fragment(block: Any) -> str:
    """One transcript fragment for a content block. Text rides verbatim;
    tool traffic collapses to its name — the assistant's own narration is
    what a recap needs, not tool payloads (and results can be huge)."""
    btype = getattr(block, "type", None) or (
        block.get("type") if isinstance(block, dict) else None
    )
    if btype == "text":
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else ""
        )
        return str(text or "")
    if btype == "tool_use":
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else ""
        )
        return f"[tool: {name or 'unknown'}]"
    if btype == "tool_result":
        return "[tool result]"
    if btype == "thinking":
        return ""  # never leak thinking into a side query
    return ""


def serialize_transcript_tail(
    messages: list[Any],
    *,
    max_messages: int = RECENT_MESSAGE_WINDOW,
    per_message_chars: int = PER_MESSAGE_CHARS,
    total_chars: int = TOTAL_CHARS,
) -> str:
    """Render the last ``max_messages`` conversation messages as a compact
    ``User:``/``Assistant:`` transcript for the recap prompt.

    Tool blocks collapse to ``[tool: Name]``/``[tool result]`` markers; a
    message that reduces to nothing (e.g. a pure tool-result carrier whose
    marker-only body would add noise) still keeps its marker so the model can
    see work happened, but a fully empty message is skipped. Head-drops whole
    messages (keeping the most recent) until the total fits ``total_chars``.
    """
    lines: list[str] = []
    for msg in messages[-max_messages:]:
        role = str(getattr(msg, "role", "") or "")
        if role not in ("user", "assistant"):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            body = content
        elif isinstance(content, list):
            body = " ".join(
                frag for frag in (_block_fragment(b) for b in content) if frag
            )
        else:
            body = str(content or "")
        body = " ".join(body.split())  # collapse whitespace/newlines
        if not body:
            continue
        if len(body) > per_message_chars:
            body = body[: per_message_chars - 1] + "…"
        lines.append(f"{'User' if role == 'user' else 'Assistant'}: {body}")

    while lines and sum(len(line) + 2 for line in lines) > total_chars:
        lines.pop(0)
    return "\n\n".join(lines)


_RECAP_TAG = re.compile(r"^\s*recap\s*:\s*", re.IGNORECASE)
_SUGGESTION_TAG = re.compile(r"^\s*suggestion\s*:\s*", re.IGNORECASE)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*$")


def parse_recap_response(text: str | None) -> tuple[str, str] | None:
    """Parse the two-line tagged reply into ``(recap, suggestion)``.

    Lenient by design (small-model output): fences are stripped, the recap
    may spill across lines until the SUGGESTION tag, tags are
    case-insensitive. ``NONE`` (or a missing tag) → empty suggestion. No
    recap → ``None`` — the whole feature is skipped for the turn.
    """
    if not text or not text.strip():
        return None
    lines = [ln for ln in text.splitlines() if not _FENCE.match(ln)]

    recap_parts: list[str] = []
    suggestion_parts: list[str] = []
    section: str | None = None
    for line in lines:
        if _RECAP_TAG.match(line):
            section = "recap"
            recap_parts.append(_RECAP_TAG.sub("", line, count=1))
        elif _SUGGESTION_TAG.match(line):
            section = "suggestion"
            suggestion_parts.append(_SUGGESTION_TAG.sub("", line, count=1))
        elif section == "recap":
            recap_parts.append(line)
        elif section == "suggestion":
            suggestion_parts.append(line)

    recap = " ".join(" ".join(recap_parts).split()).strip()
    suggestion = " ".join(" ".join(suggestion_parts).split()).strip()
    if not recap:
        return None

    # Strip decoration a model may add around the prefill text.
    if len(suggestion) >= 2 and suggestion[0] == suggestion[-1] and suggestion[0] in "\"'`":
        suggestion = suggestion[1:-1].strip()
    if suggestion.upper() in ("", "NONE", "NONE."):
        suggestion = ""
    if len(suggestion) > MAX_SUGGESTION_CHARS:
        suggestion = suggestion[:MAX_SUGGESTION_CHARS].rstrip()
    return recap, suggestion


def _resolve_recap_model(provider: Any) -> str | None:
    """Small-fast-model pin — same resolver (and same reasoning) as
    ``tool_use_summary._resolve_summary_model``. None = session model."""
    try:
        from src.memdir.find_relevant_memories import _resolve_recall_model

        return _resolve_recall_model(provider)
    except Exception:  # noqa: BLE001
        return None


async def generate_turn_recap(
    messages: list[Any],
    provider: Any,
) -> dict[str, str] | None:
    """Generate ``{"recap": …, "suggestion": …}`` for a completed turn.

    ``messages`` — conversation snapshot (``Conversation.messages``).
    Empty transcript, missing provider, model error, or an unparseable reply
    all yield ``None``; recaps are non-critical and never raise.
    """
    if not messages:
        return None
    try:
        transcript = serialize_transcript_tail(messages)
        if not transcript:
            return None
        if provider is None or not hasattr(provider, "chat_async"):
            return None

        # 1200, not ~300: reasoning-default models (deepseek-v4-*, kimi-k3)
        # spend their budget on hidden reasoning BEFORE the two visible
        # lines — a 300 cap starved the reply to a bare "RECAP:" (observed
        # against deepseek-v4-pro). The cap is a ceiling, not a target:
        # non-reasoning models still emit ~50 tokens.
        kwargs: dict[str, Any] = {
            "system": TURN_RECAP_SYSTEM_PROMPT,
            "max_tokens": 1200,
        }
        model = _resolve_recap_model(provider)
        if model:
            kwargs["model"] = model
        # Keep the side query cheap on reasoning-default APIs: ask for the
        # provider's LOWEST effort, but only where the provider itself
        # declares "low" in its vocabulary (deepseek today) — self-describing
        # opt-in, so providers that never saw the field (Anthropic SDK would
        # reject the kwarg) are untouched. Anthropic's non-streaming side
        # call already runs without extended thinking.
        supported = getattr(provider, "supported_reasoning_efforts", None)
        if supported and "low" in supported:
            kwargs["reasoning_effort"] = "low"
        user_prompt = (
            f"Recent conversation:\n\n{transcript}\n\n"
            "Write the RECAP and SUGGESTION lines now."
        )
        response = await provider.chat_async(
            [{"role": "user", "content": user_prompt}], **kwargs
        )
        if response is None:
            return None
        content = getattr(response, "content", None)
        if isinstance(content, list):
            # Anthropic-shape defense (same as the memdir selector):
            # ``ChatResponse.content`` is typed str, but guard against a
            # provider handing back raw content blocks.
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        parsed = parse_recap_response(str(content or ""))
        if parsed is None:
            return None
        recap, suggestion = parsed
        return {"recap": recap, "suggestion": suggestion}
    except Exception:  # noqa: BLE001 — recaps are non-critical
        logger.debug("turn recap generation failed", exc_info=True)
        return None
