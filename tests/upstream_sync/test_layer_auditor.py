# tests/upstream_sync/test_layer_auditor.py
"""Tests for core/layer_auditor.py."""

import pytest
from pathlib import Path

from upstream_sync.config import ProjectConfig, UpstreamConfig, PatchConfig, SyncConfig, LayerConfig
from upstream_sync.core.layer_auditor import LayerAuditor, Violation


@pytest.fixture
def project_cfg(tmp_path) -> ProjectConfig:
    src_upstream = tmp_path / "src" / "upstream"
    src_upstream.mkdir(parents=True)
    src_capabilities = tmp_path / "src" / "capabilities"
    src_capabilities.mkdir(parents=True)

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
                paths=[src_upstream],
                forbidden_imports_from=[],
            ),
            LayerConfig(
                name="capabilities",
                paths=[src_capabilities],
                forbidden_imports_from=["src.upstream"],
            ),
        ],
        patches=PatchConfig(directory=tmp_path / "patches"),
        sync=SyncConfig(),
    )


@pytest.fixture
def auditor(project_cfg) -> LayerAuditor:
    return LayerAuditor(config=project_cfg)


class TestViolation:
    def test_repr(self, tmp_path):
        v = Violation(file=tmp_path / "foo.py", forbidden_import="src.upstream", layer="capabilities", line_number=3)
        assert "foo.py" in repr(v)
        assert "capabilities" in repr(v)


class TestLayerAuditor:
    def test_audit_no_violations_clean_tree(self, auditor, tmp_path):
        (tmp_path / "src" / "capabilities" / "clean.py").write_text("import os\n", encoding="utf-8")
        violations = auditor.audit()
        assert violations == []

    def test_audit_detects_forbidden_import(self, auditor, tmp_path):
        (tmp_path / "src" / "capabilities" / "bad.py").write_text(
            "from src.upstream import bridge\n", encoding="utf-8"
        )
        violations = auditor.audit()
        assert len(violations) == 1
        assert violations[0].forbidden_import == "src.upstream"
        assert violations[0].layer == "capabilities"

    def test_extract_imports(self, auditor, tmp_path):
        py_file = tmp_path / "imp.py"
        py_file.write_text("import os\nfrom sys import path\n", encoding="utf-8")
        imports = auditor._extract_imports(py_file)
        assert ("os", 1) in imports
        assert ("sys", 2) in imports

    def test_is_forbidden(self, auditor):
        layer = auditor.layers[1]  # capabilities layer
        assert auditor._is_forbidden("src.upstream.bridge", layer) is True
        assert auditor._is_forbidden("src.capabilities.base", layer) is False

    def test_report_empty(self, auditor):
        assert "No layer violations" in auditor.report([])