# upstream_sync/core/patch_generator.py
"""Generate new patches by analyzing upstream diff and old patches.

This module provides the logic to generate new patches for an upstream commit
(456def) by understanding the transformation patterns from old patches (123abc).

Path Convention
==============
All patches are generated with paths relative to the upstream source_subpath
(e.g. "src"). The patch diff header uses paths like:
    diff --git a/bridge/__init__.py b/bridge/__init__.py
NOT:
    diff --git a/src/bridge/__init__.py b/src/bridge/__init__.py

This means patches are applied directly to the extracted upstream source
at src/upstream/{commit_id}/, where the extracted directory already contains
the source_subpath contents (e.g., src/upstream/68dc3c5/bridge/__init__.py).

When comparing two upstream commits (old vs new), the source_subpath prefix
is stripped from all paths so patches use consistent, subpath-relative paths.
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from upstream_sync.config import ProjectConfig, PatchConfig


@dataclass
class PatchDiff:
    """Represents the diff between two versions of a file."""
    path: str
    old_version: str
    new_version: str
    is_new: bool = False
    is_deleted: bool = False


@dataclass
class GeneratedPatch:
    """A generated patch with metadata."""
    filename: str
    content: str
    source_file: str
    patch_type: str  # 'modify' | 'add' | 'delete'


class PatchGenerator:
    """Generates patches for new upstream commits based on old patch patterns."""

    def __init__(self, repo_root: Path, config: ProjectConfig) -> None:
        self.repo_root = repo_root
        self.cfg = config

    def generate_patches(
        self,
        new_commit: str,
        old_commit: str,
        patch_subdir: Path,
    ) -> list[GeneratedPatch]:
        """Generate patches for new_commit based on old_commit patches.

        Args:
            new_commit: The new upstream commit hash.
            old_commit: The old upstream commit hash to reference.
            patch_subdir: Directory to write generated patches to.

        Returns:
            List of GeneratedPatch objects.
        """
        # 1. Get diff between old and new upstream commits
        upstream_diff = self._get_upstream_diff(old_commit, new_commit)
        if not upstream_diff:
            return []

        # 2. Analyze old patches to understand transformation patterns
        old_patches_dir = self._resolve_patch_dir(old_commit)
        old_patch_patterns = self._analyze_old_patches(old_patches_dir)

        # 3. Generate new patches
        generated = []
        patch_subdir.mkdir(parents=True, exist_ok=True)

        for diff in upstream_diff:
            if diff.is_deleted:
                continue

            # Check if this file was modified in old patches
            if diff.path in old_patch_patterns:
                pattern = old_patch_patterns[diff.path]
                new_patch_content = self._transform_patch(
                    diff, pattern, old_commit, new_commit
                )
            else:
                # For new files, create a simple patch
                new_patch_content = self._create_simple_patch(diff, new_commit)

            if new_patch_content:
                filename = self._generate_patch_filename(diff, new_commit)
                patch_path = patch_subdir / filename
                patch_path.write_text(new_patch_content, encoding="utf-8")
                generated.append(GeneratedPatch(
                    filename=filename,
                    content=new_patch_content,
                    source_file=diff.path,
                    patch_type='add' if diff.is_new else 'modify',
                ))

        return generated

    def _get_upstream_diff(self, old_commit: str, new_commit: str) -> list[PatchDiff]:
        """Get file diffs between two upstream commits.

        All returned paths are relative to source_subpath (e.g. "src").
        The source_subpath prefix is stripped from paths, so a file like
        "src/bridge/__init__.py" becomes "bridge/__init__.py" in the diff.
        """
        result = subprocess.run(
            ["git", "diff", f"{old_commit}..{new_commit}", "--", self.cfg.upstream.source_subpath],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return []

        diffs = []
        current_file = None
        old_lines = []
        new_lines = []
        is_new = False
        is_deleted = False

        for line in result.stdout.splitlines():
            if line.startswith("diff --git"):
                # Save previous file diff
                if current_file:
                    diffs.append(PatchDiff(
                        path=current_file,
                        old_version="\n".join(old_lines),
                        new_version="\n".join(new_lines),
                        is_new=is_new,
                        is_deleted=is_deleted,
                    ))
                # Parse new file path from "b/<path>" part
                # e.g., "diff --git a/src/bridge/__init__.py b/src/bridge/__init__.py"
                parts = line.split(" b/")
                if len(parts) == 2:
                    raw_path = parts[1].split(" ")[0] if " " in parts[1] else parts[1]
                    # Strip source_subpath prefix so paths are relative to extracted upstream root
                    # e.g., "src/bridge/__init__.py" -> "bridge/__init__.py"
                    subpath = self.cfg.upstream.source_subpath
                    if raw_path.startswith(f"{subpath}/"):
                        current_file = raw_path[len(subpath)+1:]
                    else:
                        current_file = raw_path
                old_lines = []
                new_lines = []
                is_new = "new file mode" in line
                is_deleted = "deleted file mode" in line
            elif line.startswith("+") and not line.startswith("+++"):
                new_lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                old_lines.append(line[1:])
            elif line.startswith("@@"):
                # Reset for hunk header
                old_lines = []
                new_lines = []

        # Save last file
        if current_file:
            diffs.append(PatchDiff(
                path=current_file,
                old_version="\n".join(old_lines),
                new_version="\n".join(new_lines),
                is_new=is_new,
                is_deleted=is_deleted,
            ))

        return diffs

    def _analyze_old_patches(self, old_patches_dir: Path) -> dict[str, str]:
        """Analyze old patches to understand transformation patterns.

        Returns a dict mapping file paths (relative to source_subpath) to their
        patch content. Paths in patches have source_subpath prefix stripped, so
        a patch for "src/bridge/__init__.py" is stored under key "bridge/__init__.py".
        """
        patterns = {}
        if not old_patches_dir.exists():
            return patterns

        subpath = self.cfg.upstream.source_subpath
        for patch_file in old_patches_dir.glob("*.patch"):
            content = patch_file.read_text(encoding="utf-8")
            # Extract the source file from patch header
            # Format: "diff --git a/bridge/__init__.py b/bridge/__init__.py"
            # or: "--- a/src/bridge/__init__.py"
            for line in content.splitlines():
                if line.startswith("diff --git"):
                    # Extract "b/<path>" or "a/<path>"
                    if " b/" in line:
                        src = line.split(" b/")[1].split(" ")[0]
                    elif " a/" in line:
                        src = line.split(" a/")[1].split(" ")[0]
                    else:
                        continue
                    # Strip source_subpath prefix if present
                    if src.startswith(f"{subpath}/"):
                        src = src[len(subpath)+1:]
                    patterns[src] = content
                    break
                elif line.startswith("--- a/") or line.startswith("+++ b/"):
                    # Extract path after a/ or b/
                    prefix = "--- a/" if line.startswith("--- a/") else "+++ b/"
                    src = line[len(prefix):].split(" ")[0]
                    # Strip source_subpath prefix if present
                    if src.startswith(f"{subpath}/"):
                        src = src[len(subpath)+1:]
                    patterns[src] = content
                    break

        return patterns

    def _transform_patch(
        self,
        diff: PatchDiff,
        pattern: str,
        old_commit: str,
        new_commit: str,
    ) -> str | None:
        """Transform an old patch pattern to match new upstream changes."""
        if not pattern:
            return None

        # Paths in patches are already relative to source_subpath root,
        # so we generate new patches with simple a/<path> b/<path> format.
        return self._create_unified_patch(diff, new_commit)

    def _create_simple_patch(self, diff: PatchDiff, commit: str) -> str:
        """Create a simple patch for a file change."""
        return self._create_unified_patch(diff, commit)

    def _create_unified_patch(self, diff: PatchDiff, commit: str) -> str:
        """Create a unified diff patch with source_subpath-relative paths.

        Paths in the patch header use the format:
            diff --git a/bridge/__init__.py b/bridge/__init__.py
        NOT:
            diff --git a/src/bridge/__init__.py b/src/bridge/__init__.py

        This convention allows the patch to be applied directly to the
        extracted upstream source at src/upstream/{commit_id}/, where the
        directory already contains the source_subpath contents.
        """
        old_path = f"a/{diff.path}"
        new_path = f"b/{diff.path}"

        # Generate diff using git diff for proper format
        if not diff.is_new and not diff.is_deleted:
            # For modifications, use git diff
            result = subprocess.run(
                ["git", "diff", f"upstream/{self.cfg.upstream.main_branch}~1",
                 f"upstream/{self.cfg.upstream.main_branch}", "--", diff.path],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )
            if result.stdout:
                # Normalize paths: strip source_subpath prefix from the diff output
                output = result.stdout
                subpath = self.cfg.upstream.source_subpath
                output = output.replace(f"a/{subpath}/", "a/")
                output = output.replace(f"b/{subpath}/", "b/")
                return output

        # Fallback: create manual unified diff
        lines = []
        lines.append(f"diff --git {old_path} {new_path}")
        if diff.is_new:
            lines.append(f"new file mode 100644")
        lines.append(f"--- {old_path}")
        lines.append(f"+++ {new_path}")
        lines.append(f"@@ -0,0 +1,{len(diff.new_version.splitlines())} @@")

        for line in diff.new_version.splitlines():
            lines.append(f"+{line}")

        return "\n".join(lines)

    def _generate_patch_filename(self, diff: PatchDiff, commit: str) -> str:
        """Generate a patch filename based on the file path and commit."""
        # Format: XXXX.{path}.{ext}.patch
        path_parts = diff.path.replace("/", ".").replace("_", ".")
        return f"0001.{path_parts}.patch"

    def _resolve_patch_dir(self, commit: str) -> Path:
        """Resolve the patch directory for a given commit."""
        if self.cfg.patches.patch_subdir:
            return Path(
                str(self.cfg.patches.patch_subdir).format(commit=commit)
            )
        return self.cfg.patches.directory

    def create_series_file(self, patches: list[GeneratedPatch], output_path: Path) -> None:
        """Create a series file for the generated patches."""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, patch in enumerate(patches, 1):
                f.write(f"{patch.filename}\n")