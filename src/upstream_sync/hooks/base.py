# upstream_sync/hooks/base.py
"""Lifecycle hook base classes.

Projects can subclass these hooks to inject custom logic at key points in the
sync pipeline without modifying the core framework.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from upstream_sync.config import ProjectConfig
from upstream_sync.core.change_analyzer import ChangeReport


class SyncHooks:
    """Base class for sync pipeline lifecycle hooks.

    All methods have default no-op implementations; subclasses override only
    the hooks they care about.
    """

    def __init__(self, config: ProjectConfig) -> None:
        self.cfg = config

    # ------------------------------------------------------------------
    # Pre-stage hooks
    # ------------------------------------------------------------------

    def pre_fetch(self, repo_root: Path) -> None:
        """Called before fetching from upstream remote."""

    def pre_analyze(self, from_ref: str, to_ref: str) -> None:
        """Called before running change analysis."""

    def pre_apply(self, patch_dir: Path, series_file: Path) -> None:
        """Called before applying the patch queue."""

    def pre_audit(self) -> None:
        """Called before running layer audit."""

    # ------------------------------------------------------------------
    # Post-stage hooks
    # ------------------------------------------------------------------

    def post_fetch(self, commit_hash: str) -> None:
        """Called after successful upstream fetch."""

    def post_analyze(self, report: ChangeReport) -> None:
        """Called after change analysis completes."""

    def post_apply(self, results: dict[str, Any]) -> None:
        """Called after patch application (success or failure)."""

    def post_audit(self, violations: list[Any]) -> None:
        """Called after layer audit completes."""
