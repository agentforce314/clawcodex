# upstream_sync/core/change_analyzer.py
"""Upstream diff analysis and impact assessment.

Compares two upstream references and produces a structured ``ChangeReport``
that downstream consumers (reporters, agents, CI) can act on.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from upstream_sync.config import ProjectConfig


@dataclass
class ModuleImpact:
    """Impact assessment for a single layer / module."""

    module_name: str
    layer_name: str
    files_changed: list[str] = field(default_factory=list)
    patches_affected: list[str] = field(default_factory=list)
    conflict_probability: str = "low"      # low | medium | high
    estimated_effort_minutes: int = 0
    recommended_strategy: str = "fast-forward"  # fast-forward | rebase-patches | human-review


@dataclass
class ChangeReport:
    """Structured output of the upstream change analysis."""

    upstream_version: str
    previous_version: str
    overall_impact: str = "low"            # low | medium | high
    statistics: dict = field(default_factory=dict)
    module_impacts: list[ModuleImpact] = field(default_factory=list)
    action_items: list[dict] = field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self, indent=indent, default=lambda o: o.__dict__)


class ChangeAnalyzer:
    """Analyses upstream changes between two Git refs."""

    def __init__(self, repo_root: Path, config: ProjectConfig) -> None:
        self.repo_root = repo_root
        self.cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, from_ref: str, to_ref: str) -> ChangeReport:
        """Compare *from_ref* and *to_ref* and return a structured report."""
        # 1. Obtain diff stat between the refs
        result = subprocess.run(
            ["git", "diff", "--stat", f"{from_ref}..{to_ref}"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        diff_stat = result.stdout

        # 2. Parse changed file list
        changed_files = self._parse_changed_files(diff_stat)

        # 3. Map files to configured layers
        module_impacts: list[ModuleImpact] = []
        for layer in self.cfg.layers:
            layer_files = [
                f for f in changed_files
                if any(str(f).startswith(str(p)) for p in layer.paths)
            ]
            if layer_files:
                affected_patches = self._find_affected_patches(layer_files)
                conflict_prob = self._assess_conflict_probability(layer_files, affected_patches)
                module_impacts.append(ModuleImpact(
                    module_name=layer.name,
                    layer_name=layer.name,
                    files_changed=layer_files,
                    patches_affected=affected_patches,
                    conflict_probability=conflict_prob,
                    estimated_effort_minutes=self._estimate_effort(conflict_prob, len(layer_files)),
                    recommended_strategy=self._recommend_strategy(conflict_prob),
                ))

        # 4. Assess overall impact
        overall = self._calculate_overall_impact(module_impacts)

        return ChangeReport(
            upstream_version=to_ref,
            previous_version=from_ref,
            overall_impact=overall,
            statistics={
                "files_changed_upstream": len(changed_files),
                "modules_affected": len(module_impacts),
            },
            module_impacts=module_impacts,
            action_items=self._generate_action_items(module_impacts),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_changed_files(self, diff_stat: str) -> list[str]:
        """Extract filenames from ``git diff --stat`` output."""
        files: list[str] = []
        for line in diff_stat.strip().splitlines():
            # Lines look like: "src/foo/bar.py |  42 ++------------------"
            # or "src/foo/bar.py (new file) |  10 +"
            m = re.match(r"^([^\|]+?)(?:\s*\(new file\))?\s*\|", line)
            if m:
                filename = m.group(1).strip()
                if filename:
                    files.append(filename)
        return files

    def _find_affected_patches(self, files: list[str]) -> list[str]:
        """Cross-reference changed files against patch metadata."""
        affected: list[str] = []
        meta_dir = self.cfg.patches.metadata_dir
        if not meta_dir.exists():
            return affected

        for meta_file in meta_dir.glob("*.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for mod in data.get("affected_modules", []):
                if any(f.startswith(mod) for f in files):
                    affected.append(data.get("id", meta_file.stem))
                    break
        return affected

    def _assess_conflict_probability(self, files: list[str], patches: list[str]) -> str:
        """Heuristic: low / medium / high conflict probability."""
        if len(patches) > 0 and len(files) > 3:
            return "high"
        elif len(patches) > 0:
            return "medium"
        return "low"

    def _estimate_effort(self, prob: str, file_count: int) -> int:
        """Rough effort estimate in minutes."""
        base = {"low": 5, "medium": 20, "high": 45}
        return base.get(prob, 30) + file_count * 2

    def _recommend_strategy(self, prob: str) -> str:
        """Recommend resolution strategy based on conflict probability."""
        return {
            "low": "fast-forward",
            "medium": "rebase-patches",
            "high": "human-review",
        }.get(prob, "human-review")

    def _calculate_overall_impact(self, impacts: list[ModuleImpact]) -> str:
        """Aggregate individual impacts into an overall rating."""
        if any(i.conflict_probability == "high" for i in impacts):
            return "high"
        if any(i.conflict_probability == "medium" for i in impacts):
            return "medium"
        return "low"

    def _generate_action_items(self, impacts: list[ModuleImpact]) -> list[dict]:
        """Produce actionable items for downstream consumers."""
        items: list[dict] = []
        for imp in impacts:
            if imp.conflict_probability == "high":
                items.append({
                    "module": imp.module_name,
                    "action": "human-review",
                    "reason": f"High conflict probability with patches: {imp.patches_affected}",
                })
            elif imp.patches_affected:
                items.append({
                    "module": imp.module_name,
                    "action": "review-patches",
                    "reason": f"Affected patches: {imp.patches_affected}",
                })
        return items
