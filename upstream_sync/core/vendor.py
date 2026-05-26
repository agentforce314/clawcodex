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

    def fetch_ref(self, ref: str) -> str:
        """Fetch a specific ref (commit, tag, or branch) from upstream.

        Args:
            ref: Specific git ref (commit hash, tag, or branch name).

        Returns:
            The full commit hash that was fetched.
        """
        subprocess.run(
            ["git", "fetch", "upstream", ref],
            cwd=self.repo_root,
            check=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", f"upstream/{ref}"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def extract_to_path(
        self,
        ref: str,
        subpath: str,
        target_path: Path,
        use_archive: bool = True,
    ) -> None:
        """Extract a sub-path from a fetched upstream ref to a target directory.

        Args:
            ref: The upstream ref (commit hash, tag, or branch).
            subpath: Sub-directory within the upstream repo to extract (e.g. "src").
                     The extracted contents are placed DIRECTLY into target_path,
                     NOT into target_path/subpath/. This means target_path should
                     be the destination for the subpath contents themselves.
            target_path: Local directory to extract the sub-path contents into.
                         For example, extract_to_path("abc123", "src", Path("src/upstream/abc123"))
                         extracts upstream/src/* -> src/upstream/abc123/* (NOT src/upstream/abc123/src/*).
            use_archive: If True, use git archive for efficient extraction.
                         If False, use git checkout.
        """
        upstream_ref = f"upstream/{ref}"
        target_path.mkdir(parents=True, exist_ok=True)

        if use_archive:
            import tarfile
            import io
            # Extract the full archive and filter to only the subpath members
            proc = subprocess.run(
                ["git", "archive", "--prefix=", upstream_ref],
                cwd=self.repo_root,
                capture_output=True,
                check=True,
            )
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
                # Filter to only members under the subpath directory
                members = [m for m in tar.getmembers() if m.name.startswith(f"{subpath}/")]
                for member in members:
                    # Strip the subpath/ prefix so contents go directly into target_path
                    # e.g., "src/bridge/__init__.py" -> "bridge/__init__.py"
                    member.name = member.name[len(subpath)+1:]
                    if member.name:
                        tar.extract(member, target_path)
        else:
            # Fallback: checkout to a temp branch and copy
            import tempfile
            import shutil
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_branch = f"tmp-extract-{ref[:8]}"
                subprocess.run(
                    ["git", "checkout", "-b", tmp_branch, upstream_ref],
                    cwd=self.repo_root,
                    check=True,
                )
                src_path = Path(tmpdir) / subpath
                if src_path.exists():
                    # Copy contents directly (not the subpath directory itself)
                    for item in src_path.iterdir():
                        dest = target_path / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)
                subprocess.run(
                    ["git", "checkout", "-"],
                    cwd=self.repo_root,
                )
                subprocess.run(
                    ["git", "branch", "-D", tmp_branch],
                    cwd=self.repo_root,
                )

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
