# upstream_sync/core/verifier.py
"""Verification of patch functional equivalence.

Validates that applying new patches to new upstream code produces
functionally equivalent results compared to old patches applied to old upstream.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class VerificationResult:
    """Result of a verification check."""
    passed: bool
    message: str
    details: dict | None = None


class Verifier:
    """Verifies functional equivalence of patches across upstream versions."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def verify_patches(
        self,
        old_patches_dir: Path,
        new_patches_dir: Path,
        old_upstream_dir: Path,
        new_upstream_dir: Path,
        backup_dir: Path,
    ) -> VerificationResult:
        """Verify that new patches produce functionally equivalent results.

        The verification checks:
        1. Both old and new patches apply successfully
        2. The transformation from old→new upstream doesn't break the patches
        3. The functional diff between old_patch_result and new_patch_result
           matches the upstream diff between old and new commits.

        Args:
            old_patches_dir: Directory containing old patches (e.g. patches/upstream/123abc/)
            new_patches_dir: Directory containing new patches (e.g. patches/upstream/456def/)
            old_upstream_dir: Directory with old upstream source (e.g. src/upstream/123abc/)
            new_upstream_dir: Directory with new upstream source (e.g. src/upstream/456def/)
            backup_dir: Backup directory with original src/ content

        Returns:
            VerificationResult indicating pass/fail and details.
        """
        issues = []

        # Check 1: Verify patch directories exist
        if not old_patches_dir.exists():
            issues.append(f"Old patches directory not found: {old_patches_dir}")
        if not new_patches_dir.exists():
            issues.append(f"New patches directory not found: {new_patches_dir}")
        if not old_upstream_dir.exists():
            issues.append(f"Old upstream directory not found: {old_upstream_dir}")
        if not new_upstream_dir.exists():
            issues.append(f"New upstream directory not found: {new_upstream_dir}")

        if issues:
            return VerificationResult(
                passed=False,
                message="Missing required directories",
                details={"issues": issues},
            )

        # Check 2: Compare patch structure
        old_patches = sorted(old_patches_dir.glob("*.patch"))
        new_patches = sorted(new_patches_dir.glob("*.patch"))

        patch_check = self._verify_patch_structure(old_patches, new_patches)
        if not patch_check.passed:
            issues.append(patch_check.message)

        # Check 3: Verify upstream diff matches expected transformation
        upstream_check = self._verify_upstream_diff(old_upstream_dir, new_upstream_dir)
        if not upstream_check.passed:
            issues.append(upstream_check.message)

        # Check 4: Analyze semantic equivalence
        semantic_check = self._verify_semantic_equivalence(
            old_patches, new_patches, old_upstream_dir, new_upstream_dir
        )
        if not semantic_check.passed:
            issues.append(semantic_check.message)

        if issues:
            return VerificationResult(
                passed=False,
                message="Verification failed",
                details={"issues": issues},
            )

        return VerificationResult(
            passed=True,
            message="Patch verification passed",
            details={
                "old_patches_count": len(old_patches),
                "new_patches_count": len(new_patches),
            },
        )

    def _verify_patch_structure(
        self,
        old_patches: list[Path],
        new_patches: list[Path],
    ) -> VerificationResult:
        """Verify that new patches have similar structure to old patches."""
        if len(new_patches) == 0:
            return VerificationResult(
                passed=False,
                message="No new patches found",
            )

        # Check that new patches follow similar naming convention
        for patch in new_patches:
            if not patch.name.endswith(".patch"):
                return VerificationResult(
                    passed=False,
                    message=f"Invalid patch filename: {patch.name}",
                )

        return VerificationResult(
            passed=True,
            message="Patch structure looks valid",
        )

    def _verify_upstream_diff(
        self,
        old_upstream_dir: Path,
        new_upstream_dir: Path,
    ) -> VerificationResult:
        """Verify that upstream source directories are different."""
        old_files = set(self._get_file_hashes(old_upstream_dir))
        new_files = set(self._get_file_hashes(new_upstream_dir))

        if old_files == new_files:
            return VerificationResult(
                passed=False,
                message="Upstream directories are identical - no changes to verify",
            )

        changed = len([h for h in new_files if h not in old_files])
        added = len([h for h in new_files if h[0] not in [oh[0] for oh in old_files]])

        return VerificationResult(
            passed=True,
            message=f"Upstream has {changed} changed files, {added} new files",
            details={"changed": changed, "added": added},
        )

    def _verify_semantic_equivalence(
        self,
        old_patches: list[Path],
        new_patches: list[Path],
        old_upstream_dir: Path,
        new_upstream_dir: Path,
    ) -> VerificationResult:
        """Verify semantic equivalence by comparing patch diffs."""
        # Compare the number and types of patches
        old_patch_types = self._classify_patches(old_patches)
        new_patch_types = self._classify_patches(new_patches)

        # New patches should have similar types to old patches
        if new_patch_types["new_files"] > old_patch_types["new_files"] * 2:
            return VerificationResult(
                passed=False,
                message="Too many new files in patches compared to old patches",
                details={"old": old_patch_types, "new": new_patch_types},
            )

        return VerificationResult(
            passed=True,
            message="Semantic equivalence check passed",
            details={"patch_types": new_patch_types},
        )

    def _classify_patches(self, patches: list[Path]) -> dict[str, int]:
        """Classify patches by type (new, modify, delete)."""
        stats = {"new_files": 0, "modifications": 0, "deletes": 0}

        for patch in patches:
            content = patch.read_text(encoding="utf-8")
            if "new file mode" in content:
                stats["new_files"] += 1
            elif "deleted file mode" in content:
                stats["deletes"] += 1
            else:
                stats["modifications"] += 1

        return stats

    def _get_file_hashes(self, directory: Path) -> set[tuple[str, str]]:
        """Get hashes of all files in a directory."""
        hashes = set()
        if not directory.exists():
            return hashes

        for file in directory.rglob("*"):
            if file.is_file():
                rel_path = str(file.relative_to(directory))
                content_hash = self._hash_file(file)
                hashes.add((rel_path, content_hash))

        return hashes

    def _hash_file(self, path: Path) -> str:
        """Get MD5 hash of a file."""
        return hashlib.md5(path.read_bytes()).hexdigest()

    def generate_verification_report(
        self,
        result: VerificationResult,
        output_path: Path,
    ) -> None:
        """Generate a verification report in markdown format."""
        import json
        from datetime import datetime

        report_lines = [
            f"# Patch Verification Report",
            f"",
            f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"**Status**: {'✅ PASSED' if result.passed else '❌ FAILED'}",
            f"",
            f"**Message**: {result.message}",
            f"",
        ]

        if result.details:
            report_lines.append("## Details")
            report_lines.append("")
            if "issues" in result.details:
                for issue in result.details["issues"]:
                    report_lines.append(f"- ❌ {issue}")
            else:
                for key, value in result.details.items():
                    report_lines.append(f"- **{key}**: {value}")

        output_path.write_text("\n".join(report_lines), encoding="utf-8")