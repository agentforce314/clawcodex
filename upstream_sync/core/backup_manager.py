# upstream_sync/core/backup_manager.py
"""Backup and restore management for src/ directory.

Handles backup of src/ (excluding src/upstream/) and restoration
of backed up content.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


class BackupManager:
    """Manages backup and restore operations for the src/ directory."""

    def __init__(self, repo_root: Path, backup_root: Path | None = None) -> None:
        self.repo_root = repo_root
        self.backup_root = backup_root or repo_root / "backup"

    def backup(
        self,
        src_path: Path | None = None,
        exclude_dirs: list[str] | None = None,
    ) -> Path:
        """Backup src/ directory excluding specified patterns.

        Args:
            src_path: Source path to backup (default: src/)
            exclude_dirs: List of directory basenames to exclude (default: ["upstream"])

        Returns:
            Path to the backup directory created.
        """
        if src_path is None:
            src_path = self.repo_root / "src"
        if exclude_dirs is None:
            exclude_dirs = ["upstream", ".git", "__pycache__", "*.pyc", ".pytest_cache"]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}"
        backup_dir = self.backup_root / backup_name

        # Create backup root if needed
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Copy files excluding upstream and other patterns
        self._copy_excluding(src_path, backup_dir, exclude_dirs)

        return backup_dir

    def restore(
        self,
        backup_dir: Path,
        target_path: Path | None = None,
        clear_first: bool = False,
    ) -> list[Path]:
        """Restore a backup to the target path.

        Args:
            backup_dir: The backup directory to restore from.
            target_path: Target path to restore to (default: src/)
            clear_first: If True, clear target before restoring.

        Returns:
            List of files that were restored.
        """
        if target_path is None:
            target_path = self.repo_root / "src"

        restored_files = []

        if clear_first:
            self._clear_directory(target_path)

        if not backup_dir.exists():
            raise FileNotFoundError(f"Backup directory not found: {backup_dir}")

        # Restore all files from backup
        for item in backup_dir.rglob("*"):
            if item.is_file():
                rel_path = item.relative_to(backup_dir)
                target_file = target_path / rel_path

                # Ensure target directory exists
                target_file.parent.mkdir(parents=True, exist_ok=True)

                shutil.copy2(item, target_file)
                restored_files.append(target_file)

        return restored_files

    def list_backups(self) -> list[dict]:
        """List all available backups with metadata.

        Returns:
            List of dicts with backup info (path, timestamp, file_count).
        """
        backups = []
        if not self.backup_root.exists():
            return backups

        for backup_dir in sorted(self.backup_root.iterdir(), reverse=True):
            if backup_dir.is_dir() and backup_dir.name.startswith("backup_"):
                timestamp_str = backup_dir.name.replace("backup_", "")
                files = list(backup_dir.rglob("*"))
                backups.append({
                    "path": backup_dir,
                    "timestamp": timestamp_str,
                    "file_count": len([f for f in files if f.is_file()]),
                })

        return backups

    def cleanup_old_backups(self, keep_count: int = 5) -> list[Path]:
        """Remove old backups keeping only the most recent N.

        Args:
            keep_count: Number of recent backups to keep.

        Returns:
            List of removed backup directories.
        """
        removed = []
        backups = self.list_backups()

        if len(backups) > keep_count:
            for backup in backups[keep_count:]:
                shutil.rmtree(backup["path"])
                removed.append(backup["path"])

        return removed

    def _copy_excluding(
        self,
        src: Path,
        dst: Path,
        exclude_basenames: list[str],
    ) -> None:
        """Copy directory tree excluding certain basenames."""
        for item in src.rglob("*"):
            if item.is_file():
                # Check if any parent directory should be excluded
                should_exclude = False
                for parent in item.parents:
                    if parent.name in exclude_basenames:
                        should_exclude = True
                        break

                if should_exclude:
                    continue

                rel_path = item.relative_to(src)
                target_file = dst / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target_file)

    def _clear_directory(self, path: Path) -> None:
        """Clear all contents from a directory."""
        if not path.exists():
            return
        for item in path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()