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

    Ch5/B.3: also holds a "staged" set of commits that have been queued
    but not yet promoted to the committed log. ``drain_staged()`` moves
    them all into ``commits`` and returns the count of newly-promoted
    commits. This separation lets the loop attempt a recovery via
    collapse-drain on a 413 BEFORE falling through to reactive_compact.
    """
    commits: list[CollapseCommit] = field(default_factory=list)
    _enabled: bool = True
    # Ch5/B.3: staged-but-not-committed collapse operations. These do
    # NOT participate in project_view() until drain_staged() is called.
    _staged: list[CollapseCommit] = field(default_factory=list)

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

    def add_staged(self, archived_msg_ids: list[str], summary: str) -> None:
        """Ch5/B.3: queue a collapse commit without making it visible
        to ``project_view()`` until ``drain_staged()`` is called.

        Mirrors the TS staged/committed distinction at
        ``contextCollapse/index.ts``. A staged commit becomes a real
        commit only when the loop drains the queue on a 413, so the
        granular context is preserved as long as the conversation
        stays under the limit.
        """
        if not archived_msg_ids or not summary:
            return
        self._staged.append(
            CollapseCommit(archived=list(archived_msg_ids), summary=summary)
        )

    def drain_staged(self) -> int:
        """Ch5/B.3: promote all staged commits into the committed log.

        Returns the number of newly-promoted commits (0 if nothing
        was staged). After draining, ``project_view()`` will include
        the drained commits' summaries.
        """
        if not self._staged:
            return 0
        count = len(self._staged)
        self.commits.extend(self._staged)
        self._staged.clear()
        return count

    def staged_count(self) -> int:
        """Return the number of staged-but-not-committed entries."""
        return len(self._staged)

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
        # Staged entries are intentionally ephemeral — they exist
        # only within a single REPL session's lifetime. If a session
        # is persisted/restored, staged collapses are dropped (the
        # next iteration's compression pipeline can re-stage them as
        # needed). Mirrors TS semantics where the staged queue is a
        # session-local optimization, not durable state.
        return {
            "commits": [c.to_dict() for c in self.commits],
            "enabled": self._enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextCollapseStore:
        commits = [CollapseCommit.from_dict(c) for c in data.get("commits", [])]
        # `_staged` deliberately starts empty on restore — see to_dict.
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


# ---------------------------------------------------------------------------
# Ch5/B.3: PTL recovery via staged-collapse drain
# ---------------------------------------------------------------------------


@dataclass
class DrainResult:
    """Outcome of :func:`recover_from_overflow`.

    ``committed > 0`` means staged collapses were promoted to the
    commit log; ``messages`` holds the drained view (with the now-
    committed summaries projected in, ``isVirtual=False`` so the
    summary reaches the API on retry).
    ``committed == 0`` means there was nothing staged to drain —
    caller should fall through to ``reactive_compact``.
    """
    messages: list[Message]
    committed: int


def recover_from_overflow(
    messages: list[Message],
    query_source: str,
) -> DrainResult:
    """Ch5/B.3: drain staged context-collapses on a real API 413.

    Mirrors TS ``recoverFromOverflow`` at ``query.ts:1169``. The
    chapter-§"Context Collapse" contract is:

      1. The store carries STAGED collapses while the conversation
         is still under the limit (preserve granular context).
      2. When the API returns 413, drain the staged queue — all
         staged collapses are now in the commit log.
      3. ``project_view`` over ``messages`` now returns a smaller
         array (archived spans replaced by their summaries).
      4. Caller retries with the drained view.

    Returns ``DrainResult(messages=messages, committed=0)`` if no
    store is configured or no staged collapses exist, so the caller
    falls through to ``reactive_compact`` cleanly.

    Note on visibility: ``project_view`` marks summary messages as
    ``isVirtual=True`` for the read-time UI-projection use case —
    those are filtered from the API by ``normalize_messages_for_api``.
    For drain-recovery the summary MUST reach the API, so we
    explicitly clear ``isVirtual`` on the drained view before
    returning. This is the API-bound projection.

    ``query_source`` is accepted for parity with the TS signature
    but not currently consumed — reserved for future per-source
    drain policies (e.g., subagents may need different behavior).
    """
    _ = query_source  # reserved
    store = get_context_collapse_state()
    if store is None:
        return DrainResult(messages=list(messages), committed=0)
    committed_count = store.drain_staged()
    if committed_count == 0:
        return DrainResult(messages=list(messages), committed=0)

    projected = store.project_view(messages)
    # Clear isVirtual on the summary messages so they are NOT filtered
    # out of the API call (read-time projection vs. API-bound
    # projection — these are different use cases of project_view).
    api_visible: list[Message] = []
    for msg in projected:
        if getattr(msg, "isVirtual", False):
            try:
                import copy as _copy
                cloned = _copy.copy(msg)
                cloned.isVirtual = False  # type: ignore[attr-defined]
                api_visible.append(cloned)
            except Exception:
                api_visible.append(msg)
        else:
            api_visible.append(msg)
    return DrainResult(messages=api_visible, committed=committed_count)
