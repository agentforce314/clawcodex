from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class WorkspaceDecision:
    allow: bool
    path: Path
    reason: str


class WorkspaceGuard(Protocol):
    def check_path(
        self,
        path: Path,
        *,
        roots: Sequence[Path],
        access: str,
        allow_workspace_escape: bool = False,
    ) -> WorkspaceDecision: ...


@dataclass(frozen=True)
class DefaultWorkspaceGuard:
    """Canonical path guard under the permission layer.

    ``allow_workspace_escape`` preserves current Full Access / internal-path
    semantics while making the second boundary explicit and replaceable.
    """

    def check_path(
        self,
        path: Path,
        *,
        roots: Sequence[Path],
        access: str,
        allow_workspace_escape: bool = False,
    ) -> WorkspaceDecision:
        resolved = path.expanduser().resolve()
        if allow_workspace_escape:
            return WorkspaceDecision(
                allow=True,
                path=resolved,
                reason=f"{access}: workspace escape explicitly allowed",
            )
        for root in roots:
            try:
                resolved.relative_to(root.expanduser().resolve())
                return WorkspaceDecision(
                    allow=True,
                    path=resolved,
                    reason=f"{access}: path is inside workspace roots",
                )
            except (ValueError, OSError):
                continue
        return WorkspaceDecision(
            allow=False,
            path=resolved,
            reason=f"{access}: path is outside execution workspace roots",
        )


@dataclass(frozen=True)
class ProcessDecision:
    allow: bool
    reason: str


class ProcessPolicy(Protocol):
    def check_process(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> ProcessDecision: ...


@dataclass(frozen=True)
class DefaultProcessPolicy:
    """C3 placeholder policy: validate shape, leave command policy unchanged."""

    def check_process(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> ProcessDecision:
        if not command.strip():
            return ProcessDecision(allow=False, reason="process: empty command")
        return ProcessDecision(allow=True, reason="process: default policy")


class EnvPolicy(Protocol):
    def prepare_env(
        self,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, str]: ...


@dataclass(frozen=True)
class DefaultEnvPolicy:
    """C3 environment policy interface with no scrubbing yet.

    ``C5. Network/secret policy`` owns the minimum-env/secret split. This class
    establishes the stable call surface without changing subprocess behavior.
    """

    def prepare_env(
        self,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        return dict(env or {})


@dataclass(frozen=True)
class ExecutionBoundary:
    workspace_guard: WorkspaceGuard = field(default_factory=DefaultWorkspaceGuard)
    env_policy: EnvPolicy = field(default_factory=DefaultEnvPolicy)
    process_policy: ProcessPolicy = field(default_factory=DefaultProcessPolicy)

    def check_workspace_path(
        self,
        path: Path,
        *,
        roots: Sequence[Path],
        access: str,
        allow_workspace_escape: bool = False,
    ) -> WorkspaceDecision:
        return self.workspace_guard.check_path(
            path,
            roots=roots,
            access=access,
            allow_workspace_escape=allow_workspace_escape,
        )

    def prepare_env(self, env: Mapping[str, str] | None = None) -> dict[str, str]:
        return self.env_policy.prepare_env(env)

    def check_process(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> ProcessDecision:
        return self.process_policy.check_process(command, cwd=cwd, env=env)


def default_execution_boundary() -> ExecutionBoundary:
    return ExecutionBoundary()
