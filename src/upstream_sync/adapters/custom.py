# upstream_sync/adapters/custom.py
"""Custom-command patch engine adapter.

Delegates all operations to a user-provided executable script.  Useful when
organisations already have bespoke patch-management tooling.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from upstream_sync.core.patch_engine import ApplyResult, PatchEngine


class CustomEngine:
    """PatchEngine implementation backed by an arbitrary external command."""

    def __init__(self, command: str) -> None:
        self.command = command

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.command, *args],
            capture_output=True,
            text=True,
        )

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        """Invoke ``<command> apply <patch_dir> <series_file>``.

        Args:
            patch_dir: Directory containing patch files.
            series_file: File defining patch application order.

        Returns:
            Structured result parsed from the command's stdout/stderr.
        """
        result = self._run(["apply", str(patch_dir), str(series_file)])
        stdout = result.stdout.strip()

        # Custom command can emit JSON for structured results
        if stdout.startswith("{"):
            try:
                data = json.loads(stdout)
                return ApplyResult(
                    success=data.get("success", []),
                    failed=[(p, "") for p in data.get("failed", [])],
                    needs_review=data.get("needs_review", []),
                )
            except json.JSONDecodeError:
                pass

        # Fallback: non-zero exit = failure
        if result.returncode != 0:
            return ApplyResult(
                failed=[("custom-engine", result.stderr or "unknown error")],
            )
        return ApplyResult(success=["custom-engine"])

    def pop_all(self) -> None:
        """Invoke ``<command> pop``."""
        self._run(["pop"])

    def refresh(self, patch_name: str) -> None:
        """Invoke ``<command> refresh <patch_name>``."""
        self._run(["refresh", patch_name])

    def status(self) -> dict:
        """Invoke ``<command> status`` and return parsed output."""
        result = self._run(["status"])
        if result.stdout.strip().startswith("{"):
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                pass
        return {"raw": result.stdout}
