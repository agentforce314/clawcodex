"""
Layer 4: Context collapse — read-time projection via collapse store.

Port of ``typescript/src/services/contextCollapse/index.ts`` (stub in TS,
feature-gated). We implement the full store and projection logic described
in the refactoring plan (WS-6 §6.3).

The messages array is **never** modified in place.  The collapse store holds
summaries keyed by archived message ranges.  ``project_view()`` replays the
collapse log each iteration, replacing archived sections with their summaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...types.content_blocks import TextBlock
from ...types.messages import Message, UserMessage

logger = logging.getLogger(__name__)


@dataclass
class CollapseCommit:
    """One collapse operation: the archived message UUIDs and their summary."""
    archived: list[str]  # UUIDs of messages that were collapsed
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"archived": list(self.archived), "summary": self.summary}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollapseCommit:
        return cls(
            archived=list(data.get("archived", [])),
            summary=str(data.get("summary", "")),
        )


@dataclass
class ContextCollapseStore:
    """
    Persistent log of collapse operations.

    Each commit records a set of archived message UUIDs and the summary
    that replaces them in the projected view.
    """
    commits: list[CollapseCommit] = field(default_factory=list)
    _enabled: bool = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def add_commit(self, archived_msg_ids: list[str], summary: str) -> None:
        """Record a new collapse commit."""
        if not archived_msg_ids or not summary:
            return
        self.commits.append(CollapseCommit(archived=list(archived_msg_ids), summary=summary))

    def project_view(self, messages: list[Message]) -> list[Message]:
        """
        Return *messages* with collapsed sections replaced by summaries.

        Each commit's archived UUIDs are removed and replaced by a single
        synthetic user message containing the summary text.  The replacement
        is inserted at the position of the **first** archived message.

        If the store is disabled or has no commits, the original list is
        returned unchanged (shallow copy).
        """
        if not self._enabled or not self.commits:
            return list(messages)

        # Build a lookup: uuid → commit index for all archived messages
        archived_map: dict[str, int] = {}
        for idx, commit in enumerate(self.commits):
            for uuid in commit.archived:
                archived_map[uuid] = idx

        if not archived_map:
            return list(messages)

        # Track which commits have already had their summary injected
        injected: set[int] = set()
        result: list[Message] = []

        for msg in messages:
            commit_idx = archived_map.get(msg.uuid)
            if commit_idx is not None:
                # This message is archived — inject summary on first occurrence
                if commit_idx not in injected:
                    injected.add(commit_idx)
                    commit = self.commits[commit_idx]
                    summary_msg = UserMessage(
                        content=[TextBlock(text=f"[Collapsed context]\n{commit.summary}")],
                        isMeta=True,
                        isVirtual=True,
                    )
                    result.append(summary_msg)
                # Otherwise skip (already replaced)
            else:
                result.append(msg)

        return result

    def clear(self) -> None:
        """Remove all commits."""
        self.commits.clear()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "commits": [c.to_dict() for c in self.commits],
            "enabled": self._enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextCollapseStore:
        commits = [CollapseCommit.from_dict(c) for c in data.get("commits", [])]
        return cls(commits=commits, _enabled=data.get("enabled", True))


# ---------------------------------------------------------------------------
# Module-level convenience (matches TS stub API)
# ---------------------------------------------------------------------------

_global_store: ContextCollapseStore | None = None


def is_context_collapse_enabled() -> bool:
    """Return whether context collapse is enabled (global instance)."""
    return _global_store is not None and _global_store.enabled


def get_context_collapse_state() -> ContextCollapseStore | None:
    """Return the global context collapse store, or None."""
    return _global_store


def set_context_collapse_store(store: ContextCollapseStore | None) -> None:
    """Set or clear the global context collapse store."""
    global _global_store
    _global_store = store
