"""Session title generation matching TypeScript session/title.ts."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 80

# The side query that names a session, sent to the session's OWN provider so
# it never bills an account the user did not choose for this conversation.
_TITLE_PROMPT = (
    "Write a title for a chat that begins with the request below. "
    "Reply with the title only: three to six words, in the language of the "
    "request, no quotes, no trailing punctuation.\n\nRequest:\n{text}"
)

# How much of the first prompt the title query sees. A pasted log or a long
# spec is summarised from its head; the title is about what was asked.
_TITLE_INPUT_CHARS = 2000

# Room for the reply. A reasoning model thinks inside this budget before it
# answers — DeepSeek's thinking mode spent a 48-token cap entirely on thought
# and returned nothing — so the cap leaves room for a paragraph of reasoning
# ahead of the six words that come out.
_TITLE_MAX_TOKENS = 512

_TITLE_LABEL_RE = re.compile(r"^(?:title|标题)\s*[:：]\s*", re.IGNORECASE)


def auto_title_from_message(text: str) -> str:
    """Extract a title from the first user message.

    Truncates to MAX_TITLE_LENGTH chars, removes newlines,
    strips leading/trailing whitespace and punctuation.
    """
    if not text:
        return "Untitled session"

    # Take first line or first sentence
    lines = text.strip().split("\n")
    first_line = lines[0].strip()

    # Remove common prefixes
    for prefix in ("please ", "can you ", "help me ", "i need ", "i want "):
        if first_line.lower().startswith(prefix):
            first_line = first_line[len(prefix):]
            break

    # Capitalize first letter
    if first_line and first_line[0].islower():
        first_line = first_line[0].upper() + first_line[1:]

    # Truncate
    if len(first_line) > MAX_TITLE_LENGTH:
        first_line = first_line[:MAX_TITLE_LENGTH - 3].rstrip() + "..."

    return first_line or "Untitled session"


def clean_generated_title(raw: Any) -> str | None:
    """The first usable line of a model's title reply, or None.

    Models decorate: quotes, a ``Title:`` label, a trailing period, a
    preamble line. The title is whatever survives — and nothing, when the
    reply was empty or only decoration, so the caller keeps its fallback
    rather than naming a session "".
    """
    if not isinstance(raw, str):
        return None
    line = next((part.strip() for part in raw.strip().splitlines() if part.strip()), "")
    line = _TITLE_LABEL_RE.sub("", line)
    line = line.strip("\"'`“”‘’ ").rstrip(".!。！").strip()
    line = _TITLE_LABEL_RE.sub("", line).strip("\"'“” ")
    if not line:
        return None
    if len(line) > MAX_TITLE_LENGTH:
        line = line[:MAX_TITLE_LENGTH - 3].rstrip() + "..."
    return line


async def generate_title_with_provider(
    provider: Any,
    text: str,
    *,
    timeout_s: float = 30.0,
) -> str | None:
    """A short title for a session, written by the session's own model.

    One small non-streaming request through ``provider.chat_async`` — the
    same provider (and account) the conversation runs on, unlike
    :func:`generate_llm_title`, which is pinned to Anthropic. Best-effort by
    contract: no provider, an empty prompt, a timeout, a provider error or an
    unusable reply all answer None, and the caller keeps the heuristic name
    it already has.
    """
    if provider is None or not isinstance(text, str) or not text.strip():
        return None
    body = text.strip()[:_TITLE_INPUT_CHARS]
    messages = [{"role": "user", "content": _TITLE_PROMPT.format(text=body)}]
    try:
        response = await asyncio.wait_for(
            provider.chat_async(messages, max_tokens=_TITLE_MAX_TOKENS), timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — a title is never worth an error
        logger.debug("provider title generation failed: %s", exc)
        return None
    return clean_generated_title(getattr(response, "content", None))


async def generate_llm_title(
    messages: list[dict[str, Any]],
    *,
    model: str = "claude-haiku-4-5",
) -> str | None:
    """Generate a 5-10 word title using a side LLM query.

    Returns None if the LLM call fails.
    """
    try:
        import anthropic

        # Build a concise prompt
        msg_summaries = []
        for msg in messages[:5]:  # First 5 messages
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(texts)
            if isinstance(content, str):
                content = content[:200]
            msg_summaries.append(f"{role}: {content}")

        summary = "\n".join(msg_summaries)

        from src.services.api.custom_headers import get_anthropic_custom_headers
        client = anthropic.AsyncAnthropic(
            default_headers=get_anthropic_custom_headers() or None
        )
        response = await client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": (
                    "Generate a concise 5-10 word title for this conversation. "
                    "Return ONLY the title, no quotes or punctuation.\n\n"
                    f"{summary}"
                ),
            }],
        )

        if response.content and len(response.content) > 0:
            title = response.content[0].text.strip()
            # Clean up
            title = title.strip('"\'')
            if len(title) > MAX_TITLE_LENGTH:
                title = title[:MAX_TITLE_LENGTH - 3] + "..."
            return title

    except Exception as e:
        logger.debug("LLM title generation failed: %s", e)

    return None
