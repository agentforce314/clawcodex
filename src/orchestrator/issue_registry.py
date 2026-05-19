"""Local issue→commit→PR mapping registry.

Persists the mapping so that after an orchestrator restart, previously
handled issues can still be identified even if they are no longer in
memory and the tracker API does not reflect the latest state (e.g.
issue still open on the tracker, but a PR already exists on the branch
that was created and abandoned by a previous run).

File format: JSON, stored at `{workspace.root}/.clawcodex_issue_registry.json`
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class IssueStatus(str, Enum):
    """Lifecycle stages of a tracked issue."""

    PENDING = "pending"           # claimed, workspace created, not yet synced
    SYNCED = "synced"             # git sync completed (commit + push + PR)
    COMPLETED = "completed"       # session finished successfully
    FAILED = "failed"            # session ended with a non-success status
    ABANDONED = "abandoned"      # retry limit reached, gave up


@dataclass
class IssueRecord:
    """One entry in the issue registry."""

    issue_id: str
    issue_identifier: str
    branch_name: str | None = None
    commit_sha: str | None = None
    pr_number: str | None = None
    pr_url: str | None = None
    base_branch: str = "main"
    status: IssueStatus = IssueStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    attempt_count: int = 0

    def touch(self) -> None:
        self.updated_at = time.time()


class IssueRegistry:
    """Persistent issue→commit→PR mapping, stored as JSON."""

    def __init__(self, storage_path: Path) -> None:
        self._path = storage_path
        self._records: dict[str, IssueRecord] = {}
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
                k: IssueRecord(**v) for k, v in data.items()
            }
        except Exception as exc:
            logger.warning("Failed to load issue registry: %s — starting fresh", exc)

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
            logger.warning("Failed to save issue registry: %s", exc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, issue_id: str) -> IssueRecord | None:
        return self._records.get(issue_id)

    def get_by_branch(self, branch_name: str) -> IssueRecord | None:
        for record in self._records.values():
            if record.branch_name == branch_name:
                return record
        return None

    def has_pr(self, issue_id: str) -> bool:
        record = self._records.get(issue_id)
        return record is not None and record.pr_number is not None

    def is_completed(self, issue_id: str) -> bool:
        record = self._records.get(issue_id)
        return record is not None and record.status == IssueStatus.COMPLETED

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def register(
        self,
        issue_id: str,
        issue_identifier: str,
        branch_name: str | None = None,
        base_branch: str = "main",
    ) -> IssueRecord:
        """Create a pending record for a newly claimed issue."""
        record = IssueRecord(
            issue_id=issue_id,
            issue_identifier=issue_identifier,
            branch_name=branch_name,
            base_branch=base_branch,
        )
        self._records[issue_id] = record
        self._save()
        return record

    def mark_synced(
        self,
        issue_id: str,
        *,
        branch_name: str | None = None,
        commit_sha: str | None = None,
        pr_number: str | None = None,
        pr_url: str | None = None,
    ) -> IssueRecord | None:
        """Update record after git sync has run."""
        record = self._records.get(issue_id)
        if record is None:
            return None
        if branch_name is not None:
            record.branch_name = branch_name
        if commit_sha is not None:
            record.commit_sha = commit_sha
        if pr_number is not None:
            record.pr_number = pr_number
        if pr_url is not None:
            record.pr_url = pr_url
        record.status = IssueStatus.SYNCED
        record.touch()
        self._save()
        return record

    def mark_completed(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.COMPLETED
        record.touch()
        self._save()
        return record

    def mark_failed(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.FAILED
        record.attempt_count += 1
        record.touch()
        self._save()
        return record

    def mark_abandoned(self, issue_id: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.status = IssueStatus.ABANDONED
        record.touch()
        self._save()
        return record

    def update_branch(self, issue_id: str, branch_name: str) -> IssueRecord | None:
        record = self._records.get(issue_id)
        if record is None:
            return None
        record.branch_name = branch_name
        record.touch()
        self._save()
        return record