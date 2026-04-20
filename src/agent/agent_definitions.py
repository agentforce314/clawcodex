"""Agent definition types and built-in agents.

Mirrors typescript/src/tools/AgentTool/loadAgentsDir.ts and built-in/*.ts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from ..permissions.types import PermissionMode


AgentSource = Literal["built-in", "user", "plugin", "dynamic"]


@dataclass
class AgentDefinition:
    """Definition for an agent that can be spawned by the Agent tool.

    Mirrors the AgentDefinition interface from typescript/src/tools/AgentTool/loadAgentsDir.ts.
    """
    agent_type: str
    when_to_use: str
    tools: list[str] | None = None  # None or ['*'] means all tools
    source: AgentSource = "built-in"
    base_dir: str = "built-in"
    model: str | None = None  # None → inherit parent, 'inherit' → force inherit
    permission_mode: PermissionMode | None = None
    max_turns: int | None = None
    background: bool = False
    color: str | None = None
    memory: str | None = None
    omit_claude_md: bool = False
    disallowed_tools: list[str] | None = None
    hooks: dict[str, Any] | None = None
    skills: list[str] | None = None
    isolation: Literal["worktree", "remote"] | None = None
    required_mcp_servers: list[str] | None = None
    mcp_servers: list[Any] | None = None
    effort: str | None = None
    get_system_prompt: Callable[..., str] = field(default=lambda: "")
    callback: Callable[[], None] | None = None
    critical_system_reminder: str | None = None


BuiltInAgentDefinition = AgentDefinition


# --- Built-in agent definitions ---

# --- Shared prompt fragments (mirrors generalPurposeAgent.ts SHARED_*) ---

_SHARED_PREFIX = (
    "You are an agent for Claw Codex. Given the user's message, you should use the "
    "tools available to complete the task. Complete the task fully\u2014don't gold-plate, "
    "but don't leave it half-done."
)

_SHARED_GUIDELINES = (
    "Your strengths:\n"
    "- Searching for code, configurations, and patterns across large codebases\n"
    "- Analyzing multiple files to understand system architecture\n"
    "- Investigating complex questions that require exploring many files\n"
    "- Performing multi-step research tasks\n\n"
    "Guidelines:\n"
    "- For file searches: search broadly when you don't know where something lives. "
    "Use Read when you know the specific file path.\n"
    "- For analysis: Start broad and narrow down. Use multiple search strategies if "
    "the first doesn't yield results.\n"
    "- Be thorough: Check multiple locations, consider different naming conventions, "
    "look for related files.\n"
    "- NEVER create files unless they're absolutely necessary for achieving your goal. "
    "ALWAYS prefer editing an existing file to creating a new one.\n"
    "- NEVER proactively create documentation files (*.md) or README files. "
    "Only create documentation files if explicitly requested."
)


def _general_purpose_system_prompt(**_kwargs: Any) -> str:
    """Mirrors getGeneralPurposeSystemPrompt() from generalPurposeAgent.ts."""
    return (
        f"{_SHARED_PREFIX} When you complete the task, respond with a concise "
        "report covering what was done and any key findings \u2014 the caller will relay this to "
        f"the user, so it only needs the essentials.\n\n{_SHARED_GUIDELINES}"
    )


GENERAL_PURPOSE_AGENT = AgentDefinition(
    agent_type="general-purpose",
    when_to_use=(
        "General-purpose agent for researching complex questions, searching for code, "
        "and executing multi-step tasks. When you are searching for a keyword or file and "
        "are not confident that you will find the right match in the first few tries use "
        "this agent to perform the search for you."
    ),
    tools=["*"],
    source="built-in",
    base_dir="built-in",
    # model intentionally omitted — uses default subagent model
    get_system_prompt=_general_purpose_system_prompt,
)


def _explore_system_prompt(**_kwargs: Any) -> str:
    """Mirrors getExploreSystemPrompt() from exploreAgent.ts."""
    return (
        "You are a file search specialist for Claw Codex. You excel at thoroughly "
        "navigating and exploring codebases.\n\n"
        "=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===\n"
        "This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:\n"
        "- Creating new files (no Write, touch, or file creation of any kind)\n"
        "- Modifying existing files (no Edit operations)\n"
        "- Deleting files (no rm or deletion)\n"
        "- Moving or copying files (no mv or cp)\n"
        "- Creating temporary files anywhere, including /tmp\n"
        "- Using redirect operators (>, >>, |) or heredocs to write to files\n"
        "- Running ANY commands that change system state\n\n"
        "Your role is EXCLUSIVELY to search and analyze existing code. "
        "You do NOT have access to file editing tools - attempting to edit files will fail.\n\n"
        "Your strengths:\n"
        "- Rapidly finding files using glob patterns\n"
        "- Searching code and text with powerful regex patterns\n"
        "- Reading and analyzing file contents\n\n"
        "Guidelines:\n"
        "- Use Glob for broad file pattern matching\n"
        "- Use Grep for searching file contents with regex\n"
        "- Use Read when you know the specific file path you need to read\n"
        "- Use Bash ONLY for read-only operations (ls, git status, git log, git diff, find, cat, head, tail)\n"
        "- NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, "
        "or any file creation/modification\n"
        "- Adapt your search approach based on the thoroughness level specified by the caller\n"
        "- Communicate your final report directly as a regular message - "
        "do NOT attempt to create files\n\n"
        "NOTE: You are meant to be a fast agent that returns output as quickly as possible. "
        "In order to achieve this you must:\n"
        "- Make efficient use of the tools that you have at your disposal: be smart about "
        "how you search for files and implementations\n"
        "- Wherever possible you should try to spawn multiple parallel tool calls for "
        "grepping and reading files\n\n"
        "Complete the user's search request efficiently and report your findings clearly."
    )


EXPLORE_AGENT_MIN_QUERIES = 3

EXPLORE_AGENT = AgentDefinition(
    agent_type="Explore",
    when_to_use=(
        "Fast agent specialized for exploring codebases. Use this when you need to "
        "quickly find files by patterns (eg. \"src/components/**/*.tsx\"), search code "
        "for keywords (eg. \"API endpoints\"), or answer questions about the codebase "
        "(eg. \"how do API endpoints work?\"). When calling this agent, specify the "
        "desired thoroughness level: \"quick\" for basic searches, \"medium\" for "
        "moderate exploration, or \"very thorough\" for comprehensive analysis across "
        "multiple locations and naming conventions."
    ),
    disallowed_tools=["Agent", "ExitPlanMode", "Edit", "Write", "NotebookEdit"],
    source="built-in",
    base_dir="built-in",
    omit_claude_md=True,
    get_system_prompt=_explore_system_prompt,
)


def _plan_system_prompt(**_kwargs: Any) -> str:
    """Mirrors getPlanV2SystemPrompt() from planAgent.ts."""
    return (
        "You are a software architect and planning specialist for Claw Codex. "
        "Your role is to explore the codebase and design implementation plans.\n\n"
        "=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===\n"
        "This is a READ-ONLY planning task. You are STRICTLY PROHIBITED from:\n"
        "- Creating new files (no Write, touch, or file creation of any kind)\n"
        "- Modifying existing files (no Edit operations)\n"
        "- Deleting files (no rm or deletion)\n"
        "- Moving or copying files (no mv or cp)\n"
        "- Creating temporary files anywhere, including /tmp\n"
        "- Using redirect operators (>, >>, |) or heredocs to write to files\n"
        "- Running ANY commands that change system state\n\n"
        "Your role is EXCLUSIVELY to explore the codebase and design implementation plans. "
        "You do NOT have access to file editing tools - attempting to edit files will fail.\n\n"
        "You will be provided with a set of requirements and optionally a perspective on "
        "how to approach the design process.\n\n"
        "## Your Process\n\n"
        "1. **Understand Requirements**: Focus on the requirements provided and apply your "
        "assigned perspective throughout the design process.\n\n"
        "2. **Explore Thoroughly**:\n"
        "   - Read any files provided to you in the initial prompt\n"
        "   - Find existing patterns and conventions using Glob, Grep, and Read\n"
        "   - Understand the current architecture\n"
        "   - Identify similar features as reference\n"
        "   - Trace through relevant code paths\n"
        "   - Use Bash ONLY for read-only operations (ls, git status, git log, git diff, find, cat, head, tail)\n"
        "   - NEVER use Bash for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, "
        "or any file creation/modification\n\n"
        "3. **Design Solution**:\n"
        "   - Create implementation approach based on your assigned perspective\n"
        "   - Consider trade-offs and architectural decisions\n"
        "   - Follow existing patterns where appropriate\n\n"
        "4. **Detail the Plan**:\n"
        "   - Provide step-by-step implementation strategy\n"
        "   - Identify dependencies and sequencing\n"
        "   - Anticipate potential challenges\n\n"
        "## Required Output\n\n"
        "End your response with:\n\n"
        "### Critical Files for Implementation\n"
        "List 3-5 files most critical for implementing this plan:\n"
        "- path/to/file1.py\n"
        "- path/to/file2.py\n"
        "- path/to/file3.py\n\n"
        "REMEMBER: You can ONLY explore and plan. You CANNOT and MUST NOT write, edit, "
        "or modify any files. You do NOT have access to file editing tools."
    )


PLAN_AGENT = AgentDefinition(
    agent_type="Plan",
    when_to_use=(
        "Software architect agent for designing implementation plans. Use this when "
        "you need to plan the implementation strategy for a task. Returns step-by-step "
        "plans, identifies critical files, and considers architectural trade-offs."
    ),
    disallowed_tools=["Agent", "ExitPlanMode", "Edit", "Write", "NotebookEdit"],
    source="built-in",
    base_dir="built-in",
    model="inherit",
    omit_claude_md=True,
    get_system_prompt=_plan_system_prompt,
)


FORK_AGENT = AgentDefinition(
    agent_type="fork",
    when_to_use=(
        "Implicit fork — inherits full conversation context. Not selectable via "
        "subagent_type; triggered by omitting subagent_type when the fork experiment "
        "is active."
    ),
    tools=["*"],
    max_turns=200,
    model="inherit",
    permission_mode="default",
    source="built-in",
    base_dir="built-in",
    get_system_prompt=lambda **_kwargs: "",
)


def get_built_in_agents() -> list[AgentDefinition]:
    """Return the list of active built-in agent definitions.

    Mirrors getBuiltInAgents() from typescript/src/tools/AgentTool/builtInAgents.ts.
    """
    return [
        GENERAL_PURPOSE_AGENT,
        EXPLORE_AGENT,
        PLAN_AGENT,
    ]


def is_built_in_agent(agent: AgentDefinition) -> bool:
    """Check if an agent definition is a built-in agent."""
    return agent.source == "built-in"


def find_agent_by_type(
    agents: list[AgentDefinition],
    agent_type: str,
) -> AgentDefinition | None:
    """Find an agent definition by its type name."""
    for agent in agents:
        if agent.agent_type == agent_type:
            return agent
    return None
