"""Parse agent definitions supplied as inline JSON (SDK / flag sources).

Port of ``parseAgentFromJson`` / ``parseAgentsFromJson`` in
typescript/src/tools/AgentTool/loadAgentsDir.ts:435-526. Used today by
the SDK ``initialize`` control request, which lets a programmatic caller
inject custom agents without writing markdown files to disk.

Schema (matches TS ``AgentJsonSchema`` exactly — camelCase keys only,
typed values):

    {
        "<agent-name>": {
            "description": "...",         # required, string
            "prompt": "...",              # required, string; becomes get_system_prompt()
            "tools": ["Read", "Grep"],    # optional, list[str]; ['*'] / omitted = all tools
            "disallowedTools": [...],     # optional, list[str]
            "model": "claude-sonnet-4-6", # optional; 'inherit' kept as literal
            "permissionMode": "default",  # optional
            "maxTurns": 12,               # optional, positive int
            "background": false,          # optional, bool
            "skills": ["my-skill"],       # optional, list[str]
            "memory": "user",             # optional
            "isolation": "worktree",      # optional
            "effort": "high",             # optional
            "mcpServers": [...],          # optional, list
            "hooks": {...}                # optional, dict
        },
        ...
    }

Unlike the markdown parser, this loader rejects type mismatches outright
(e.g. ``tools: "Read"`` returns ``None``) to match TS Zod behaviour —
JSON is a typed wire format and silent coercion would mask callsite bugs.
"""
from __future__ import annotations

import logging
from typing import Any

from src.agent.agent_definitions import AgentDefinition, AgentSource
from src.utils.frontmatter_validators import (
    parse_effort_value,
    parse_hooks,
    parse_permission_mode,
)

logger = logging.getLogger(__name__)


AGENT_COLORS: frozenset[str] = frozenset(
    {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
)
VALID_MEMORY_SCOPES: frozenset[str] = frozenset({"user", "project", "local"})
VALID_ISOLATION_MODES: frozenset[str] = frozenset({"worktree", "remote"})


# Sentinel returned by ``_parse_tools_strict`` to signal "reject the whole
# agent" — distinct from ``None`` (= all tools) and ``[]`` (= no tools).
_INVALID = object()


def _parse_tools_strict(value: Any, *, field_name: str, agent_name: str) -> list[str] | None | object:
    """JSON-strict tools parser. ``None`` / missing → all tools (returns ``None``).

    Unlike the markdown loader, JSON values come from a typed wire format
    (TS uses Zod ``z.array(z.string())``). A scalar string is a genuine
    callsite bug and must be rejected rather than silently coerced —
    matching TS behaviour where Zod throws on type mismatch.

    Returns:
        * ``None`` — missing or contains ``"*"``: all tools.
        * ``list[str]`` — explicit allowlist.
        * ``_INVALID`` sentinel — caller should reject the whole agent.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        logger.debug(
            "agent %s: %s must be a list of strings, got %s — rejecting agent",
            agent_name, field_name, type(value).__name__,
        )
        return _INVALID
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            logger.debug(
                "agent %s: %s contains non-string entry %r — rejecting agent",
                agent_name, field_name, item,
            )
            return _INVALID
        s = item.strip()
        if s:
            cleaned.append(s)
    if "*" in cleaned:
        return None
    return cleaned


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
    source: AgentSource = "flag",
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
    if not isinstance(name, str):
        logger.debug("agent JSON has non-string name %r; skipping", name)
        return None
    agent_type = name.strip()
    if not agent_type:
        logger.debug("agent JSON has empty name; skipping")
        return None

    description = definition.get("description")
    if not isinstance(description, str) or not description.strip():
        logger.debug("agent %s: missing required 'description'", agent_type)
        return None

    system_prompt = definition.get("prompt")
    if not isinstance(system_prompt, str) or not system_prompt:
        logger.debug("agent %s: missing or empty required 'prompt'", agent_type)
        return None
    body_text = system_prompt

    # JSON wire format is typed; mirror TS Zod by only accepting camelCase
    # keys and list-shaped collections. A scalar where a list is expected
    # is a callsite bug — fail loud rather than silently coerce.
    tools = _parse_tools_strict(
        definition.get("tools"), field_name="tools", agent_name=agent_type
    )
    if tools is _INVALID:
        return None

    disallowed_raw = definition.get("disallowedTools")
    if disallowed_raw is None:
        disallowed_tools: list[str] | None = None
    elif not isinstance(disallowed_raw, list) or not all(
        isinstance(x, str) for x in disallowed_raw
    ):
        logger.debug(
            "agent %s: disallowedTools must be list[str], got %r — rejecting agent",
            agent_type, disallowed_raw,
        )
        return None
    else:
        disallowed_tools = [s.strip() for s in disallowed_raw if s.strip()]

    model = _parse_model(definition.get("model"))
    permission_mode = parse_permission_mode(definition.get("permissionMode"))
    # JSON-strict: TS Zod uses ``z.number().int().positive()`` here, so we
    # reject string-encoded ints too (markdown's ``parse_positive_int`` would
    # accept them — that's the parser shape mismatch flagged by the critic).
    max_turns_raw = definition.get("maxTurns")
    max_turns: int | None
    if max_turns_raw is None:
        max_turns = None
    elif isinstance(max_turns_raw, bool) or not isinstance(max_turns_raw, int):
        max_turns = None
    elif max_turns_raw <= 0:
        max_turns = None
    else:
        max_turns = max_turns_raw
    background_raw = definition.get("background")
    background = background_raw if isinstance(background_raw, bool) else False
    color = _parse_color(definition.get("color"))
    memory = _enum_or_none(definition.get("memory"), VALID_MEMORY_SCOPES)
    isolation = _enum_or_none(definition.get("isolation"), VALID_ISOLATION_MODES)
    hooks = parse_hooks(definition.get("hooks"), owner_name=f"agent {agent_type}")

    skills_raw = definition.get("skills")
    if skills_raw is None:
        skills: list[str] = []
    elif not isinstance(skills_raw, list) or not all(
        isinstance(x, str) for x in skills_raw
    ):
        logger.debug(
            "agent %s: skills must be list[str], got %r — dropping field",
            agent_type, skills_raw,
        )
        skills = []
    else:
        skills = [s.strip() for s in skills_raw if s.strip()]

    mcp_servers_raw = definition.get("mcpServers")
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
        required_mcp_servers=None,  # not in TS JSON schema; markdown-only
        mcp_servers=mcp_servers,
        effort=effort,
        get_system_prompt=_get_system_prompt,
    )


def parse_agents_from_json(
    agents_json: Any,
    source: AgentSource = "flag",
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
        if not isinstance(name, str):
            logger.debug("parse_agents_from_json: dropping non-string key %r", name)
            continue
        agent = parse_agent_from_json(name, definition, source)
        if agent is not None:
            out.append(agent)
    return out
