from __future__ import annotations

from src.permissions.pre_trust import check_pre_trust_gate


def test_policy_and_user_sources_are_allowed_pre_trust():
    assert check_pre_trust_gate(
        "hook", source="policy", workspace_trusted=False,
    ).allow is True
    assert check_pre_trust_gate(
        "mcp", source="user", workspace_trusted=False,
    ).allow is True


def test_project_scoped_sources_require_workspace_trust():
    denied = check_pre_trust_gate(
        "mcp", source="project", workspace_trusted=False,
    )
    assert denied.allow is False
    assert "not trusted" in denied.reason

    allowed = check_pre_trust_gate(
        "project-config", source="local", workspace_trusted=True,
    )
    assert allowed.allow is True


def test_unknown_source_fails_closed():
    decision = check_pre_trust_gate(
        "hook", source=None, workspace_trusted=False,
    )
    assert decision.allow is False
