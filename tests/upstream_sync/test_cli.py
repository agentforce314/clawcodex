# tests/upstream_sync/test_cli.py
"""End-to-end CLI tests."""

import pytest
from pathlib import Path

from typer.testing import CliRunner
from upstream_sync.cli import app

runner = CliRunner()


class TestInit:
    def test_init_blank_template(self, tmp_path):
        result = runner.invoke(app, ["init", "--output", str(tmp_path / "us.yaml")])
        assert result.exit_code == 0
        content = (tmp_path / "us.yaml").read_text()
        assert "project_name" in content

    def test_init_python_port_template(self, tmp_path):
        result = runner.invoke(app, ["init", "--template", "python-port", "--output", str(tmp_path / "us.yaml")])
        assert result.exit_code == 0


class TestFetch:
    def test_fetch_requires_git_repo(self, tmp_path):
        result = runner.invoke(app, ["fetch", "--config", str(tmp_path / "us.yaml")])
        # Should fail gracefully on non-git directory
        assert result.exit_code != 0


class TestAudit:
    def test_audit_with_no_layers(self, tmp_path):
        cfg = tmp_path / "us.yaml"
        cfg.write_text('project_name: "t"\nsource_lang: "python"\n'
                       'upstream:\n  remote_url: "x"\n  main_branch: "m"\n'
                       'vendor_branch: "v"\n  version_tag_format: "x"\n'
                       'layers: []\n'
                       'patches:\n  directory: "p"\n  engine: "quilt"\n'
                       'sync:\n  report_formats: []\n', encoding="utf-8")
        result = runner.invoke(app, ["audit", "--config", str(cfg)])
        # Should exit 0 with no layers configured
        assert "No layer violations" in result.stdout


class TestAnalyze:
    def test_analyze_missing_ref(self, tmp_path):
        result = runner.invoke(app, ["analyze", "HEAD~10", "HEAD", "--config", str(tmp_path / "us.yaml")])
        # Should fail because refs don't exist in non-git repo
        assert result.exit_code != 0