# upstream_sync/adapters/git_am.py
"""Git-am patch engine adapter.

Applies ``*.patch`` files using ``git am``.  Useful for projects that prefer
git-native patch workflows over quilt.

Supports both flat directory structure and per-commit subdirectory structure.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from upstream_sync.core.patch_engine import ApplyResult, PatchEngine


class GitAmEngine:
    """PatchEngine implementation backed by ``git am``."""

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        """Apply all ``*.patch`` files via ``git am --3way``.

        If *series_file* exists and is readable, patches are applied in the
        order listed in the series file.  Otherwise, falls back to globbing
        ``*.patch`` from *patch_dir* in sorted order.

        Args:
            patch_dir: Directory containing ``*.patch`` files.
            series_file: Optional quilt-style series file listing patch order.

        Returns:
            Structured result with success / failed / needs-review lists.
        """
        # Read series file if it exists
        patches: list[Path] = []
        if series_file.exists():
            # Quilt series file: one patch filename per line
            # Lines starting with # are comments, empty lines are ignored
            for line in series_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patch_path = patch_dir / line
                if patch_path.exists():
                    patches.append(patch_path)
                else:
                    # Try relative to parent (backward compatibility)
                    patches.append(patch_path)
        else:
            # Fallback: glob patches from patch_dir
            patches = sorted(patch_dir.glob("*.patch"))

        if not patches:
            return ApplyResult()

        result = subprocess.run(
            ["git", "am", "--3way"] + [str(p) for p in patches],
            cwd=patch_dir.parent,
            capture_output=True,
            text=True,
        )
        stdout, stderr = result.stdout, result.stderr
        lines = (stdout + "\n" + stderr).splitlines()

        success: list[str] = []
        failed: list[tuple[str, str]] = []
        needs_review: list[str] = []

        for line in lines:
            # "Applying: <patch name>"
            m = re.match(r"Applying: ([\w\-\./]+)", line)
            if m:
                success.append(m.group(1))
            # "Patch failed at: <patch name>"
            m = re.match(r"Patch failed at: ([\w\-\./]+)", line)
            if m:
                failed.append((m.group(1), "patch failed to apply"))
            # "You are in the middle of an am session"
            if "You are in the middle of an am session" in line:
                needs_review.append("in-progress-am-session")

        if result.returncode != 0 and not failed:
            # Fallback: try to extract failed patch name from conflict
            for line in lines:
                m = re.match(r"error: patch failed: ([\w\-\./]+):", line)
                if m:
                    failed.append((m.group(1), "patch failed to apply"))

        return ApplyResult(success=success, failed=failed, needs_review=needs_review)

    def pop_all(self) -> None:
        """Abort an in-progress ``git am`` session if one exists."""
        subprocess.run(
            ["git", "am", "--abort"],
            cwd=Path("."),
            capture_output=True,
        )

    def refresh(self, patch_name: str) -> None:
        """Raise ``NotImplementedError`` — git-am does not support refresh."""
        raise NotImplementedError("git-am engine does not support refresh")

    def status(self) -> dict:
        """Return git-am state by checking for .git/rebase-apply."""
        import os
        rebase_apply = Path(".git") / "rebase-apply"
        am_style = Path(".git") / "rebase-merge"
        if rebase_apply.exists() or am_style.exists():
            return {"state": "in-progress"}
        return {"state": "clean"}
