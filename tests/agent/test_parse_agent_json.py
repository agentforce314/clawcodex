"""Tests for src/agent/parse_agent_json.py (SDK initialize control request)."""
from __future__ import annotations

from src.agent.parse_agent_json import parse_agent_from_json, parse_agents_from_json


def test_minimal_json_definition_round_trip():
    agent = parse_agent_from_json(
        "critic",
        {"description": "Senior code critic", "prompt": "You are a critic."},
    )
    assert agent is not None
    assert agent.agent_type == "critic"
    assert agent.when_to_use == "Senior code critic"
    assert agent.get_system_prompt() == "You are a critic."
    assert agent.tools is None  # default = all tools
    assert agent.source == "user"


def test_full_field_mapping():
    agent = parse_agent_from_json(
        "kitchen-sink",
        {
            "description": "Every field set",
            "prompt": "body",
            "tools": ["Read", "Grep"],
            "disallowedTools": ["Write"],
            "model": "claude-sonnet-4-6",
            "permissionMode": "acceptEdits",
            "maxTurns": 7,
            "background": True,
            "color": "blue",
            "memory": "project",
            "skills": ["my-skill"],
            "isolation": "worktree",
            "requiredMcpServers": ["slack"],
            "mcpServers": [{"slack": {"type": "stdio", "command": "x"}}],
            "effort": "high",
        },
        source="user",
    )
    assert agent is not None
    assert agent.tools == ["Read", "Grep"]
    assert agent.disallowed_tools == ["Write"]
    assert agent.model == "claude-sonnet-4-6"
    assert agent.permission_mode == "acceptEdits"
    assert agent.max_turns == 7
    assert agent.background is True
    assert agent.color == "blue"
    assert agent.memory == "project"
    assert agent.skills == ["my-skill"]
    assert agent.isolation == "worktree"
    assert agent.required_mcp_servers == ["slack"]
    assert agent.mcp_servers == [{"slack": {"type": "stdio", "command": "x"}}]
    assert agent.effort == "high"


def test_kebab_case_keys_also_accepted():
    agent = parse_agent_from_json(
        "kebab",
        {
            "description": "k",
            "prompt": "p",
            "disallowed-tools": ["Write"],
            "permission-mode": "default",
            "max-turns": 3,
            "required-mcp-servers": ["foo"],
            "mcp-servers": ["bar"],
        },
    )
    assert agent is not None
    assert agent.disallowed_tools == ["Write"]
    assert agent.permission_mode == "default"
    assert agent.max_turns == 3
    assert agent.required_mcp_servers == ["foo"]


def test_missing_description_returns_none():
    assert parse_agent_from_json("x", {"prompt": "p"}) is None


def test_missing_prompt_returns_none():
    assert parse_agent_from_json("x", {"description": "d"}) is None


def test_non_dict_returns_none():
    assert parse_agent_from_json("x", "not-a-dict") is None
    assert parse_agent_from_json("x", None) is None


def test_parse_agents_from_json_skips_bad_entries():
    agents = parse_agents_from_json(
        {
            "good": {"description": "g", "prompt": "p"},
            "bad": {"description": "missing prompt"},
        }
    )
    types = {a.agent_type for a in agents}
    assert types == {"good"}


def test_parse_agents_from_json_returns_empty_for_non_dict():
    assert parse_agents_from_json([]) == []
    assert parse_agents_from_json(None) == []


def test_tools_star_means_all():
    agent = parse_agent_from_json(
        "c",
        {"description": "d", "prompt": "p", "tools": ["*"]},
    )
    assert agent is not None
    assert agent.tools is None
