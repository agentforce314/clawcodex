from __future__ import annotations

from pathlib import Path

import pytest

from src.execution import (
    DefaultWorkspaceGuard,
    ExecutionBoundary,
    ProcessDecision,
    WorkspaceDecision,
)
from src.permissions.types import ToolPermissionContext
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolPermissionError


class StrictWorkspaceGuard(DefaultWorkspaceGuard):
    def check_path(
        self,
        path: Path,
        *,
        roots,
        access: str,
        allow_workspace_escape: bool = False,
    ) -> WorkspaceDecision:
        return super().check_path(
            path,
            roots=roots,
            access=access,
            allow_workspace_escape=False,
        )


class RecordingWorkspaceGuard(DefaultWorkspaceGuard):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, bool]] = []

    def check_path(
        self,
        path: Path,
        *,
        roots,
        access: str,
        allow_workspace_escape: bool = False,
    ) -> WorkspaceDecision:
        self.calls.append((access, path, allow_workspace_escape))
        return super().check_path(
            path,
            roots=roots,
            access=access,
            allow_workspace_escape=allow_workspace_escape,
        )


class DenyAllProcessPolicy:
    def check_process(self, command: str, *, cwd: Path, env=None) -> ProcessDecision:
        return ProcessDecision(allow=False, reason=f"blocked: {command}")


class ScrubEnvPolicy:
    def prepare_env(self, env=None) -> dict[str, str]:
        env = dict(env or {})
        env.pop("SECRET_TOKEN", None)
        return env


def test_default_boundary_preserves_existing_bypass_workspace_escape(tmp_path):
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
    )

    outside = Path("/tmp/clawcodex-execution-boundary-outside.txt")

    assert ctx.ensure_allowed_path(outside) == outside.resolve()


def test_strict_workspace_guard_blocks_even_when_permission_would_bypass(tmp_path):
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="bypassPermissions"),
        execution_boundary=ExecutionBoundary(workspace_guard=StrictWorkspaceGuard()),
    )

    with pytest.raises(ToolPermissionError, match="outside execution workspace roots"):
        ctx.ensure_allowed_path("/etc/passwd")


def test_tool_context_routes_read_and_write_checks_through_boundary(tmp_path):
    guard = RecordingWorkspaceGuard()
    ctx = ToolContext(
        workspace_root=tmp_path,
        permission_context=ToolPermissionContext(mode="default"),
        execution_boundary=ExecutionBoundary(workspace_guard=guard),
    )
    target = tmp_path / "src" / "x.py"

    assert ctx.ensure_allowed_path(target) == target.resolve()
    assert ctx.ensure_readable_path(target) == target.resolve()

    assert [call[0] for call in guard.calls] == ["write", "read"]


def test_execution_boundary_exposes_env_and_process_policy_hooks(tmp_path):
    boundary = ExecutionBoundary(
        env_policy=ScrubEnvPolicy(),
        process_policy=DenyAllProcessPolicy(),
    )

    assert boundary.prepare_env({"PATH": "/bin", "SECRET_TOKEN": "x"}) == {
        "PATH": "/bin",
    }
    decision = boundary.check_process("curl https://example.com", cwd=tmp_path)
    assert decision.allow is False
    assert "curl" in decision.reason
