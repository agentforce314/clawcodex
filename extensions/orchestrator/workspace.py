"""Per-issue isolated workspace management.

Port of Symphony's Workspace module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
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
    repo_clone_url: str | None = None
    clone_depth: int | None = 1
    checkout_issue_branch: bool = True
    git_username: str | None = None
    git_token: str | None = None
    strategy: str = "isolated"
    base_branch: str | None = None
    integration_branch: str | None = None
    require_clean_start: bool = True
    require_clean_between_issues: bool = True
    preserve_on_terminal: bool = True
    sequential_lock: bool = True

    def __post_init__(self) -> None:
        if self.hooks is None:
            self.hooks = {}
        self.strategy = str(self.strategy or "isolated").strip().lower()
        if self.strategy not in {"isolated", "shared", "sequential"}:
            raise ValueError(
                "workspace.strategy must be one of: isolated, shared, sequential"
            )


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

        if self.config.strategy == "isolated":
            workspace_path = self._build_path(safe_id)
            created = await self._prepare_workspace(workspace_path, issue)
        else:
            workspace_path = self._root
            created = await self._prepare_shared_workspace(workspace_path)
            if self.config.strategy == "sequential":
                try:
                    await self._prepare_sequential_workspace(workspace_path, issue)
                except Exception:
                    await self._release_sequential_lock()
                    raise

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
        workspace_path = (
            self._build_path(safe_id)
            if self.config.strategy == "isolated"
            else self._root
        )

        if workspace_path.exists():
            hook = self.config.hooks.get("before_remove")
            if hook:
                await self._run_hook(
                    hook, workspace_path, issue, "before_remove", ignore_fail=True
                )
            if self.config.strategy == "isolated":
                try:
                    shutil.rmtree(workspace_path)
                except Exception as exc:
                    logger.warning(
                        "Failed to remove workspace %s: %s", workspace_path, exc
                    )
        if self.config.strategy == "sequential":
            await self._release_sequential_lock()

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

    async def _prepare_workspace(self, path: Path, issue: Any) -> bool:
        created = False
        if self.config.repo_clone_url:
            if not path.exists():
                await self._clone_repository(path)
                created = True
            elif not path.is_dir():
                path.unlink(missing_ok=True)
                await self._clone_repository(path)
                created = True
            elif not (path / ".git").exists():
                shutil.rmtree(path, ignore_errors=True)
                await self._clone_repository(path)
                created = True
            await self._checkout_issue_branch(path, issue)
            return created

        return await self._ensure_workspace(path)

    async def _prepare_shared_workspace(self, path: Path) -> bool:
        if self.config.repo_clone_url:
            if not path.exists():
                await self._clone_repository(path)
                return True
            if not path.is_dir():
                raise WorkspaceHookError(
                    f"Shared workspace path exists and is not a directory: {path}"
                )
            if not (path / ".git").exists():
                # 如果目录存在但不是 git 仓库，自动 init 并设置 remote
                logger.info(
                    "Shared workspace %s is not a git repo, initializing...",
                    path,
                )
                await self._run_process(["git", "init"], cwd=str(path))
                await self._run_process(
                    ["git", "remote", "add", "origin", self.config.repo_clone_url],
                    cwd=str(path),
                )
                integration_branch = (
                    self.config.integration_branch
                    or self.config.base_branch
                    or ""
                ).strip()
                if integration_branch:
                    # 从本地来源仓库 fetch 集成 branch
                    fetch_cmd = [
                        "git",
                        "fetch",
                        "origin",
                        f"{integration_branch}:refs/remotes/origin/{integration_branch}",
                    ]
                    await self._try_process(fetch_cmd, cwd=str(path))
                return True
            return False
        return await self._ensure_workspace(path)

    async def _prepare_sequential_workspace(self, path: Path, issue: Any) -> None:
        await self._checkout_integration_branch(path)
        if self.config.require_clean_start:
            await self._ensure_clean_workspace(
                path,
                "sequential workspace must be clean before starting an issue",
            )
        await self._acquire_sequential_lock(issue)
        self._exclude_sequential_lock(path)

    async def _ensure_workspace(self, path: Path) -> bool:
        """Ensure workspace exists. Returns True if newly created."""
        if path.is_dir():
            return False
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        return True

    async def _clone_repository(self, path: Path) -> None:
        clone_url = self.config.repo_clone_url
        if not clone_url:
            raise WorkspaceHookError("Missing repo_clone_url")

        path.parent.mkdir(parents=True, exist_ok=True)

        # Inject credentials if git_username and git_token are configured
        effective_url = clone_url
        if self.config.git_username and self.config.git_token:
            effective_url = clone_url.replace(
                "https://", f"https://{self.config.git_username}:{self.config.git_token}@"
            )

        command = ["git", "clone"]
        if (
            isinstance(self.config.clone_depth, int)
            and self.config.clone_depth > 0
        ):
            command.extend(["--depth", str(self.config.clone_depth)])
        command.extend([effective_url, str(path)])
        await self._run_process(command, cwd=str(path.parent))

    async def _checkout_issue_branch(self, path: Path, issue: Any) -> None:
        if not self.config.checkout_issue_branch:
            return
        if not (path / ".git").exists():
            return

        branch_name = getattr(issue, "branch_name", None)
        if not isinstance(branch_name, str) or not branch_name.strip():
            return
        branch_name = branch_name.strip()

        if await self._try_process(
            ["git", "checkout", branch_name],
            cwd=str(path),
        ):
            return

        await self._try_process(
            [
                "git",
                "fetch",
                "origin",
                f"{branch_name}:refs/remotes/origin/{branch_name}",
            ],
            cwd=str(path),
        )

        if await self._try_process(
            ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch_name}"],
            cwd=str(path),
        ):
            if await self._try_process(
                ["git", "checkout", "-b", branch_name, "--track", f"origin/{branch_name}"],
                cwd=str(path),
            ):
                return

        if not await self._try_process(
            ["git", "checkout", "-b", branch_name],
            cwd=str(path),
        ):
            logger.warning(
                "Failed to checkout issue branch branch=%s workspace=%s",
                branch_name,
                path,
            )

    async def _checkout_integration_branch(self, path: Path) -> None:
        if not (path / ".git").exists():
            return
        integration_branch = (
            self.config.integration_branch or self.config.base_branch or ""
        ).strip()
        if not integration_branch:
            return
        if await self._try_process(["git", "checkout", integration_branch], cwd=str(path)):
            return
        base_branch = (self.config.base_branch or "").strip()
        if base_branch:
            await self._run_process(["git", "checkout", base_branch], cwd=str(path))
        await self._run_process(
            ["git", "checkout", "-b", integration_branch], cwd=str(path)
        )

    async def _ensure_clean_workspace(self, path: Path, reason: str) -> None:
        if not (path / ".git").exists():
            return
        output = await self._run_process(
            ["git", "status", "--porcelain"], cwd=str(path)
        )
        if output.decode("utf-8", errors="replace").strip():
            raise WorkspaceHookError(reason)

    async def current_head(self, path: Path | str | None = None) -> str | None:
        workspace_path = Path(path) if path is not None else self._root
        if not (workspace_path / ".git").exists():
            return None
        output = await self._run_process(
            ["git", "rev-parse", "HEAD"], cwd=str(workspace_path)
        )
        return output.decode("utf-8", errors="replace").strip() or None

    async def _acquire_sequential_lock(self, issue: Any) -> None:
        if not self.config.sequential_lock:
            return
        lock_path = self._sequential_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Stale lock recovery: if lock exists, check if its PID is still alive
        if lock_path.exists():
            if not self._lock_pid_alive(lock_path):
                logger.warning(
                    "Stale sequential lock found at %s, removing (owner process dead)",
                    lock_path,
                )
                lock_path.unlink()
            else:
                raise WorkspaceHookError(
                    f"Sequential workspace lock already held by live process: "
                    f"{lock_path}"
                )
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise WorkspaceHookError(
                f"Sequential workspace lock already exists: {lock_path}"
            ) from exc
        issue_id = getattr(issue, "id", None) or ""
        identifier = getattr(issue, "identifier", None) or "issue"
        content = "\n".join(
            [
                f"pid={os.getpid()}",
                f"issue_id={issue_id}",
                f"issue_identifier={identifier}",
                f"timestamp={time.time()}",
                "",
            ]
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)

    async def _release_sequential_lock(self) -> None:
        if not self.config.sequential_lock:
            return
        self._sequential_lock_path().unlink(missing_ok=True)

    def _sequential_lock_path(self) -> Path:
        return self._root / ".clawcodex_workspace.lock"

    def _lock_pid_alive(self, lock_path: Path) -> bool:
        """Check if the PID recorded in the lock file is still alive."""
        try:
            content = lock_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.startswith("pid="):
                    pid_str = line.split("=", 1)[1].strip()
                    if pid_str:
                        pid = int(pid_str)
                        os.kill(pid, 0)
                        return True
        except (ValueError, OSError, FileNotFoundError):
            pass
        return False

    def _exclude_sequential_lock(self, path: Path) -> None:
        exclude_path = path / ".git" / "info" / "exclude"
        if not exclude_path.exists():
            return
        existing = exclude_path.read_text(encoding="utf-8")
        pattern = ".clawcodex_workspace.lock"
        if pattern in {line.strip() for line in existing.splitlines()}:
            return
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        exclude_path.write_text(f"{existing}{suffix}{pattern}\n", encoding="utf-8")

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
            await self._run_process(
                command,
                cwd=str(workspace),
                timeout_sec=timeout_sec,
                shell=True,
                logger_context={
                    "hook_name": hook_name,
                    "issue_id": issue_id,
                    "timeout_ms": timeout_ms,
                },
            )
        except WorkspaceHookError:
            if ignore_fail:
                return
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
        if self.config.strategy != "isolated":
            return
        if not self._root.exists():
            return
        for entry in self._root.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry)
                except Exception as exc:
                    logger.warning("Failed to clean up workspace %s: %s", entry, exc)

    async def _run_process(
        self,
        command: list[str] | str,
        *,
        cwd: str,
        timeout_sec: float = 60.0,
        shell: bool = False,
        logger_context: dict[str, Any] | None = None,
    ) -> bytes:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                str(command),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        else:
            assert isinstance(command, list)
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            context = logger_context or {}
            logger.warning(
                "Workspace process timed out command=%s timeout_sec=%s context=%s",
                command,
                timeout_sec,
                context,
            )
            raise WorkspaceHookError(
                f"Workspace command timed out after {int(timeout_sec * 1000)}ms"
            ) from exc

        if proc.returncode != 0:
            output = stdout.decode("utf-8", errors="replace")[:2048]
            context = logger_context or {}
            logger.warning(
                "Workspace process failed command=%s status=%s context=%s output=%s",
                command,
                proc.returncode,
                context,
                output,
            )
            raise WorkspaceHookError(
                f"Workspace command failed with exit code {proc.returncode}"
            )
        return stdout

    async def _try_process(
        self,
        command: list[str],
        *,
        cwd: str,
    ) -> bool:
        try:
            await self._run_process(command, cwd=cwd, timeout_sec=30.0)
        except WorkspaceHookError:
            return False
        return True


class WorkspaceHookError(Exception):
    """Raised when a workspace hook fails."""


def _safe_identifier(identifier: str | None) -> str:
    if not identifier:
        return "issue"
    return re.sub(r"[^a-zA-Z0-9._-]", "_", identifier)
