#!/usr/bin/env python3
"""
Upstream Sync Workflow Script

Orchestrates the complete upstream sync workflow:
1. Extract new upstream commit source to src/upstream/{commit}/
2. Generate new patches based on old patch patterns
3. Backup current src/ (excluding src/upstream/)
4. Sync src/ with new upstream source
5. Apply new patches
6. Verify patch functional equivalence

Usage:
    python scripts/upstream_sync_workflow.py --new-commit 456def --old-commit 123abc
    python scripts/upstream_sync_workflow.py --new-commit 456def --old-commit 123abc --skip-verify
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime

# Add upstream_sync to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from upstream_sync.config import ProjectConfig
from upstream_sync.core.vendor import VendorManager
from upstream_sync.core.patch_generator import PatchGenerator
from upstream_sync.core.backup_manager import BackupManager
from upstream_sync.core.verifier import Verifier
from upstream_sync.core.patch_engine import create_engine


class UpstreamSyncWorkflow:
    """Orchestrates the full upstream sync workflow."""

    def __init__(self, config_path: Path | str = "upstream-sync.yaml") -> None:
        self.config_path = Path(config_path)
        self.cfg = self._load_config()
        self.repo_root = Path(".")

    def _load_config(self) -> ProjectConfig:
        """Load upstream-sync configuration."""
        import yaml
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        return ProjectConfig(**data)

    def run(
        self,
        new_commit: str,
        old_commit: str,
        skip_backup: bool = False,
        skip_verify: bool = False,
        skip_patch_apply: bool = False,
    ) -> dict:
        """Execute the full sync workflow.

        Args:
            new_commit: New upstream commit hash to sync to
            old_commit: Old upstream commit hash to reference
            skip_backup: Skip the backup step
            skip_verify: Skip the verification step
            skip_patch_apply: Skip applying new patches

        Returns:
            Dict with workflow results and status
        """
        results = {
            "status": "running",
            "new_commit": new_commit,
            "old_commit": old_commit,
            "steps": [],
        }

        print("=" * 60)
        print(f"Upstream Sync Workflow")
        print(f"  Old commit: {old_commit}")
        print(f"  New commit: {new_commit}")
        print("=" * 60)

        try:
            # Step 1: Extract new upstream source
            print("\n[1/6] Extracting new upstream source...")
            extract_result = self._extract_upstream(new_commit)
            results["steps"].append({"step": "extract", **extract_result})
            print(f"  ✓ Extracted to {extract_result['output_path']}")

            # Step 2: Generate new patches
            print("\n[2/6] Generating new patches...")
            patch_result = self._generate_patches(new_commit, old_commit)
            results["steps"].append({"step": "generate_patches", **patch_result})
            print(f"  ✓ Generated {patch_result['patch_count']} patches")

            # Step 3: Backup current src/
            if skip_backup:
                print("\n[3/6] Skipping backup (--skip-backup)")
                results["steps"].append({"step": "backup", "skipped": True})
            else:
                print("\n[3/6] Backing up current src/...")
                backup_result = self._backup_src()
                results["steps"].append({"step": "backup", **backup_result})
                print(f"  ✓ Backup created: {backup_result['backup_path']}")

            # Step 4: Sync src/ with new upstream
            print("\n[4/6] Syncing src/ with new upstream source...")
            sync_result = self._sync_src(new_commit)
            results["steps"].append({"step": "sync", **sync_result})
            print(f"  ✓ Synced {sync_result['files_copied']} files")

            # Step 5: Apply new patches
            if skip_patch_apply:
                print("\n[5/6] Skipping patch apply (--skip-patch-apply)")
                results["steps"].append({"step": "apply_patches", "skipped": True})
            else:
                print("\n[5/6] Applying new patches...")
                apply_result = self._apply_patches(new_commit)
                results["steps"].append({"step": "apply_patches", **apply_result})
                print(f"  ✓ Applied: {apply_result['success_count']}, Failed: {apply_result['failed_count']}")

            # Step 6: Verify
            if skip_verify:
                print("\n[6/6] Skipping verification (--skip-verify)")
                results["steps"].append({"step": "verify", "skipped": True})
            else:
                print("\n[6/6] Verifying patch functional equivalence...")
                verify_result = self._verify(new_commit, old_commit)
                results["steps"].append({"step": "verify", **verify_result})
                if verify_result["passed"]:
                    print(f"  ✓ Verification PASSED")
                else:
                    print(f"  ✗ Verification FAILED")
                    for issue in verify_result.get("issues", []):
                        print(f"    - {issue}")

            results["status"] = "completed"
            print("\n" + "=" * 60)
            print("Workflow completed successfully!")
            print("=" * 60)

        except Exception as e:
            results["status"] = "failed"
            results["error"] = str(e)
            print(f"\n✗ Workflow failed: {e}")
            raise

        return results

    def _extract_upstream(self, commit: str) -> dict:
        """Extract upstream commit source to src/upstream/{short_commit}/."""
        vendor = VendorManager(self.repo_root, self.cfg.upstream)
        vendor.ensure_remote()

        # Fetch the ref
        full_commit = vendor.fetch_ref(commit)
        short_commit = full_commit[:8]

        # Determine output path
        output_path = Path("src") / "upstream" / short_commit

        # Extract source subpath
        vendor.extract_to_path(
            ref=commit,
            subpath=self.cfg.upstream.source_subpath,
            target_path=output_path,
        )

        return {
            "commit": full_commit,
            "short_commit": short_commit,
            "output_path": str(output_path),
        }

    def _generate_patches(self, new_commit: str, old_commit: str) -> dict:
        """Generate patches for new_commit based on old_commit patterns."""
        generator = PatchGenerator(self.repo_root, self.cfg)

        # Determine output directory
        if self.cfg.patches.patch_subdir:
            output_dir = Path(str(self.cfg.patches.patch_subdir).format(commit=new_commit))
        else:
            output_dir = self.cfg.patches.directory

        patches = generator.generate_patches(new_commit, old_commit, output_dir)

        if patches:
            # Create series file
            series_file = output_dir / f"{new_commit}_series"
            generator.create_series_file(patches, series_file)

        return {
            "patch_count": len(patches),
            "output_dir": str(output_dir),
            "series_file": str(output_dir / f"{new_commit}_series") if patches else None,
        }

    def _backup_src(self) -> dict:
        """Backup src/ directory excluding upstream."""
        backup_mgr = BackupManager(self.repo_root)
        backup_path = backup_mgr.backup(Path("src"))

        return {
            "backup_path": str(backup_path),
            "files_count": len(list(backup_path.rglob("*"))),
        }

    def _sync_src(self, commit: str) -> dict:
        """Sync src/ with new upstream source (same-name files overwritten)."""
        import shutil

        short_commit = commit[:8] if len(commit) > 8 else commit
        upstream_src = Path("src") / "upstream" / short_commit
        target_src = Path("src")

        if not upstream_src.exists():
            raise FileNotFoundError(f"Upstream source not found: {upstream_src}")

        files_copied = 0

        # Copy files from upstream, overwriting same-name files
        for item in upstream_src.rglob("*"):
            if item.is_file():
                rel_path = item.relative_to(upstream_src)
                target_file = target_src / rel_path

                # Ensure target directory exists
                target_file.parent.mkdir(parents=True, exist_ok=True)

                shutil.copy2(item, target_file)
                files_copied += 1

        return {
            "upstream_dir": str(upstream_src),
            "files_copied": files_copied,
        }

    def _apply_patches(self, commit: str) -> dict:
        """Apply patches for the given commit."""
        if self.cfg.patches.patch_subdir:
            patch_dir = Path(str(self.cfg.patches.patch_subdir).format(commit=commit))
            series_file = patch_dir / f"{commit}_series"
        else:
            patch_dir = self.cfg.patches.directory
            series_file = self.cfg.patches.series_file

        if not patch_dir.exists():
            return {
                "success_count": 0,
                "failed_count": 0,
                "error": f"Patch directory not found: {patch_dir}",
            }

        engine = create_engine(self.cfg.patches)
        result = engine.apply_all(patch_dir, series_file)

        return {
            "success_count": len(result.success),
            "failed_count": len(result.failed),
            "needs_review_count": len(result.needs_review),
            "failed_patches": [f[0] for f in result.failed],
        }

    def _verify(self, new_commit: str, old_commit: str) -> dict:
        """Verify patch functional equivalence."""
        short_new = new_commit[:8] if len(new_commit) > 8 else new_commit
        short_old = old_commit[:8] if len(old_commit) > 8 else old_commit

        old_patches_dir = self._resolve_patch_dir(old_commit)
        new_patches_dir = self._resolve_patch_dir(new_commit)
        old_upstream_dir = Path("src") / "upstream" / short_old
        new_upstream_dir = Path("src") / "upstream" / short_new
        backup_dir = Path("backup")

        verifier = Verifier(self.repo_root)
        result = verifier.verify_patches(
            old_patches_dir=old_patches_dir,
            new_patches_dir=new_patches_dir,
            old_upstream_dir=old_upstream_dir,
            new_upstream_dir=new_upstream_dir,
            backup_dir=backup_dir,
        )

        # Generate report
        report_path = Path(".upstream-sync") / f"verify-{short_new}.md"
        report_path.parent.mkdir(exist_ok=True)
        verifier.generate_verification_report(result, report_path)

        return {
            "passed": result.passed,
            "report_path": str(report_path),
            "issues": result.details.get("issues", []) if result.details else [],
        }

    def _resolve_patch_dir(self, commit: str) -> Path:
        """Resolve patch directory for a commit."""
        if self.cfg.patches.patch_subdir:
            return Path(str(self.cfg.patches.patch_subdir).format(commit=commit))
        return self.cfg.patches.directory


def main():
    parser = argparse.ArgumentParser(
        description="Upstream Sync Workflow - Full upstream synchronization automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sync from commit 123abc to 456def
  python scripts/upstream_sync_workflow.py --new-commit 456def --old-commit 123abc

  # Skip verification step
  python scripts/upstream_sync_workflow.py --new-commit 456def --old-commit 123abc --skip-verify

  # Skip backup (dangerous!)
  python scripts/upstream_sync_workflow.py --new-commit 456def --old-commit 123abc --skip-backup

  # Show help
  python scripts/upstream_sync_workflow.py --help
        """,
    )

    parser.add_argument(
        "--new-commit",
        required=True,
        help="New upstream commit hash to sync to",
    )
    parser.add_argument(
        "--old-commit",
        required=True,
        help="Old upstream commit hash to reference for patch patterns",
    )
    parser.add_argument(
        "--config",
        default="upstream-sync.yaml",
        help="Path to upstream-sync.yaml (default: upstream-sync.yaml)",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip backup step (dangerous!)",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip verification step",
    )
    parser.add_argument(
        "--skip-patch-apply",
        action="store_true",
        help="Skip applying new patches",
    )

    args = parser.parse_args()

    workflow = UpstreamSyncWorkflow(config_path=args.config)

    try:
        results = workflow.run(
            new_commit=args.new_commit,
            old_commit=args.old_commit,
            skip_backup=args.skip_backup,
            skip_verify=args.skip_verify,
            skip_patch_apply=args.skip_patch_apply,
        )

        if results["status"] == "completed":
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()