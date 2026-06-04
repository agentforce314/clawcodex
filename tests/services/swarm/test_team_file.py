"""WI-6.4 tests — team file + members[] schema."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.swarm.team_file import (
    TeamFile,
    TeamMember,
    add_member,
    find_member_by_name,
    get_team_file_path,
    read_team_file,
    remove_member,
    write_team_file,
)


def test_read_missing_team_returns_none(tmp_path: Path) -> None:
    assert read_team_file(tmp_path) is None


def test_round_trip_write_read(tmp_path: Path) -> None:
    team = TeamFile(
        team_name="my-team",
        lead_agent_id="abc12345",
        description="testing",
        members=(
            TeamMember(agent_id="t1", name="alice", color="blue"),
            TeamMember(agent_id="t2", name="bob"),
        ),
    )
    write_team_file(team, tmp_path)
    loaded = read_team_file(tmp_path)
    assert loaded == team


def test_legacy_team_file_no_members_treated_as_empty(tmp_path: Path) -> None:
    """Pre-Chunk-F team files lacked ``members``; reader treats it
    as empty rather than raising."""
    path = get_team_file_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"team_name": "old", "lead_agent_id": "abc", "description": "legacy"}
        ),
        encoding="utf-8",
    )
    loaded = read_team_file(tmp_path)
    assert loaded is not None
    assert loaded.members == ()


def test_add_member_returns_new_team_file(tmp_path: Path) -> None:
    """Frozen TeamFile — mutator returns new instance."""
    team = TeamFile(team_name="t", lead_agent_id="abc")
    member = TeamMember(agent_id="a1", name="alice")
    new_team = add_member(team, member)

    assert team.members == ()  # original unchanged
    assert new_team.members == (member,)


def test_add_member_replaces_existing_agent_id(tmp_path: Path) -> None:
    """Idempotent on agent_id — re-add replaces, not duplicates."""
    team = TeamFile(team_name="t", lead_agent_id="abc")
    team = add_member(team, TeamMember(agent_id="a1", name="alice", color="blue"))
    team = add_member(team, TeamMember(agent_id="a1", name="alice", color="red"))

    assert len(team.members) == 1
    assert team.members[0].color == "red"


def test_remove_member_drops_only_target(tmp_path: Path) -> None:
    team = TeamFile(
        team_name="t", lead_agent_id="abc",
        members=(
            TeamMember(agent_id="a1", name="alice"),
            TeamMember(agent_id="a2", name="bob"),
        ),
    )
    new_team = remove_member(team, "a1")
    assert {m.agent_id for m in new_team.members} == {"a2"}


def test_find_member_by_name_case_insensitive() -> None:
    team = TeamFile(
        team_name="t", lead_agent_id="abc",
        members=(TeamMember(agent_id="a1", name="Alice"),),
    )
    assert find_member_by_name(team, "alice") is not None
    assert find_member_by_name(team, "ALICE") is not None
    assert find_member_by_name(team, "bob") is None


def test_team_create_writes_members_field(tmp_path: Path) -> None:
    """``TeamCreate`` (Chunk-F edit) now writes ``members: []`` from
    day one — verify the on-disk shape directly."""
    from src.tool_system.context import ToolContext
    from src.tool_system.tools.team import TeamCreateTool

    ctx = ToolContext(workspace_root=tmp_path)
    TeamCreateTool.call(
        {"team_name": "my-team", "description": "x"}, ctx
    )
    raw = json.loads(get_team_file_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["members"] == []
    assert raw["team_name"] == "my-team"
    assert "lead_agent_id" in raw
