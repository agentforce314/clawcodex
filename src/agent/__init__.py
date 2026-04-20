"""Agent module for Claw Codex."""

from .conversation import Conversation, Message
from .session import Session

from .agent_definitions import (
    AgentDefinition,
    AgentSource,
    BuiltInAgentDefinition,
    EXPLORE_AGENT,
    FORK_AGENT,
    GENERAL_PURPOSE_AGENT,
    PLAN_AGENT,
    find_agent_by_type,
    get_built_in_agents,
    is_built_in_agent,
)
from .agent_tool_utils import (
    AgentToolResult,
    ResolvedAgentTools,
    count_tool_uses,
    extract_partial_result,
    filter_tools_for_agent,
    finalize_agent_tool,
    resolve_agent_tools,
)
from .constants import (
    AGENT_TOOL_NAME,
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    DEFAULT_AGENT_PROMPT,
    FORK_SUBAGENT_TYPE,
    LEGACY_AGENT_TOOL_NAME,
    ONE_SHOT_BUILTIN_AGENT_TYPES,
)
from .prompt import (
    format_agent_line,
    get_agent_prompt,
    get_agent_system_prompt,
)
from .run_agent import (
    RunAgentParams,
    RunAgentResult,
    filter_incomplete_tool_calls,
    resolve_permission_mode,
    run_agent,
)
from .subagent_context import (
    SubagentContextOverrides,
    create_subagent_context,
)

__all__ = [
    # Legacy
    "Conversation",
    "Message",
    "Session",
    # Agent definitions
    "AgentDefinition",
    "AgentSource",
    "BuiltInAgentDefinition",
    "EXPLORE_AGENT",
    "FORK_AGENT",
    "GENERAL_PURPOSE_AGENT",
    "PLAN_AGENT",
    "find_agent_by_type",
    "get_built_in_agents",
    "is_built_in_agent",
    # Agent tool utils
    "AgentToolResult",
    "ResolvedAgentTools",
    "count_tool_uses",
    "extract_partial_result",
    "filter_tools_for_agent",
    "finalize_agent_tool",
    "resolve_agent_tools",
    # Constants
    "AGENT_TOOL_NAME",
    "ALL_AGENT_DISALLOWED_TOOLS",
    "ASYNC_AGENT_ALLOWED_TOOLS",
    "CUSTOM_AGENT_DISALLOWED_TOOLS",
    "DEFAULT_AGENT_PROMPT",
    "FORK_SUBAGENT_TYPE",
    "LEGACY_AGENT_TOOL_NAME",
    "ONE_SHOT_BUILTIN_AGENT_TYPES",
    # Prompt
    "format_agent_line",
    "get_agent_prompt",
    "get_agent_system_prompt",
    # Run agent
    "RunAgentParams",
    "RunAgentResult",
    "filter_incomplete_tool_calls",
    "resolve_permission_mode",
    "run_agent",
    # Subagent context
    "SubagentContextOverrides",
    "create_subagent_context",
]
