"""WI-6.4 tests — ``is_team_lead`` predicate (4 truth cases)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services.swarm.team_membership import is_team_lead
from src.tool_system.context import ToolContext


def test_is_team_lead_true_when_agent_id_matches(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.team = {"team_name": "t", "lead_agent_id": "lead-123"}
    ctx.agent_id = "lead-123"
    assert is_team_lead(ctx) is True


def test_is_team_lead_false_when_agent_id_is_member_not_lead(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.team = {"team_name": "t", "lead_agent_id": "lead-123"}
    ctx.agent_id = "member-456"
    assert is_team_lead(ctx) is False


def test_is_team_lead_false_when_no_team_active(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.agent_id = "lead-123"
    # ctx.team is None
    assert is_team_lead(ctx) is False


def test_is_team_lead_false_when_agent_id_unset(tmp_path: Path) -> None:
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.team = {"team_name": "t", "lead_agent_id": "lead-123"}
    # ctx.agent_id is None
    assert is_team_lead(ctx) is False


def test_is_team_lead_false_when_team_lacks_lead_agent_id(tmp_path: Path) -> None:
    """Defensive — a malformed team dict missing ``lead_agent_id``
    must NOT spuriously authorize anyone."""
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.team = {"team_name": "t"}  # no lead_agent_id
    ctx.agent_id = "lead-123"
    assert is_team_lead(ctx) is False


def test_is_team_lead_false_when_team_is_not_a_dict(tmp_path: Path) -> None:
    """Defensive — a malformed team field must not authorize.

    Per the docstring: the predicate collapses 'team unavailable' and
    'not authorized' into the same False return so authorization
    branches handle both safely. Granting permission to either would
    be catastrophic.
    """
    ctx = ToolContext(workspace_root=tmp_path)
    ctx.team = "not-a-dict"  # type: ignore[assignment]
    ctx.agent_id = "lead-123"
    assert is_team_lead(ctx) is False
