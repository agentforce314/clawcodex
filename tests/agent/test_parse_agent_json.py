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
    assert agent.source == "flag"


def test_full_field_mapping():
    """All TS JSON schema fields round-trip. ``requiredMcpServers`` is NOT
    in the JSON schema (markdown-only) — dropped silently for parity."""
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
            "mcpServers": [{"slack": {"type": "stdio", "command": "x"}}],
            "effort": "high",
        },
        source="flag",
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
    assert agent.mcp_servers == [{"slack": {"type": "stdio", "command": "x"}}]
    assert agent.effort == "high"
    assert agent.source == "flag"


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


def test_scalar_tools_rejected_in_json():
    """Wire format is typed; a string where a list is expected rejects the agent."""
    assert parse_agent_from_json(
        "c", {"description": "d", "prompt": "p", "tools": "Read"}
    ) is None


def test_kebab_keys_no_longer_accepted_in_json():
    """JSON sticks to camelCase only (matches TS Zod schema)."""
    agent = parse_agent_from_json(
        "c",
        {
            "description": "d",
            "prompt": "p",
            "permission-mode": "acceptEdits",  # kebab key — should be ignored
            "max-turns": 9,
        },
    )
    assert agent is not None
    assert agent.permission_mode is None
    assert agent.max_turns is None


def test_default_source_is_flag():
    """Bridge-injected agents must land in the 'flag' source slot."""
    agent = parse_agent_from_json("c", {"description": "d", "prompt": "p"})
    assert agent is not None
    assert agent.source == "flag"


def test_non_string_dict_key_dropped():
    from src.agent.parse_agent_json import parse_agents_from_json
    agents = parse_agents_from_json({
        None: {"description": "d", "prompt": "p"},  # type: ignore[dict-item]
        "good": {"description": "d", "prompt": "p"},
    })
    assert [a.agent_type for a in agents] == ["good"]


def test_non_list_disallowed_tools_rejects_agent():
    assert parse_agent_from_json(
        "c", {"description": "d", "prompt": "p", "disallowedTools": "Write"}
    ) is None
