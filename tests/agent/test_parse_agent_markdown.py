"""Tests for src/agent/parse_agent_markdown.py."""
from __future__ import annotations

from textwrap import dedent

from src.agent.parse_agent_markdown import parse_agent_from_markdown
from src.skills.frontmatter import parse_frontmatter


def _parse(content: str, *, file_path: str = "/tmp/some.md"):
    result = parse_frontmatter(content)
    return parse_agent_from_markdown(
        file_path=file_path,
        frontmatter=result.frontmatter,
        body=result.body,
        source="user",
        base_dir="/tmp",
    )


def test_parses_all_frontmatter_fields_to_agent_definition():
    content = dedent(
        """\
        ---
        name: kitchen-sink
        description: An agent with every field set
        tools:
          - Read
          - Grep
        disallowed-tools:
          - Write
        model: claude-sonnet-4-6
        permission-mode: acceptEdits
        max-turns: 12
        background: true
        color: blue
        memory: project
        omit-claude-md: true
        skills:
          - my-skill
        isolation: worktree
        required-mcp-servers:
          - slack
        mcp-servers:
          - some-server
        effort: high
        ---
        You are the kitchen sink agent.
        """
    )
    agent = _parse(content)
    assert agent is not None
    assert agent.agent_type == "kitchen-sink"
    assert agent.when_to_use == "An agent with every field set"
    assert agent.tools == ["Read", "Grep"]
    assert agent.disallowed_tools == ["Write"]
    assert agent.model == "claude-sonnet-4-6"
    assert agent.permission_mode == "acceptEdits"
    assert agent.max_turns == 12
    assert agent.background is True
    assert agent.color == "blue"
    assert agent.memory == "project"
    assert agent.omit_claude_md is True
    assert agent.skills == ["my-skill"]
    assert agent.isolation == "worktree"
    assert agent.required_mcp_servers == ["slack"]
    assert agent.mcp_servers == ["some-server"]
    assert agent.effort == "high"


def test_filename_used_when_name_field_absent():
    content = dedent(
        """\
        ---
        description: Description-only agent
        ---
        body
        """
    )
    agent = _parse(content, file_path="/tmp/critic.md")
    assert agent is not None
    assert agent.agent_type == "critic"


def test_body_becomes_system_prompt():
    body_text = "You are a critic.\nYou give critical reviews."
    content = "---\nname: c\ndescription: x\n---\n" + body_text + "\n"
    agent = _parse(content)
    assert agent is not None
    assert agent.get_system_prompt() == body_text


def test_invalid_permission_mode_dropped_not_crashed():
    content = dedent(
        """\
        ---
        name: looseperms
        description: tries to set garbage perms
        permission-mode: nope
        ---
        body
        """
    )
    agent = _parse(content)
    assert agent is not None
    assert agent.permission_mode is None
    assert agent.agent_type == "looseperms"


def test_missing_description_returns_none():
    content = "---\nname: nope\n---\nbody\n"
    agent = _parse(content)
    assert agent is None


def test_tools_star_means_all():
    content = "---\nname: c\ndescription: x\ntools:\n  - '*'\n---\nbody\n"
    agent = _parse(content)
    assert agent is not None
    assert agent.tools is None


def test_non_string_name_falls_back_to_filename():
    """``name: true`` (YAML coerces to bool) must not register as agent_type 'True'."""
    content = "---\nname: true\ndescription: oops\n---\nbody\n"
    agent = _parse(content, file_path="/tmp/realname.md")
    assert agent is not None
    assert agent.agent_type == "realname"


def test_camelcase_aliases_supported():
    """camelCase frontmatter keys parse the same as kebab-case."""
    content = (
        "---\n"
        "name: cc\n"
        "description: d\n"
        "permissionMode: acceptEdits\n"
        "maxTurns: 5\n"
        "disallowedTools:\n  - Write\n"
        "requiredMcpServers:\n  - slack\n"
        "---\n"
        "body\n"
    )
    agent = _parse(content)
    assert agent is not None
    assert agent.permission_mode == "acceptEdits"
    assert agent.max_turns == 5
    assert agent.disallowed_tools == ["Write"]
    assert agent.required_mcp_servers == ["slack"]
