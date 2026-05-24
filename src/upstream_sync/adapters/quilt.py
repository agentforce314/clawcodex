# upstream_sync/adapters/quilt.py
"""Quilt patch engine adapter.

Wraps the ``quilt`` CLI to provide push/pop/refresh/status operations.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from upstream_sync.core.patch_engine import ApplyResult, PatchEngine


def _run_quilt(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["quilt", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class QuiltEngine:
    """PatchEngine implementation backed by ``quilt``."""

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        """Push all patches in the series file.

        Args:
            patch_dir: Directory containing patch files.
            series_file: Quilt series file listing patch order.

        Returns:
            Structured result with success / failed / needs-review lists.
        """
        result = _run_quilt(["push", "-a"], cwd=patch_dir.parent)
        stdout, stderr = result.stdout, result.stderr
        lines = (stdout + "\n" + stderr).splitlines()

        success: list[str] = []
        failed: list[tuple[str, str]] = []
        needs_review: list[str] = []

        for line in lines:
            # quilt push: Applied patch xxx.patch
            m = re.match(r"Applied patch ([\w\-\./]+)", line)
            if m:
                success.append(m.group(1))
            # quilt push: Refusing to apply patch xxx.patch (already applied)
            m = re.match(r"Refusing to apply patch ([\w\-\./]+) \(already applied\)", line)
            if m:
                success.append(m.group(1))
            # quilt push: Can't re-apply patch xxx.patch -- already overlaps "yyy.patch"
            m = re.match(r"Can't re-apply patch ([\w\-\./]+)", line)
            if m:
                needs_review.append(m.group(1))

        if result.returncode != 0:
            # Try to detect failed patches from output
            for line in lines:
                m = re.match(r"Patch ([\w\-\./]+) does not apply to ([\w\-\./]+)", line)
                if m:
                    failed.append((m.group(1), f"does not apply: {m.group(2)}"))

        return ApplyResult(success=success, failed=failed, needs_review=needs_review)

    def pop_all(self) -> None:
        """Pop all applied patches."""
        _run_quilt(["pop", "-a"], cwd=Path("."))
        # quilt pop -a exits non-zero when there are no applied patches — not an error

    def refresh(self, patch_name: str) -> None:
        """Refresh a single patch to match current working-tree changes."""
        _run_quilt(["refresh", patch_name], cwd=Path("."))

    def status(self) -> dict:
        """Return current quilt status (applied / unapplied patches)."""
        result = _run_quilt(["applied"], cwd=Path("."))
        applied = []
        if result.returncode == 0 and result.stdout.strip():
            applied = [p.strip() for p in result.stdout.strip().splitlines() if p.strip()]

        result = _run_quilt(["unapplied"], cwd=Path("."))
        unapplied = []
        if result.returncode == 0 and result.stdout.strip():
            unapplied = [p.strip() for p in result.stdout.strip().splitlines() if p.strip()]

        return {"applied": applied, "unapplied": unapplied}
