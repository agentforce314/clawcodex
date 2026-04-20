"""
Group messages by API round-trip boundaries.

Port of ``typescript/src/services/compact/grouping.ts``.

An *API round* is one assistant turn plus the tool-result user messages
that follow it.  Grouping is used by the reactive compaction logic so it
can operate on single-prompt agentic sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...types.messages import Message


@dataclass
class ApiRound:
    """One assistant response plus its tool-result user messages."""
    assistant: Message | None = None
    tool_results: list[Message] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        """Return all messages in this round (assistant + tool results)."""
        result: list[Message] = []
        if self.assistant is not None:
            result.append(self.assistant)
        result.extend(self.tool_results)
        return result


def group_messages_by_api_round(messages: list[Message]) -> list[ApiRound]:
    """
    Group *messages* into API round-trip boundaries.

    Each round starts with an assistant message and includes all subsequent
    user messages (tool results) until the next assistant message.  Leading
    user messages (before the first assistant turn) are placed into a round
    with ``assistant=None``.

    Mirrors ``groupMessagesByApiRound`` in the TypeScript reference.
    """
    rounds: list[ApiRound] = []
    current: ApiRound | None = None

    for msg in messages:
        role = msg.role if hasattr(msg, "role") else "user"

        if role == "assistant":
            # Start a new round
            current = ApiRound(assistant=msg)
            rounds.append(current)
        else:
            # user / system / progress — attach to current round
            if current is None:
                current = ApiRound()
                rounds.append(current)
            current.tool_results.append(msg)

    return rounds
