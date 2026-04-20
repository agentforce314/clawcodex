"""Agent tool filtering and resolution utilities.

Mirrors typescript/src/tools/AgentTool/agentToolUtils.ts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..permissions.types import PermissionMode
from ..tool_system.build_tool import Tool, Tools, tool_matches_name
from ..types.content_blocks import ToolUseBlock
from ..types.messages import AssistantMessage, Message

from .constants import (
    AGENT_TOOL_NAME,
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
)

logger = logging.getLogger(__name__)


@dataclass
class ResolvedAgentTools:
    """Result of resolving agent tools against available tools."""
    has_wildcard: bool
    valid_tools: list[str]
    invalid_tools: list[str]
    resolved_tools: Tools
    allowed_agent_types: list[str] | None = None


def filter_tools_for_agent(
    *,
    tools: Tools,
    is_built_in: bool,
    is_async: bool = False,
    permission_mode: PermissionMode | None = None,
) -> Tools:
    """Filter available tools based on agent type and mode.

    Mirrors filterToolsForAgent() from typescript/src/tools/AgentTool/agentToolUtils.ts.

    - MCP tools are always allowed for all agents.
    - ExitPlanMode is allowed for agents in plan mode.
    - ALL_AGENT_DISALLOWED_TOOLS are always blocked.
    - CUSTOM_AGENT_DISALLOWED_TOOLS are blocked for non-built-in agents.
    - Async agents are restricted to ASYNC_AGENT_ALLOWED_TOOLS whitelist.
    """
    result: Tools = []
    for tool in tools:
        # MCP tools always allowed
        if tool.name.startswith("mcp__") or tool.is_mcp:
            result.append(tool)
            continue

        # Allow ExitPlanMode for agents in plan mode
        if tool_matches_name(tool, "ExitPlanMode") and permission_mode == "plan":
            result.append(tool)
            continue

        # Block ALL_AGENT_DISALLOWED_TOOLS
        if tool.name in ALL_AGENT_DISALLOWED_TOOLS:
            continue

        # Block CUSTOM_AGENT_DISALLOWED_TOOLS for non-built-in agents
        if not is_built_in and tool.name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            continue

        # Async agents: only whitelisted tools
        if is_async and tool.name not in ASYNC_AGENT_ALLOWED_TOOLS:
            continue

        result.append(tool)

    return result


def resolve_agent_tools(
    agent_definition: Any,
    available_tools: Tools,
    is_async: bool = False,
) -> ResolvedAgentTools:
    """Resolve and validate agent tools against available tools.

    Mirrors resolveAgentTools() from typescript/src/tools/AgentTool/agentToolUtils.ts.

    Handles wildcard expansion, validation, and disallowed tool filtering.
    """
    from .agent_definitions import is_built_in_agent

    agent_tools = agent_definition.tools
    disallowed_tools = agent_definition.disallowed_tools
    source = agent_definition.source
    permission_mode = agent_definition.permission_mode

    # Apply base filtering
    filtered_available_tools = filter_tools_for_agent(
        tools=available_tools,
        is_built_in=(source == "built-in"),
        is_async=is_async,
        permission_mode=permission_mode,
    )

    # Build disallowed set
    disallowed_set: set[str] = set()
    if disallowed_tools:
        for tool_spec in disallowed_tools:
            # Extract tool name from spec (may include pattern like "Tool(arg)")
            tool_name = _extract_tool_name(tool_spec)
            disallowed_set.add(tool_name)

    # Filter by disallowed list
    allowed_available_tools = [
        t for t in filtered_available_tools
        if t.name not in disallowed_set
    ]

    # If tools is None or ['*'], allow all tools (after filtering)
    has_wildcard = (
        agent_tools is None
        or (len(agent_tools) == 1 and agent_tools[0] == "*")
    )
    if has_wildcard:
        return ResolvedAgentTools(
            has_wildcard=True,
            valid_tools=[],
            invalid_tools=[],
            resolved_tools=allowed_available_tools,
        )

    # Build map of available tools
    available_map: dict[str, Tool] = {}
    for tool in allowed_available_tools:
        available_map[tool.name] = tool

    valid_tools: list[str] = []
    invalid_tools: list[str] = []
    resolved: list[Tool] = []
    resolved_set: set[str] = set()
    allowed_agent_types: list[str] | None = None

    for tool_spec in agent_tools:
        tool_name = _extract_tool_name(tool_spec)
        rule_content = _extract_rule_content(tool_spec)

        # Special case: Agent tool carries allowedAgentTypes
        if tool_name == AGENT_TOOL_NAME:
            if rule_content:
                allowed_agent_types = [s.strip() for s in rule_content.split(",")]
            valid_tools.append(tool_spec)
            continue

        tool = available_map.get(tool_name)
        if tool:
            valid_tools.append(tool_spec)
            if tool.name not in resolved_set:
                resolved.append(tool)
                resolved_set.add(tool.name)
        else:
            invalid_tools.append(tool_spec)

    return ResolvedAgentTools(
        has_wildcard=False,
        valid_tools=valid_tools,
        invalid_tools=invalid_tools,
        resolved_tools=resolved,
        allowed_agent_types=allowed_agent_types,
    )


@dataclass
class AgentToolResult:
    """Result data from a completed agent run."""
    agent_id: str
    agent_type: str
    content: list[dict[str, Any]]
    total_duration_ms: int
    total_tokens: int
    total_tool_use_count: int


def count_tool_uses(messages: list[Message]) -> int:
    """Count the number of tool_use blocks across all assistant messages."""
    count = 0
    for m in messages:
        if isinstance(m, AssistantMessage):
            content = m.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolUseBlock):
                        count += 1
    return count


def finalize_agent_tool(
    agent_messages: list[Message],
    agent_id: str,
    metadata: dict[str, Any],
) -> AgentToolResult:
    """Extract final result from agent messages.

    Mirrors finalizeAgentTool() from typescript/src/tools/AgentTool/agentToolUtils.ts.
    """
    # Find the last assistant message
    last_assistant: AssistantMessage | None = None
    for msg in reversed(agent_messages):
        if isinstance(msg, AssistantMessage):
            last_assistant = msg
            break

    if last_assistant is None:
        raise ValueError("No assistant messages found")

    # Extract text content from the agent's response
    content: list[dict[str, Any]] = []
    raw_content = last_assistant.content
    if isinstance(raw_content, str):
        if raw_content:
            content = [{"type": "text", "text": raw_content}]
    elif isinstance(raw_content, list):
        for block in raw_content:
            if hasattr(block, "type") and block.type == "text":
                content.append({"type": "text", "text": block.text})

    # If no text content in last message, search backwards
    if not content:
        for msg in reversed(agent_messages):
            if isinstance(msg, AssistantMessage):
                raw = msg.content
                if isinstance(raw, list):
                    for block in raw:
                        if hasattr(block, "type") and block.type == "text":
                            content.append({"type": "text", "text": block.text})
                    if content:
                        break

    total_tool_use_count = count_tool_uses(agent_messages)
    start_time = metadata.get("start_time", time.time())
    duration_ms = int((time.time() - start_time) * 1000)

    return AgentToolResult(
        agent_id=agent_id,
        agent_type=metadata.get("agent_type", ""),
        content=content,
        total_duration_ms=duration_ms,
        total_tokens=0,
        total_tool_use_count=total_tool_use_count,
    )


def extract_partial_result(messages: list[Message]) -> str | None:
    """Extract a partial result string from agent messages.

    Used when an async agent is killed to preserve what it accomplished.
    """
    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage):
            raw = msg.content
            if isinstance(raw, str) and raw.strip():
                return raw
            if isinstance(raw, list):
                texts = []
                for block in raw:
                    if hasattr(block, "type") and block.type == "text" and block.text:
                        texts.append(block.text)
                if texts:
                    return "\n".join(texts)
    return None


def _extract_tool_name(tool_spec: str) -> str:
    """Extract the base tool name from a spec like 'Tool(arg)'."""
    paren_idx = tool_spec.find("(")
    if paren_idx != -1:
        return tool_spec[:paren_idx].strip()
    return tool_spec.strip()


def _extract_rule_content(tool_spec: str) -> str | None:
    """Extract rule content from a spec like 'Tool(arg1, arg2)'."""
    paren_idx = tool_spec.find("(")
    if paren_idx != -1 and tool_spec.endswith(")"):
        return tool_spec[paren_idx + 1:-1].strip()
    return None
