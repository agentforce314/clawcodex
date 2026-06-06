# tests/upstream_sync/test_change_analyzer.py
"""Tests for core/change_analyzer.py."""

import pytest
from pathlib import Path

from upstream_sync.config import ProjectConfig, UpstreamConfig, PatchConfig, SyncConfig, LayerConfig
from upstream_sync.core.change_analyzer import ChangeAnalyzer, ChangeReport, ModuleImpact


@pytest.fixture
def project_cfg(tmp_path) -> ProjectConfig:
    return ProjectConfig(
        project_name="test-project",
        source_lang="python",
        upstream=UpstreamConfig(
            remote_url="https://github.com/example/repo.git",
            main_branch="main",
            vendor_branch="upstream/vendor",
            version_tag_format="upstream/v{YYYY}_{MM}",
        ),
        layers=[
            LayerConfig(
                name="upstream",
                paths=[tmp_path / "src" / "upstream"],
                forbidden_imports_from=[],
            ),
        ],
        patches=PatchConfig(
            directory=tmp_path / "patches",
            engine="quilt",
            metadata_dir=tmp_path / "patches" / "metadata",
        ),
        sync=SyncConfig(),
    )


@pytest.fixture
def analyzer(tmp_path, project_cfg) -> ChangeAnalyzer:
    return ChangeAnalyzer(repo_root=tmp_path, config=project_cfg)


class TestModuleImpact:
    def test_defaults(self):
        imp = ModuleImpact(module_name="test", layer_name="test")
        assert imp.conflict_probability == "low"
        assert imp.estimated_effort_minutes == 0
        assert imp.recommended_strategy == "fast-forward"
        assert imp.files_changed == []
        assert imp.patches_affected == []


class TestChangeReport:
    def test_to_json(self):
        report = ChangeReport(
            upstream_version="upstream/v2025_05",
            previous_version="upstream/v2025_04",
            overall_impact="medium",
            statistics={"files_changed_upstream": 12, "modules_affected": 2},
            module_impacts=[],
            action_items=[{"module": "foo", "action": "review-patches", "reason": "patches affected"}],
        )
        json_str = report.to_json()
        assert "upstream/v2025_05" in json_str
        assert "medium" in json_str


class TestChangeAnalyzer:
    def test_analyze(self, analyzer):
        pass  # requires git repo with commits

    def test_parse_changed_files(self, analyzer):
        pass

    def test_find_affected_patches(self, analyzer):
        pass

    def test_assess_conflict_probability(self, analyzer):
        assert analyzer._assess_conflict_probability(["a.py"], []) == "low"
        assert analyzer._assess_conflict_probability(["a.py", "b.py"], ["patch1"]) == "medium"
        assert analyzer._assess_conflict_probability(["a.py", "b.py", "c.py", "d.py"], ["p1"]) == "high"

    def test_estimate_effort(self, analyzer):
        assert analyzer._estimate_effort("low", 5) == 15   # 5 + 5*2
        assert analyzer._estimate_effort("medium", 3) == 26  # 20 + 3*2

    def test_recommend_strategy(self, analyzer):
        assert analyzer._recommend_strategy("low") == "fast-forward"
        assert analyzer._recommend_strategy("medium") == "rebase-patches"
        assert analyzer._recommend_strategy("high") == "human-review"

    def test_calculate_overall_impact(self, analyzer):
        assert analyzer._calculate_overall_impact([]) == "low"