"""Async clarification queue for operator answers.

File format: JSON, stored at ~/.clawcodex/clarification_queue.json

Architecture:
- ClarificationItem: one pending question awaiting an answer
- ClarificationQueue: file-backed queue with polling support
- Handles conflict detection (DUPLICATE_REJECTED, STALE_REJECTED, CONFLICT_RESOLVED)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# Default queue path under user config directory
DEFAULT_QUEUE_PATH = Path.home() / ".clawcodex" / "clarification_queue.json"


class ClarificationStatus(str, Enum):
    """Lifecycle stages of a clarification item."""

    NONE = "none"                         # not in clarification flow
    PENDING = "pending"                   # awaiting answer (default on enqueue)
    AWAITING_LOCAL = "awaiting_local"     # waiting for local operator answer
    AWAITING_AUTHOR = "awaiting_author"   # waiting for issue author (@mention sent)
    RESOLVED_LOCAL = "resolved_local"     # resolved by local operator
    RESOLVED_AUTHOR = "resolved_author"   # resolved by issue author
    TIMED_OUT_LOCAL = "timed_out_local"   # local timeout, escalated to author
    TIMED_OUT_AUTHOR = "timed_out_author" # author timeout, escalation triggered
    EXHAUSTED = "exhausted"             # max questions reached, gave up
    # --- conflict handling states ---
    DUPLICATE_REJECTED = "duplicate_rejected"   # duplicate submission, dropped
    STALE_REJECTED = "stale_rejected"           # late answer after escalation
    CONFLICT_RESOLVED = "conflict_resolved"     # simultaneous answers resolved


@dataclass
class ClarificationItem:
    """One entry in the clarification queue."""

    issue_id: str
    issue_identifier: str
    question: str
    options: list[str] = field(default_factory=list)
    context_summary: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    status: ClarificationStatus = ClarificationStatus.PENDING
    answer: str | None = None
    answer_source: str | None = None           # "dashboard" | "clarification_queue" | "author"
    answered_at: float | None = None
    escalation_notified: bool = False           # operator informed of escalation
    first_response_source: str | None = None   # "local" | "author" — first answer source
    duplicate_of: str | None = None             # if DUPLICATE_REJECTED, reference original
    stale_answers: list[str] = field(default_factory=list)  # rejected late answers

    def touch(self) -> None:
        self.updated_at = time.time()

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        if now is None:
            now = time.time()
        return now >= self.expires_at

    def mark_answered(
        self,
        answer: str,
        source: str,
        answered_at: float | None = None,
    ) -> None:
        self.answer = answer
        self.answer_source = source
        self.answered_at = answered_at or time.time()
        self.touch()


class ClarificationQueue:
    """File-backed async clarification queue for operator answers.

    Polling mechanism: call poll_pending() each orchestrator poll cycle
    to find items that are awaiting answers.
    """

    def __init__(self, queue_path: Path | None = None) -> None:
        self._path = queue_path or DEFAULT_QUEUE_PATH
        self._records: dict[str, ClarificationItem] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = {
                k: ClarificationItem(**v) for k, v in data.items()
            }
        except Exception as exc:
            logger.warning(
                "Failed to load clarification queue: %s — starting fresh",
                exc,
            )

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    {k: asdict(v) for k, v in self._records.items()},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save clarification queue: %s", exc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, issue_id: str) -> ClarificationItem | None:
        return self._records.get(issue_id)

    def poll_pending(self) -> list[ClarificationItem]:
        """Return all pending items that have not expired."""
        now = time.time()
        return [
            item
            for item in self._records.values()
            if item.status in (
                ClarificationStatus.PENDING,
                ClarificationStatus.AWAITING_LOCAL,
                ClarificationStatus.AWAITING_AUTHOR,
            )
            and not item.is_expired(now)
        ]

    def get_resolved(self, issue_id: str) -> ClarificationItem | None:
        """Return resolved item if one exists (for answer retrieval)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        if item.status in (
            ClarificationStatus.RESOLVED_LOCAL,
            ClarificationStatus.RESOLVED_AUTHOR,
        ):
            return item
        return None

    def get_stale(self, issue_id: str) -> list[str]:
        """Return list of stale (rejected) answers for an issue."""
        item = self._records.get(issue_id)
        if item is None:
            return []
        return item.stale_answers

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def enqueue(
        self,
        issue_id: str,
        issue_identifier: str,
        question: str,
        *,
        options: list[str] | None = None,
        context_summary: str = "",
        timeout_seconds: float | None = None,
    ) -> ClarificationItem:
        """Create a pending clarification item."""
        now = time.time()
        expires_at = None
        if timeout_seconds is not None:
            expires_at = now + timeout_seconds

        item = ClarificationItem(
            issue_id=issue_id,
            issue_identifier=issue_identifier,
            question=question,
            options=list(options) if options else [],
            context_summary=context_summary,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            status=ClarificationStatus.PENDING,
        )
        self._records[issue_id] = item
        self._save()
        return item

    def mark_awaiting_local(self, issue_id: str) -> ClarificationItem | None:
        """Transition to Channel 1/2 (local operator awaiting)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        item.status = ClarificationStatus.AWAITING_LOCAL
        item.touch()
        self._save()
        return item

    def mark_awaiting_author(self, issue_id: str) -> ClarificationItem | None:
        """Transition to Channel 3 (@mention author)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        item.status = ClarificationStatus.AWAITING_AUTHOR
        item.touch()
        self._save()
        return item

    def resolve(
        self,
        issue_id: str,
        answer: str,
        source: str,
    ) -> ClarificationItem | None:
        """Record an answer from any channel.

        Args:
            issue_id: the issue being clarified
            answer: the answer text
            source: one of "dashboard", "clarification_queue", "author"

        Returns:
            The updated item, or None if not found.
        """
        item = self._records.get(issue_id)
        if item is None:
            return None

        now = time.time()
        item.mark_answered(answer, source, now)

        # Determine resolution status based on current status
        if item.status in (
            ClarificationStatus.PENDING,
            ClarificationStatus.AWAITING_LOCAL,
        ):
            if source in ("dashboard", "clarification_queue"):
                item.status = ClarificationStatus.RESOLVED_LOCAL
            else:
                item.status = ClarificationStatus.RESOLVED_AUTHOR
        elif item.status == ClarificationStatus.AWAITING_AUTHOR:
            # Author answer in author channel
            item.status = ClarificationStatus.RESOLVED_AUTHOR
        else:
            # Unexpected state — still record answer but keep current status
            logger.warning(
                "resolve() called on issue %s in unexpected status %s",
                issue_id,
                item.status,
            )

        item.first_response_source = item.answer_source
        item.touch()
        self._save()
        return item

    def mark_duplicate(
        self,
        issue_id: str,
        duplicate_answer: str,
        original_timestamp: float,
    ) -> ClarificationItem | None:
        """Mark an answer as a duplicate (idempotent deduplication)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        item.status = ClarificationStatus.DUPLICATE_REJECTED
        item.duplicate_of = str(original_timestamp)
        item.stale_answers.append(duplicate_answer)
        item.touch()
        self._save()
        return item

    def mark_stale(
        self,
        issue_id: str,
        stale_answer: str,
        reason: str = "",
    ) -> ClarificationItem | None:
        """Mark a late answer as stale (after channel escalation)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        item.status = ClarificationStatus.STALE_REJECTED
        item.stale_answers.append(stale_answer)
        item.touch()
        self._save()
        return item

    def mark_escalation_notified(self, issue_id: str) -> ClarificationItem | None:
        """Mark that the operator has been informed of channel escalation."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        item.escalation_notified = True
        item.touch()
        self._save()
        return item

    def mark_expired(self, issue_id: str) -> ClarificationItem | None:
        """Mark an item as expired (timeout reached, trigger escalation)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        if item.status == ClarificationStatus.AWAITING_LOCAL:
            item.status = ClarificationStatus.TIMED_OUT_LOCAL
        elif item.status == ClarificationStatus.AWAITING_AUTHOR:
            item.status = ClarificationStatus.TIMED_OUT_AUTHOR
        else:
            item.status = ClarificationStatus.EXHAUSTED
        item.touch()
        self._save()
        return item

    def mark_exhausted(self, issue_id: str) -> ClarificationItem | None:
        """Mark an item as exhausted (max questions reached)."""
        item = self._records.get(issue_id)
        if item is None:
            return None
        item.status = ClarificationStatus.EXHAUSTED
        item.touch()
        self._save()
        return item

    def mark_issue_failed(self, issue_id: str) -> None:
        """Mark an issue as failed due to escalation policy.

        Writes a sentinel file that the orchestrator reads to mark the
        issue as failed on its next poll cycle.
        """
        import json
        import time

        sentinel_path = self._path.parent / ".escalated_issues.json"
        try:
            sentinel_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if sentinel_path.exists():
                existing = json.loads(sentinel_path.read_text())
            existing[issue_id] = {"failed_at": time.time()}
            sentinel_path.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass

    def remove(self, issue_id: str) -> None:
        """Remove an item from the queue."""
        if issue_id in self._records:
            del self._records[issue_id]
            self._save()