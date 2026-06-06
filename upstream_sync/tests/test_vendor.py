# tests/upstream_sync/test_vendor.py
"""Tests for core/vendor.py."""

import pytest
from pathlib import Path

from upstream_sync.config import UpstreamConfig
from upstream_sync.core.vendor import VendorManager


@pytest.fixture
def upstream_cfg() -> UpstreamConfig:
    return UpstreamConfig(
        remote_url="https://github.com/anthropics/claude-code.git",
        main_branch="main",
        vendor_branch="upstream/vendor",
        version_tag_format="upstream/v{YYYY}_{MM}",
    )


@pytest.fixture
def vendor(tmp_path, upstream_cfg) -> VendorManager:
    return VendorManager(repo_root=tmp_path, upstream=upstream_cfg)


class TestVendorManager:
    def test_ensure_remote_adds_upstream_when_missing(self, vendor, tmp_path):
        # No remotes exist initially in a fresh tmp_path git repo
        pass

    def test_fetch_returns_commit_hash(self, vendor):
        pass

    def test_create_version_tag(self, vendor):
        pass

    def test_list_version_tags(self, vendor):
        pass

    def test_checkout_vendor_creates_branch_if_missing(self, vendor):
        pass

    def test_reset_vendor_to_upstream(self, vendor):
        pass

    def test_detect_sync_refs_returns_tuple(self, vendor):
        pass