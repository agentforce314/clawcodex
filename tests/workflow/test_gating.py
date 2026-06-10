"""Tests for workflow feature gating + the recursion-guard constant."""

from __future__ import annotations

import src.workflow.gating as gating
from src.agent.constants import ALL_AGENT_DISALLOWED_TOOLS, WORKFLOW_TOOL_NAME
from src.settings.types import SettingsSchema


def test_workflow_tool_is_disallowed_in_subagents():
    # Recursion guard: subagents must not be able to launch workflows.
    assert WORKFLOW_TOOL_NAME == "Workflow"
    assert WORKFLOW_TOOL_NAME in ALL_AGENT_DISALLOWED_TOOLS


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    monkeypatch.setattr("src.settings.settings.get_settings", lambda **_: SettingsSchema())
    assert gating.is_workflows_enabled() is True


def test_env_kill_switch(monkeypatch):
    monkeypatch.setattr("src.settings.settings.get_settings", lambda **_: SettingsSchema())
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", value)
        assert gating.is_workflows_enabled() is False
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_WORKFLOWS", "0")
    assert gating.is_workflows_enabled() is True


def test_disabled_via_typed_setting(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    monkeypatch.setattr(
        "src.settings.settings.get_settings", lambda **_: SettingsSchema(disable_workflows=True)
    )
    assert gating.is_workflows_enabled() is False


def test_disabled_via_camelcase_extra(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_WORKFLOWS", raising=False)
    monkeypatch.setattr(
        "src.settings.settings.get_settings",
        lambda **_: SettingsSchema(extra={"disableWorkflows": True}),
    )
    assert gating.is_workflows_enabled() is False
