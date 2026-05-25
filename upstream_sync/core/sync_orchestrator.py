# upstream_sync/core/sync_orchestrator.py
"""Sync pipeline orchestration.

Coordinates the end-to-end upstream sync workflow:
  fetch -> analyze -> (optionally auto-resolve) -> apply patches -> audit layers
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from upstream_sync.config import ProjectConfig
from upstream_sync.core.change_analyzer import ChangeAnalyzer, ChangeReport
from upstream_sync.core.layer_auditor import LayerAuditor
from upstream_sync.core.patch_engine import PatchEngine
from upstream_sync.core.vendor import VendorManager

if TYPE_CHECKING:
    from upstream_sync.hooks.base import SyncHooks


class SyncOrchestrator:
    """High-level coordinator for the upstream sync pipeline."""

    def __init__(
        self,
        repo_root: Path,
        config: ProjectConfig,
        vendor: VendorManager | None = None,
        analyzer: ChangeAnalyzer | None = None,
        engine: PatchEngine | None = None,
        auditor: LayerAuditor | None = None,
        hooks: "SyncHooks | None" = None,
    ) -> None:
        self.repo_root = repo_root
        self.cfg = config
        self.vendor = vendor or VendorManager(repo_root, config.upstream)
        self.analyzer = analyzer or ChangeAnalyzer(repo_root, config)
        self.engine = engine
        self.auditor = auditor or LayerAuditor(config)
        self.hooks = hooks

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def run_fetch(self) -> str:
        """Ensure remote exists and fetch upstream main."""
        if self.hooks:
            self.hooks.pre_fetch(self.repo_root)
        self.vendor.ensure_remote()
        commit = self.vendor.fetch()
        if self.hooks:
            self.hooks.post_fetch(commit)
        return commit

    def run_analyze(self, from_ref: str, to_ref: str) -> ChangeReport:
        """Run change analysis between two refs."""
        if self.hooks:
            self.hooks.pre_analyze(from_ref, to_ref)
        report = self.analyzer.analyze(from_ref, to_ref)
        if self.hooks:
            self.hooks.post_analyze(report)
        return report

    def run_apply(self) -> dict:
        """Apply the configured patch queue.

        Returns:
            Dict with keys: success (list), failed (list), needs_review (list).
        """
        if self.hooks:
            self.hooks.pre_apply(self.cfg.patches.directory, self.cfg.patches.series_file)
        if self.engine is None:
            from upstream_sync.core.patch_engine import create_engine
            self.engine = create_engine(self.cfg.patches)
        result = self.engine.apply_all(
            self.cfg.patches.directory,
            self.cfg.patches.series_file,
        )
        output = {
            "success": result.success,
            "failed": result.failed,
            "needs_review": result.needs_review,
        }
        if self.hooks:
            self.hooks.post_apply(output)
        return output

    def run_audit(self) -> list:
        """Audit layer imports and return violations."""
        if self.hooks:
            self.hooks.pre_audit()
        violations = self.auditor.audit()
        if self.hooks:
            self.hooks.post_audit(violations)
        return violations

    def detect_refs(self) -> tuple[str, str]:
        """Auto-detect previous and latest refs using local tags."""
        return self.vendor.detect_sync_refs()

    def run_full_sync(
        self,
        from_ref: str | None = None,
        to_ref: str | None = None,
        auto: bool = False,
    ) -> dict:
        """Execute the complete sync pipeline.

        Args:
            from_ref: Previous locked upstream ref (auto-detected if None).
            to_ref: Target upstream ref (auto-detected if None).
            auto: If ``True``, automatically resolve changes below the
                ``impact_threshold_auto`` configured threshold.

        Returns:
            A dictionary summarising the pipeline results.
        """
        # Auto-detect refs if not provided
        if from_ref is None or to_ref is None:
            detected_from, detected_to = self.detect_refs()
            from_ref = from_ref or detected_from
            to_ref = to_ref or detected_to

        # Stage 1: fetch
        commit = self.run_fetch()

        # Stage 2: analyze
        report = self.run_analyze(from_ref, to_ref)

        results: dict = {
            "fetch_commit": commit,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "report": report,
            "applied": [],
            "failed": [],
            "needs_review": [],
            "violations": [],
        }

        # Stage 3: conditionally auto-resolve / apply patches
        if auto and report.overall_impact in ("low", self.cfg.sync.impact_threshold_auto):
            apply_result = self.run_apply()
            results["applied"] = apply_result["success"]
            results["failed"] = [f[0] for f in apply_result["failed"]]
            results["needs_review"] = apply_result["needs_review"]

        # Stage 4: audit
        violations = self.run_audit()
        results["violations"] = violations

        return results
