from __future__ import annotations

from pathlib import Path

import pytest

from src.execution import (
    DefaultEnvPolicy,
    DefaultProcessPolicy,
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


def test_default_workspace_guard_allows_inside_roots_and_denies_outside(tmp_path):
    boundary = ExecutionBoundary()
    inside = tmp_path / "src" / "module.py"
    outside = tmp_path.parent / "outside-module.py"

    inside_decision = boundary.check_workspace_path(
        inside,
        roots=[tmp_path],
        access="read",
    )
    outside_decision = boundary.check_workspace_path(
        outside,
        roots=[tmp_path],
        access="read",
    )

    assert inside_decision.allow is True
    assert inside_decision.path == inside.resolve()
    assert "inside workspace roots" in inside_decision.reason
    assert outside_decision.allow is False
    assert outside_decision.path == outside.resolve()
    assert "outside execution workspace roots" in outside_decision.reason


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


def test_tool_context_accepts_additional_working_directory_through_boundary(tmp_path):
    workspace = tmp_path / "workspace"
    extra_root = tmp_path / "extra"
    target = extra_root / "generated.py"
    guard = RecordingWorkspaceGuard()
    ctx = ToolContext(
        workspace_root=workspace,
        additional_working_directories=(extra_root,),
        permission_context=ToolPermissionContext(mode="default"),
        execution_boundary=ExecutionBoundary(workspace_guard=guard),
    )

    assert ctx.ensure_allowed_path(target) == target.resolve()
    assert ctx.ensure_readable_path(target) == target.resolve()

    assert [call[0] for call in guard.calls] == ["write", "read"]
    assert all(call[2] is False for call in guard.calls)


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


def test_default_env_policy_returns_isolated_copy():
    env = {"PATH": "/bin", "TOKEN": "keep-for-c5"}

    prepared = DefaultEnvPolicy().prepare_env(env)
    prepared["PATH"] = "/usr/bin"

    assert env == {"PATH": "/bin", "TOKEN": "keep-for-c5"}
    assert prepared == {"PATH": "/usr/bin", "TOKEN": "keep-for-c5"}


def test_default_process_policy_rejects_empty_command_and_allows_non_empty(tmp_path):
    policy = DefaultProcessPolicy()

    empty = policy.check_process(" \t", cwd=tmp_path)
    allowed = policy.check_process("python -V", cwd=tmp_path)

    assert empty.allow is False
    assert empty.reason == "process: empty command"
    assert allowed.allow is True
    assert allowed.reason == "process: default policy"


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
