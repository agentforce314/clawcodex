"""Parse a markdown agent definition into an ``AgentDefinition``.

Mirrors ``parseAgentFromMarkdown`` in typescript/src/tools/AgentTool/loadAgentsDir.ts.

Field mapping (frontmatter → AgentDefinition):
    name                    → agent_type   (defaults to filename stem)
    description             → when_to_use  (required)
    tools                   → tools        (None / ['*'] both mean "all")
    disallowed-tools        → disallowed_tools
    disallowedTools         → disallowed_tools (camelCase alias)
    model                   → model        ('inherit' kept as a literal)
    permission-mode         → permission_mode
    permissionMode          → permission_mode (camelCase alias)
    max-turns               → max_turns
    maxTurns                → max_turns (camelCase alias)
    background              → background
    color                   → color
    memory                  → memory
    omit-claude-md          → omit_claude_md
    omitClaudeMd            → omit_claude_md (camelCase alias)
    hooks                   → hooks
    skills                  → skills
    isolation               → isolation
    required-mcp-servers    → required_mcp_servers
    requiredMcpServers      → required_mcp_servers (camelCase alias)
    mcp-servers             → mcp_servers
    mcpServers              → mcp_servers (camelCase alias)
    effort                  → effort

The markdown body becomes the agent's system prompt, returned by
``agent.get_system_prompt()``. Missing required fields produce ``None``
with a debug log; the loader silently drops the file rather than crash.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.agent.agent_definitions import AgentDefinition, AgentSource
from src.tool_system_ext.bundles import TOOL_BUNDLES
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

# Valid bundle names for tool expansion
_VALID_BUNDLES: frozenset[str] = frozenset(TOOL_BUNDLES.keys())


def _first(d: dict[str, Any], *keys: str) -> Any:
    """Return the first non-``None`` value among ``keys`` in ``d``."""
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


def _parse_tools(value: Any) -> list[str] | None:
    """Parse a tools list. ``None`` / ``['*']`` both mean "all tools".

    Bundle expansion: items starting with ':' are expanded from bundle.
    e.g. [":default", "Bash"] -> ["Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebSearch", "WebFetch", "Bash"]

    Returns ``None`` to signal all-tools (matches AgentDefinition.tools
    semantics: ``None`` or ``['*']`` both mean unrestricted).
    """
    if value is None:
        return None
    parsed = parse_string_list(value)
    if not parsed:
        return []
    if "*" in parsed:
        return None

    # Expand bundle references (items starting with ':')
    result: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.startswith(":") and item[1:] in _VALID_BUNDLES:
            bundle_name = item[1:]
            result.extend(TOOL_BUNDLES[bundle_name])
        elif isinstance(item, str):
            result.append(item)

    return result if result else []


def _parse_color(value: Any) -> str | None:
    if value is None or not isinstance(value, str):
        return None
    color = value.strip().lower()
    return color if color in AGENT_COLORS else None


def _parse_memory(value: Any, *, file_path: str) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    if s in VALID_MEMORY_SCOPES:
        return s
    logger.debug(
        "agent %s: invalid memory=%r (valid: %s)",
        file_path, value, ", ".join(sorted(VALID_MEMORY_SCOPES)),
    )
    return None


def _parse_isolation(value: Any, *, file_path: str) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    if s in VALID_ISOLATION_MODES:
        return s
    logger.debug(
        "agent %s: invalid isolation=%r (valid: %s)",
        file_path, value, ", ".join(sorted(VALID_ISOLATION_MODES)),
    )
    return None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return False


def _parse_model(value: Any) -> str | None:
    """Return ``'inherit'``, a concrete model string, or ``None``."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return "inherit" if s.lower() == "inherit" else s


def parse_agent_from_markdown(
    file_path: str,
    frontmatter: dict[str, Any],
    body: str,
    source: AgentSource,
    base_dir: str,
) -> AgentDefinition | None:
    """Map a parsed markdown agent definition to an ``AgentDefinition``.

    Returns ``None`` (with a debug log) when the required ``description``
    field is missing. Never raises — every other invalid field is dropped
    silently so a single bad value doesn't prevent the agent from loading.
    """
    raw_name = _first(frontmatter, "name")
    if raw_name is not None and not isinstance(raw_name, str):
        # Reject non-string names (TS does the same). YAML can coerce
        # ``name: true`` to a bool or ``name: 12345`` to an int; treating
        # those as the agent_type would silently register agents that
        # can't be invoked via ``@agent-True`` mention syntax.
        logger.debug(
            "agent file %s: 'name' must be a string (got %s); using filename",
            file_path, type(raw_name).__name__,
        )
        raw_name = None
    agent_type = (raw_name or Path(file_path).stem).strip()
    if not agent_type:
        logger.debug("agent file %s has empty name; skipping", file_path)
        return None

    description = _first(frontmatter, "description")
    if not description or not isinstance(description, str):
        logger.debug(
            "agent file %s is missing required 'description'; skipping",
            file_path,
        )
        return None
    when_to_use = description.replace("\\n", "\n")

    tools = _parse_tools(_first(frontmatter, "tools"))

    disallowed_raw = _first(frontmatter, "disallowed-tools", "disallowedTools")
    disallowed_tools = parse_string_list(disallowed_raw) if disallowed_raw is not None else None

    model = _parse_model(_first(frontmatter, "model"))
    permission_mode = parse_permission_mode(
        _first(frontmatter, "permission-mode", "permissionMode")
    )
    max_turns = parse_positive_int(_first(frontmatter, "max-turns", "maxTurns"))
    background = _parse_bool(_first(frontmatter, "background"))
    color = _parse_color(_first(frontmatter, "color"))
    memory = _parse_memory(_first(frontmatter, "memory"), file_path=file_path)
    omit_claude_md = _parse_bool(_first(frontmatter, "omit-claude-md", "omitClaudeMd"))
    hooks = parse_hooks(_first(frontmatter, "hooks"), owner_name=f"agent {agent_type}")
    skills = parse_string_list(_first(frontmatter, "skills"))
    isolation = _parse_isolation(
        _first(frontmatter, "isolation"), file_path=file_path
    )
    required_mcp_servers = parse_string_list(
        _first(frontmatter, "required-mcp-servers", "requiredMcpServers")
    )
    mcp_servers_raw = _first(frontmatter, "mcp-servers", "mcpServers")
    mcp_servers: list[Any] | None = (
        list(mcp_servers_raw) if isinstance(mcp_servers_raw, list) else None
    )
    effort = parse_effort_value(_first(frontmatter, "effort"))

    body_text = body.strip()

    def _get_system_prompt(**_kwargs: Any) -> str:
        return body_text

    return AgentDefinition(
        agent_type=agent_type,
        when_to_use=when_to_use,
        tools=tools,
        source=source,
        base_dir=base_dir,
        model=model,
        permission_mode=permission_mode,
        max_turns=max_turns,
        background=background,
        color=color,
        memory=memory,
        omit_claude_md=omit_claude_md,
        disallowed_tools=disallowed_tools,
        hooks=hooks,
        skills=skills or None,
        isolation=isolation,  # type: ignore[arg-type]
        required_mcp_servers=required_mcp_servers or None,
        mcp_servers=mcp_servers,
        effort=effort,
        get_system_prompt=_get_system_prompt,
    )
