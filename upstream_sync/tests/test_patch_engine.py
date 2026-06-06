# tests/upstream_sync/test_patch_engine.py
"""Tests for core/patch_engine.py and adapters."""

import pytest
from pathlib import Path

from upstream_sync.config import PatchConfig
from upstream_sync.core.patch_engine import create_engine, ApplyResult, PatchEngine


@pytest.fixture
def quilt_cfg(tmp_path) -> PatchConfig:
    return PatchConfig(
        directory=tmp_path / "patches",
        engine="quilt",
    )


@pytest.fixture
def git_am_cfg(tmp_path) -> PatchConfig:
    return PatchConfig(
        directory=tmp_path / "patches",
        engine="git-am",
    )


@pytest.fixture
def custom_cfg(tmp_path) -> PatchConfig:
    return PatchConfig(
        directory=tmp_path / "patches",
        engine="custom",
        custom_command="/usr/local/bin/my-patch-manager",
    )


class TestApplyResult:
    def test_default_fields_are_empty_lists(self):
        result = ApplyResult()
        assert result.success == []
        assert result.failed == []
        assert result.needs_review == []


class TestCreateEngine:
    def test_quilt_engine_created(self, quilt_cfg):
        engine = create_engine(quilt_cfg)
        assert isinstance(engine, PatchEngine)

    def test_git_am_engine_created(self, git_am_cfg):
        engine = create_engine(git_am_cfg)
        assert isinstance(engine, PatchEngine)

    def test_custom_engine_requires_custom_command(self, custom_cfg):
        engine = create_engine(custom_cfg)
        assert isinstance(engine, PatchEngine)

    def test_raises_on_unknown_engine(self, tmp_path):
        cfg = PatchConfig(directory=tmp_path / "p", engine="unknown")
        with pytest.raises(ValueError, match="Unknown patch engine"):
            create_engine(cfg)


class TestQuiltEngine:
    def test_apply_all(self):
        pass

    def test_pop_all(self):
        pass

    def test_refresh(self):
        pass

    def test_status(self):
        pass


class TestGitAmEngine:
    def test_apply_all(self):
        pass

    def test_pop_all(self):
        pass

    def test_refresh_raises(self):
        pass

    def test_status(self):
        pass


class TestCustomEngine:
    def test_apply_all(self):
        pass

    def test_pop_all(self):
        pass

    def test_refresh(self):
        pass

    def test_status(self):
        pass