"""MCP description-truncation helper.

Phase 10 WI-10.3 (gap #27). Mirrors TS' 2048-char truncation logic
that previously lived inline in both ``client.py:141-147`` (server
``instructions``) and ``tool_wrapper.py`` (tool ``description``). One
helper, two callers.
"""

from __future__ import annotations

MAX_MCP_DESCRIPTION_LENGTH = 2048


def truncate_description(text: str | None) -> str | None:
    """Truncate ``text`` to ``MAX_MCP_DESCRIPTION_LENGTH`` chars + suffix.

    Returns ``None`` if input is None or empty. Suffix
    ``"... [truncated]"`` is appended when truncation actually fires,
    so the model can see why the field is cut off.
    """
    if not text:
        return text
    if len(text) <= MAX_MCP_DESCRIPTION_LENGTH:
        return text
    return text[:MAX_MCP_DESCRIPTION_LENGTH] + "... [truncated]"
