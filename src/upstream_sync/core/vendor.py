# upstream_sync/core/vendor.py
"""Vendor branch management.

Mirrors upstream code into a local, read-only vendor branch and maintains
version-lock tags.  Zero business awareness — purely mechanical Git ops.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from upstream_sync.config import UpstreamConfig


class VendorManager:
    """Manages upstream remote, fetch, tags, and the vendor branch."""

    def __init__(self, repo_root: Path, upstream: UpstreamConfig) -> None:
        self.repo_root = repo_root
        self.cfg = upstream

    # ------------------------------------------------------------------
    # Remote lifecycle
    # ------------------------------------------------------------------

    def ensure_remote(self) -> None:
        """Add the upstream remote if it does not already exist."""
        result = subprocess.run(
            ["git", "remote", "get-url", "upstream"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "remote", "add", "upstream", self.cfg.remote_url],
                cwd=self.repo_root,
                check=True,
            )

    def fetch(self) -> str:
        """Fetch upstream main and return the latest commit hash."""
        subprocess.run(
            ["git", "fetch", "upstream", self.cfg.main_branch],
            cwd=self.repo_root,
            check=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", f"upstream/{self.cfg.main_branch}"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    # ------------------------------------------------------------------
    # Version tags
    # ------------------------------------------------------------------

    def create_version_tag(self, version: str, commit: str) -> None:
        """Create a version lock tag (e.g. upstream/v2025_06)."""
        dt = datetime.strptime(version, "%Y.%m.%d")
        tag = self.cfg.version_tag_format.format(
            YYYY=dt.year, MM=f"{dt.month:02d}"
        )
        subprocess.run(
            ["git", "tag", tag, commit],
            cwd=self.repo_root,
            check=True,
        )

    def list_version_tags(self) -> list[str]:
        """Return all locally-created upstream version tags."""
        result = subprocess.run(
            ["git", "tag", "--list", "upstream/*"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        tags = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
        return tags

    # ------------------------------------------------------------------
    # Vendor branch
    # ------------------------------------------------------------------

    def checkout_vendor(self) -> None:
        """Switch to the vendor branch, creating it if necessary."""
        result = subprocess.run(
            ["git", "branch", "--list", self.cfg.vendor_branch],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            subprocess.run(
                ["git", "checkout", "-b", self.cfg.vendor_branch],
                cwd=self.repo_root,
                check=True,
            )
        else:
            subprocess.run(
                ["git", "checkout", self.cfg.vendor_branch],
                cwd=self.repo_root,
                check=True,
            )

    def reset_vendor_to_upstream(self, commit: str | None = None) -> None:
        """Hard-reset vendor branch to the given upstream commit (default: FETCH_HEAD)."""
        ref = commit or f"upstream/{self.cfg.main_branch}"
        subprocess.run(
            ["git", "checkout", self.cfg.vendor_branch],
            cwd=self.repo_root,
            check=True,
        )
        subprocess.run(
            ["git", "reset", "--hard", ref],
            cwd=self.repo_root,
            check=True,
        )

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    def detect_sync_refs(self) -> tuple[str, str]:
        """Detect the previous and latest upstream refs for sync.

        Returns:
            A tuple ``(previous_ref, latest_ref)`` where:
            - *previous_ref* is the newest local ``upstream/v*`` tag,
              or ``upstream/vendor`` branch tip if no tags exist.
            - *latest_ref* is ``upstream/<main_branch>`` (the current upstream head).

        Raises:
            RuntimeError: If the upstream remote has never been fetched.
        """
        # Ensure we have something to compare against
        upstream_ref = f"upstream/{self.cfg.main_branch}"
        result = subprocess.run(
            ["git", "rev-parse", upstream_ref],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Upstream remote '{upstream_ref}' not found. "
                "Run 'upstream-sync fetch' first."
            )
        latest_ref = upstream_ref

        # Find the newest local upstream/* tag
        tags = self.list_version_tags()
        if tags:
            # Sort tags lexicographically (tags follow upstream/vYYYY_MM format)
            latest_tag = sorted(tags)[-1]
            previous_ref = latest_tag
        else:
            # Fall back to vendor branch
            vb = self.cfg.vendor_branch
            result = subprocess.run(
                ["git", "rev-parse", vb],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                previous_ref = vb
            else:
                # No tags and no vendor branch — use upstream main as both
                # (first sync will show all files as new)
                previous_ref = latest_ref

        return previous_ref, latest_ref
