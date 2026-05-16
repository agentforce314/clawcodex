"""Parse agent definitions supplied as inline JSON (SDK / flag sources).

Port of ``parseAgentFromJson`` / ``parseAgentsFromJson`` in
typescript/src/tools/AgentTool/loadAgentsDir.ts:435-526. Used today by
the SDK ``initialize`` control request, which lets a programmatic caller
inject custom agents without writing markdown files to disk.

Schema (matches TS ``AgentJsonSchema``):

    {
        "<agent-name>": {
            "description": "...",         # required
            "prompt": "...",              # required; becomes get_system_prompt()
            "tools": ["Read", "Grep"],    # optional; ['*'] / omitted = all tools
            "disallowedTools": [...],     # optional
            "model": "claude-sonnet-4-6", # optional; 'inherit' kept as literal
            "permissionMode": "default",  # optional
            "maxTurns": 12,               # optional
            "background": false,          # optional
            "skills": ["my-skill"],       # optional
            "initialPrompt": "...",       # optional (not yet plumbed)
            "memory": "user",             # optional
            "isolation": "worktree",      # optional
            "effort": "high",             # optional
            "mcpServers": [...],          # optional
            "hooks": {...}                # optional
        },
        ...
    }
"""
from __future__ import annotations

import logging
from typing import Any

from src.agent.agent_definitions import AgentDefinition, AgentSource
from src.utils.frontmatter_validators import (
    parse_effort_value,
    parse_hooks,
    parse_permission_mode,
    parse_positive_int,
    parse_string_list,
)

logger = logging.getLogger(__name__)


AGENT_COLORS: frozenset[str] = frozenset(
    {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
)
VALID_MEMORY_SCOPES: frozenset[str] = frozenset({"user", "project", "local"})
VALID_ISOLATION_MODES: frozenset[str] = frozenset({"worktree", "remote"})


def _parse_tools(value: Any) -> list[str] | None:
    """``None`` / missing / ``['*']`` all mean "all tools" (returns ``None``)."""
    if value is None:
        return None
    parsed = parse_string_list(value)
    if not parsed:
        return []
    if "*" in parsed:
        return None
    return parsed


def _parse_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    color = value.strip().lower()
    return color if color in AGENT_COLORS else None


def _enum_or_none(value: Any, valid: frozenset[str]) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    return s if s in valid else None


def _parse_model(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return "inherit" if s.lower() == "inherit" else s


def parse_agent_from_json(
    name: str,
    definition: Any,
    source: AgentSource = "user",
) -> AgentDefinition | None:
    """Build an ``AgentDefinition`` from a JSON object.

    Returns ``None`` (with a debug log) when ``definition`` is not a dict
    or is missing the required ``description`` / ``prompt`` fields. Never
    raises — invalid optional fields are dropped silently to match the
    fail-open behaviour of ``parse_agent_from_markdown``.
    """
    if not isinstance(definition, dict):
        logger.debug("agent %s: JSON definition must be a dict, got %s",
                     name, type(definition).__name__)
        return None
    agent_type = (name or "").strip()
    if not agent_type:
        logger.debug("agent JSON has empty name; skipping")
        return None

    description = definition.get("description")
    if not isinstance(description, str) or not description.strip():
        logger.debug("agent %s: missing required 'description'", agent_type)
        return None

    system_prompt = definition.get("prompt")
    if not isinstance(system_prompt, str):
        logger.debug("agent %s: missing required 'prompt'", agent_type)
        return None
    body_text = system_prompt

    tools = _parse_tools(definition.get("tools"))

    disallowed_raw = definition.get("disallowedTools")
    if disallowed_raw is None:
        disallowed_raw = definition.get("disallowed-tools")
    disallowed_tools = (
        parse_string_list(disallowed_raw) if disallowed_raw is not None else None
    )

    model = _parse_model(definition.get("model"))
    permission_mode = parse_permission_mode(
        definition.get("permissionMode") or definition.get("permission-mode")
    )
    max_turns = parse_positive_int(
        definition.get("maxTurns") if definition.get("maxTurns") is not None
        else definition.get("max-turns")
    )
    background_raw = definition.get("background")
    background = bool(background_raw) if isinstance(background_raw, bool) else False
    color = _parse_color(definition.get("color"))
    memory = _enum_or_none(definition.get("memory"), VALID_MEMORY_SCOPES)
    isolation = _enum_or_none(definition.get("isolation"), VALID_ISOLATION_MODES)
    hooks = parse_hooks(definition.get("hooks"), owner_name=f"agent {agent_type}")
    skills = parse_string_list(definition.get("skills"))
    required_mcp_servers = parse_string_list(
        definition.get("requiredMcpServers")
        if definition.get("requiredMcpServers") is not None
        else definition.get("required-mcp-servers")
    )
    mcp_servers_raw = (
        definition.get("mcpServers")
        if definition.get("mcpServers") is not None
        else definition.get("mcp-servers")
    )
    mcp_servers: list[Any] | None = (
        list(mcp_servers_raw) if isinstance(mcp_servers_raw, list) else None
    )
    effort = parse_effort_value(definition.get("effort"))

    def _get_system_prompt(**_kwargs: Any) -> str:
        return body_text

    return AgentDefinition(
        agent_type=agent_type,
        when_to_use=description,
        tools=tools,
        source=source,
        base_dir="json",
        model=model,
        permission_mode=permission_mode,
        max_turns=max_turns,
        background=background,
        color=color,
        memory=memory,
        omit_claude_md=False,
        disallowed_tools=disallowed_tools,
        hooks=hooks,
        skills=skills or None,
        isolation=isolation,  # type: ignore[arg-type]
        required_mcp_servers=required_mcp_servers or None,
        mcp_servers=mcp_servers,
        effort=effort,
        get_system_prompt=_get_system_prompt,
    )


def parse_agents_from_json(
    agents_json: Any,
    source: AgentSource = "user",
) -> list[AgentDefinition]:
    """Parse a ``{name: definition, ...}`` JSON object into agent definitions.

    Entries that fail validation are silently dropped (matches TS).
    """
    if not isinstance(agents_json, dict):
        logger.debug("parse_agents_from_json: expected dict, got %s",
                     type(agents_json).__name__)
        return []
    out: list[AgentDefinition] = []
    for name, definition in agents_json.items():
        agent = parse_agent_from_json(str(name), definition, source)
        if agent is not None:
            out.append(agent)
    return out
