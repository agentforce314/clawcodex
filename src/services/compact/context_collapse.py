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

    Two queues exist:
      * ``commits`` — applied collapses; ``project_view`` honors them.
      * ``staged`` — proposed collapses awaiting commit. Held in
        suspension until ``drain_staged()`` promotes them into
        ``commits``. The Ch5/B.3 PTL-recovery path drains staged
        collapses when an overflow forces a real 413 from the API,
        so collapses the agent had been debating actually fire.
    """
    commits: list[CollapseCommit] = field(default_factory=list)
    staged: list[CollapseCommit] = field(default_factory=list)
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
        """Record a new collapse commit (immediately applied)."""
        if not archived_msg_ids or not summary:
            return
        self.commits.append(CollapseCommit(archived=list(archived_msg_ids), summary=summary))

    def add_staged(self, archived_msg_ids: list[str], summary: str) -> None:
        """Ch5/B.3 — record a proposed collapse without applying it.

        Staged collapses are NOT honored by ``project_view`` until
        promoted via ``drain_staged()``. The query loop's PTL recovery
        path calls ``recover_from_overflow`` on a real 413, which
        drains the staged queue to apply pending collapses that the
        agent had been holding back.
        """
        if not archived_msg_ids or not summary:
            return
        self.staged.append(CollapseCommit(archived=list(archived_msg_ids), summary=summary))

    def drain_staged(self) -> int:
        """Ch5/B.3 — promote all staged collapses into ``commits``.

        Returns the number of commits promoted. Called by
        :func:`recover_from_overflow` and downstream by the PTL
        recovery path.
        """
        count = len(self.staged)
        if count == 0:
            return 0
        self.commits.extend(self.staged)
        self.staged.clear()
        return count

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
        """Remove all commits AND staged proposals."""
        self.commits.clear()
        self.staged.clear()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "commits": [c.to_dict() for c in self.commits],
            "staged": [c.to_dict() for c in self.staged],
            "enabled": self._enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextCollapseStore:
        commits = [CollapseCommit.from_dict(c) for c in data.get("commits", [])]
        staged = [CollapseCommit.from_dict(c) for c in data.get("staged", [])]
        return cls(commits=commits, staged=staged, _enabled=data.get("enabled", True))


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


@dataclass
class DrainResult:
    """Outcome of recover_from_overflow.

    ``committed > 0`` means staged collapses were promoted into the
    commit log; ``messages`` is the freshly projected view that
    incorporates them. ``committed == 0`` means nothing to drain —
    the caller should fall through to reactive_compact.
    """
    messages: list[Message]
    committed: int


def recover_from_overflow(
    messages: list[Message],
    query_source: str,
) -> DrainResult:
    """Ch5/B.3 — drain staged context-collapses on a real API 413.

    Mirrors TS ``recoverFromOverflow`` at query.ts:1169. Used by the
    query loop's PTL recovery path BEFORE reactive_compact. If no
    ContextCollapseStore is set, or the store is disabled, or no
    staged collapses are present, returns ``DrainResult(messages,
    committed=0)`` so the caller falls through to reactive_compact.

    The ``query_source`` parameter mirrors the TS signature; it is
    currently unused in Python — callers that need source-specific
    behavior (e.g. skip drain on session_memory forks) can read it
    in a future iteration.
    """
    store = get_context_collapse_state()
    if store is None or not store.enabled:
        return DrainResult(messages=messages, committed=0)
    committed_count = store.drain_staged()
    if committed_count == 0:
        return DrainResult(messages=messages, committed=0)
    return DrainResult(
        messages=store.project_view(messages),
        committed=committed_count,
    )
