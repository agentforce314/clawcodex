"""
Layer 2: Snip compact — stub matching typescript/src/services/compact/snipCompact.ts.

The TS implementation is a stub that returns null (not implemented).
We match that behavior here to avoid aggressively trimming tool results
that the model may need to reference later in the conversation.
"""
from __future__ import annotations

from typing import Any

from ...types.messages import Message

SNIPPED_MARKER = "[Snipped: tool result too old]"
DEFAULT_KEEP_RECENT = 10


def snip_compact(
    messages: list[Message],
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[Message], int]:
    return list(messages), 0
