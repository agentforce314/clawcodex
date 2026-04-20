"""Tests for agent permission mode inheritance.

Validates the resolve_permission_mode() logic from src/agent/run_agent.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.agent_definitions import AgentDefinition, GENERAL_PURPOSE_AGENT
from src.agent.run_agent import resolve_permission_mode, _build_permission_context
from src.permissions.types import PermissionMode, ToolPermissionContext
from src.tool_system.context import ToolContext
from src.utils.abort_controller import AbortController


def _make_context(mode: PermissionMode = "default", **kwargs) -> ToolContext:
    return ToolContext(
        workspace_root=Path("/tmp/test-ws"),
        permission_context=ToolPermissionContext(mode=mode, **kwargs),
        abort_controller=AbortController(),
    )


def _make_agent(permission_mode: PermissionMode | None = None) -> AgentDefinition:
    return AgentDefinition(
        agent_type="test-agent",
        when_to_use="test",
        permission_mode=permission_mode,
    )


# --- resolve_permission_mode ---

class TestResolvePermissionMode:
    def test_bypass_perms_takes_precedence(self):
        """Parent bypassPermissions always wins, even if agent defines plan."""
        ctx = _make_context("bypassPermissions")
        agent = _make_agent("plan")

        result = resolve_permission_mode(ctx, agent)

        assert result == "bypassPermissions"

    def test_accept_edits_takes_precedence(self):
        """Parent acceptEdits always wins."""
        ctx = _make_context("acceptEdits")
        agent = _make_agent("plan")

        result = resolve_permission_mode(ctx, agent)

        assert result == "acceptEdits"

    def test_dont_ask_takes_precedence(self):
        """Parent dontAsk always wins."""
        ctx = _make_context("dontAsk")
        agent = _make_agent("plan")

        result = resolve_permission_mode(ctx, agent)

        assert result == "dontAsk"

    def test_plan_mode_agent_overrides_default_parent(self):
        """Agent's permissionMode overrides default parent mode."""
        ctx = _make_context("default")
        agent = _make_agent("plan")

        result = resolve_permission_mode(ctx, agent)

        assert result == "plan"

    def test_accept_edits_agent_overrides_plan_parent(self):
        """Agent's permissionMode overrides plan parent mode."""
        ctx = _make_context("plan")
        agent = _make_agent("acceptEdits")

        result = resolve_permission_mode(ctx, agent)

        assert result == "acceptEdits"

    def test_default_mode_no_override(self):
        """No agent permissionMode → parent mode passes through."""
        ctx = _make_context("default")
        agent = _make_agent(None)

        result = resolve_permission_mode(ctx, agent)

        assert result == "default"

    def test_plan_parent_no_agent_override(self):
        """No agent permissionMode → parent plan passes through."""
        ctx = _make_context("plan")
        agent = _make_agent(None)

        result = resolve_permission_mode(ctx, agent)

        assert result == "plan"

    def test_bypass_agent_overrides_default_parent(self):
        """Agent bypass overrides default parent."""
        ctx = _make_context("default")
        agent = _make_agent("bypassPermissions")

        result = resolve_permission_mode(ctx, agent)

        assert result == "bypassPermissions"


# --- _build_permission_context ---

class TestBuildPermissionContext:
    def test_sync_preserves_parent_prompts_setting(self):
        """Sync agents inherit should_avoid_permission_prompts from parent."""
        ctx = _make_context(
            "default",
            should_avoid_permission_prompts=False,
        )

        perm = _build_permission_context(ctx, "default", is_async=False)

        assert perm.should_avoid_permission_prompts is False

    def test_async_always_avoids_prompts(self):
        """Async agents always suppress permission prompts."""
        ctx = _make_context(
            "default",
            should_avoid_permission_prompts=False,
        )

        perm = _build_permission_context(ctx, "default", is_async=True)

        assert perm.should_avoid_permission_prompts is True

    def test_effective_mode_applied(self):
        """Effective mode is set on the built permission context."""
        ctx = _make_context("default")

        perm = _build_permission_context(ctx, "plan", is_async=False)

        assert perm.mode == "plan"

    def test_rules_inherited(self):
        """Allow/deny/ask rules are inherited from parent."""
        ctx = _make_context(
            "default",
            always_allow_rules={"session": ["Read"]},
            always_deny_rules={"session": ["Write"]},
        )

        perm = _build_permission_context(ctx, "default", is_async=False)

        assert perm.always_allow_rules == {"session": ["Read"]}
        assert perm.always_deny_rules == {"session": ["Write"]}

    def test_bypass_available_inherited(self):
        """is_bypass_permissions_mode_available inherited from parent."""
        ctx = _make_context(
            "default",
            is_bypass_permissions_mode_available=True,
        )

        perm = _build_permission_context(ctx, "default", is_async=False)

        assert perm.is_bypass_permissions_mode_available is True


# --- End-to-end permission scenarios ---

class TestPermissionScenarios:
    def test_general_purpose_agent_inherits_default(self):
        """GENERAL_PURPOSE_AGENT has no permissionMode → inherits parent."""
        ctx = _make_context("default")

        result = resolve_permission_mode(ctx, GENERAL_PURPOSE_AGENT)

        assert result == "default"

    def test_general_purpose_agent_inherits_bypass(self):
        """GENERAL_PURPOSE_AGENT inherits parent bypass."""
        ctx = _make_context("bypassPermissions")

        result = resolve_permission_mode(ctx, GENERAL_PURPOSE_AGENT)

        assert result == "bypassPermissions"
