"""Per-issue isolated workspace management.

Port of Symphony's Workspace module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Workspace:
    """One active workspace."""

    path: Path
    issue_identifier: str
    issue_id: str | None = None


@dataclass
class WorkspaceConfig:
    """Configuration for workspace management."""

    root: Path
    hooks: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.hooks is None:
            self.hooks = {}


class WorkspaceManager:
    """Per-issue isolated workspace management."""

    def __init__(self, config: WorkspaceConfig) -> None:
        self.config = config
        self._root = Path(config.root).expanduser().resolve()

    async def create_for_issue(self, issue: Any) -> Workspace:
        """Create or freshen workspace for an issue.

        Runs after_create hook if configured.
        """
        issue_id = getattr(issue, "id", None)
        identifier = getattr(issue, "identifier", None) or "issue"
        safe_id = _safe_identifier(identifier)

        workspace_path = self._build_path(safe_id)
        created = await self._ensure_workspace(workspace_path)

        if created:
            hook = self.config.hooks.get("after_create")
            if hook:
                await self._run_hook(
                    hook, workspace_path, issue, "after_create"
                )

        return Workspace(
            path=workspace_path, issue_identifier=safe_id, issue_id=issue_id
        )

    async def cleanup(self, issue: Any) -> None:
        """Remove workspace directory. Runs before_remove hook."""
        identifier = getattr(issue, "identifier", None) or "issue"
        safe_id = _safe_identifier(identifier)
        workspace_path = self._build_path(safe_id)

        if workspace_path.exists():
            hook = self.config.hooks.get("before_remove")
            if hook:
                await self._run_hook(
                    hook, workspace_path, issue, "before_remove", ignore_fail=True
                )
            try:
                shutil.rmtree(workspace_path)
            except Exception as exc:
                logger.warning(
                    "Failed to remove workspace %s: %s", workspace_path, exc
                )

    async def run_before_run_hook(
        self, workspace: Workspace, issue: Any
    ) -> None:
        hook = self.config.hooks.get("before_run")
        if hook:
            await self._run_hook(hook, workspace.path, issue, "before_run")

    async def run_after_run_hook(
        self, workspace: Workspace, issue: Any
    ) -> None:
        hook = self.config.hooks.get("after_run")
        if hook:
            await self._run_hook(
                hook, workspace.path, issue, "after_run", ignore_fail=True
            )

    def _build_path(self, safe_id: str) -> Path:
        return self._root / safe_id

    async def _ensure_workspace(self, path: Path) -> bool:
        """Ensure workspace exists. Returns True if newly created."""
        if path.is_dir():
            return False
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        return True

    async def _run_hook(
        self,
        command: str,
        workspace: Path,
        issue: Any,
        hook_name: str,
        ignore_fail: bool = False,
    ) -> None:
        timeout_ms = self.config.hooks.get("timeout_ms", 60_000)
        timeout_sec = timeout_ms / 1000.0

        issue_id = getattr(issue, "id", None)
        identifier = getattr(issue, "identifier", None) or "issue"

        logger.info(
            "Running workspace hook=%s issue_id=%s identifier=%s workspace=%s",
            hook_name,
            issue_id,
            identifier,
            workspace,
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_sec
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning(
                    "Workspace hook timed out hook=%s issue_id=%s timeout_ms=%s",
                    hook_name,
                    issue_id,
                    timeout_ms,
                )
                if not ignore_fail:
                    raise WorkspaceHookError(
                        f"Hook {hook_name} timed out after {timeout_ms}ms"
                    )
                return

            if proc.returncode != 0:
                output = stdout.decode("utf-8", errors="replace")[:2048]
                logger.warning(
                    "Workspace hook failed hook=%s issue_id=%s status=%s output=%s",
                    hook_name,
                    issue_id,
                    proc.returncode,
                    output,
                )
                if not ignore_fail:
                    raise WorkspaceHookError(
                        f"Hook {hook_name} failed with exit code {proc.returncode}"
                    )
        except WorkspaceHookError:
            raise
        except Exception as exc:
            logger.error(
                "Workspace hook error hook=%s issue_id=%s error=%s",
                hook_name,
                issue_id,
                exc,
            )
            if not ignore_fail:
                raise WorkspaceHookError(f"Hook {hook_name} error: {exc}") from exc

    async def run_terminal_workspace_cleanup(self) -> None:
        """Remove workspaces for issues in terminal states on startup.

        Called once by the orchestrator during initialization.
        """
        if not self._root.exists():
            return
        for entry in self._root.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry)
                except Exception as exc:
                    logger.warning("Failed to clean up workspace %s: %s", entry, exc)


class WorkspaceHookError(Exception):
    """Raised when a workspace hook fails."""


def _safe_identifier(identifier: str | None) -> str:
    if not identifier:
        return "issue"
    return re.sub(r"[^a-zA-Z0-9._-]", "_", identifier)
