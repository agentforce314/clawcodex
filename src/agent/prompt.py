"""Agent tool prompt generation.

Mirrors typescript/src/tools/AgentTool/prompt.ts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_definitions import AgentDefinition

# Tool name constants used in prompts
_FILE_READ_TOOL_NAME = "Read"
_FILE_WRITE_TOOL_NAME = "Write"
_GLOB_TOOL_NAME = "Glob"
_AGENT_TOOL_NAME = "Agent"
_SEND_MESSAGE_TOOL_NAME = "SendMessage"


def _get_tools_description(agent: AgentDefinition) -> str:
    """Build a human-readable description of an agent's available tools.

    Mirrors getToolsDescription() from typescript/src/tools/AgentTool/prompt.ts.
    """
    tools = agent.tools
    disallowed = agent.disallowed_tools

    has_allowlist = tools is not None and len(tools) > 0
    has_denylist = disallowed is not None and len(disallowed) > 0

    if has_allowlist and has_denylist:
        deny_set = set(disallowed)  # type: ignore[arg-type]
        effective = [t for t in tools if t not in deny_set]  # type: ignore[union-attr]
        return ", ".join(effective) if effective else "None"
    elif has_allowlist:
        return ", ".join(tools)  # type: ignore[arg-type]
    elif has_denylist:
        return f"All tools except {', '.join(disallowed)}"  # type: ignore[arg-type]
    return "All tools"


def format_agent_line(agent: AgentDefinition) -> str:
    """Format one agent line: ``- type: whenToUse (Tools: ...)``.

    Mirrors formatAgentLine() from typescript/src/tools/AgentTool/prompt.ts.
    """
    tools_desc = _get_tools_description(agent)
    return f"- {agent.agent_type}: {agent.when_to_use} (Tools: {tools_desc})"


def get_agent_prompt(
    agent_definitions: list[AgentDefinition],
    *,
    allow_async: bool = True,
    allow_fork: bool = False,
) -> str:
    """Build the full prompt for the Agent tool.

    Mirrors getPrompt() from typescript/src/tools/AgentTool/prompt.ts.
    This text is fed to the model as the Agent tool's description, instructing
    the parent agent on how and when to spawn sub-agents.
    """
    # --- Shared core prompt (used by both coordinator and non-coordinator) ---
    agent_list = "\n".join(format_agent_line(a) for a in agent_definitions)

    shared = (
        f"Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
        f"The {_AGENT_TOOL_NAME} tool launches specialized agents (subprocesses) that "
        f"autonomously handle complex tasks. Each agent type has specific capabilities "
        f"and tools available to it.\n\n"
        f"Available agent types and the tools they have access to:\n"
        f"{agent_list}\n\n"
        f"When using the {_AGENT_TOOL_NAME} tool, specify a subagent_type parameter "
        f"to select which agent type to use. If omitted, the general-purpose agent is used."
    )

    # --- "When NOT to use" section ---
    when_not_to_use = (
        f"\nWhen NOT to use the {_AGENT_TOOL_NAME} tool:\n"
        f"- If you want to read a specific file path, use the {_FILE_READ_TOOL_NAME} tool "
        f"or the {_GLOB_TOOL_NAME} tool instead of the {_AGENT_TOOL_NAME} tool, "
        f"to find the match more quickly\n"
        f"- If you are searching for a specific class definition like \"class Foo\", "
        f"use the {_GLOB_TOOL_NAME} tool instead, to find the match more quickly\n"
        f"- If you are searching for code within a specific file or set of 2-3 files, "
        f"use the {_FILE_READ_TOOL_NAME} tool instead of the {_AGENT_TOOL_NAME} tool, "
        f"to find the match more quickly\n"
        f"- Other tasks that are not related to the agent descriptions above\n"
    )

    # --- "Writing the prompt" section ---
    writing_the_prompt = (
        f"\n## Writing the prompt\n\n"
        f"Brief the agent like a smart colleague who just walked into the room \u2014 "
        f"it hasn't seen this conversation, doesn't know what you've tried, doesn't "
        f"understand why this task matters.\n"
        f"- Explain what you're trying to accomplish and why.\n"
        f"- Describe what you've already learned or ruled out.\n"
        f"- Give enough context about the surrounding problem that the agent can make "
        f"judgment calls rather than just following a narrow instruction.\n"
        f"- If you need a short response, say so (\"report in under 200 words\").\n"
        f"- Lookups: hand over the exact command. Investigations: hand over the question "
        f"\u2014 prescribed steps become dead weight when the premise is wrong.\n\n"
        f"Terse command-style prompts produce shallow, generic work.\n\n"
        f"**Never delegate understanding.** Don't write \"based on your findings, fix the "
        f"bug\" or \"based on the research, implement it.\" Those phrases push synthesis "
        f"onto the agent instead of doing it yourself. Write prompts that prove you "
        f"understood: include file paths, line numbers, what specifically to change.\n"
    )

    # --- Usage notes ---
    usage_parts = [
        f"\nUsage notes:\n"
        f"- Always include a short description (3-5 words) summarizing what the agent will do",
    ]

    if allow_async:
        usage_parts.append(
            f"- You can optionally run agents in the background using the run_in_background "
            f"parameter. When an agent runs in the background, you will be automatically "
            f"notified when it completes \u2014 do NOT sleep, poll, or proactively check on "
            f"its progress. Continue with other work or respond to the user instead.\n"
            f"- **Foreground vs background**: Use foreground (default) when you need the "
            f"agent's results before you can proceed \u2014 e.g., research agents whose "
            f"findings inform your next steps. Use background when you have genuinely "
            f"independent work to do in parallel."
        )

    usage_parts.extend([
        f"- When the agent is done, it will return a single message back to you. "
        f"The result returned by the agent is not visible to the user. To show the "
        f"user the result, you should send a text message back to the user with a "
        f"concise summary of the result.",
        f"- To continue a previously spawned agent, use {_SEND_MESSAGE_TOOL_NAME} "
        f"with the agent's ID or name as the `to` field. The agent resumes with its "
        f"full context preserved. Each Agent invocation starts fresh \u2014 provide a "
        f"complete task description.",
        f"- The agent's outputs should generally be trusted",
        f"- Clearly tell the agent whether you expect it to write code or just to do "
        f"research (search, file reads, web fetches, etc.), since it is not aware of "
        f"the user's intent",
        f"- If the agent description mentions that it should be used proactively, then "
        f"you should try your best to use it without the user having to ask for it "
        f"first. Use your judgement.",
        f"- If the user specifies that they want you to run agents \"in parallel\", "
        f"you MUST send a single message with multiple {_AGENT_TOOL_NAME} tool use "
        f"content blocks. For example, if you need to launch both a build-validator "
        f"agent and a test-runner agent in parallel, send a single message with both "
        f"tool calls.",
    ])
    usage_notes = "\n".join(usage_parts)

    # --- Examples ---
    examples = (
        f"\nExample usage:\n\n"
        f"<example_agent_descriptions>\n"
        f"\"test-runner\": use this agent after you are done writing code to run tests\n"
        f"\"greeting-responder\": use this agent to respond to user greetings with a friendly joke\n"
        f"</example_agent_descriptions>\n\n"
        f"<example>\n"
        f"user: \"Please write a function that checks if a number is prime\"\n"
        f"assistant: I'm going to use the {_FILE_WRITE_TOOL_NAME} tool to write the following code:\n"
        f"<code>\n"
        f"def is_prime(n):\n"
        f"    if n <= 1:\n"
        f"        return False\n"
        f"    for i in range(2, int(n**0.5) + 1):\n"
        f"        if n % i == 0:\n"
        f"            return False\n"
        f"    return True\n"
        f"</code>\n"
        f"<commentary>\n"
        f"Since a significant piece of code was written and the task was completed, "
        f"now use the test-runner agent to run the tests\n"
        f"</commentary>\n"
        f"assistant: Uses the {_AGENT_TOOL_NAME} tool to launch the test-runner agent\n"
        f"</example>\n\n"
        f"<example>\n"
        f"user: \"Hello\"\n"
        f"<commentary>\n"
        f"Since the user is greeting, use the greeting-responder agent to respond with a friendly joke\n"
        f"</commentary>\n"
        f"assistant: \"I'm going to use the {_AGENT_TOOL_NAME} tool to launch the greeting-responder agent\"\n"
        f"</example>"
    )

    return f"{shared}\n{when_not_to_use}\n{usage_notes}\n{writing_the_prompt}\n{examples}"


def get_agent_system_prompt(
    agent_definition: AgentDefinition,
    parent_system_prompt: str | None = None,
) -> str:
    """Get the system prompt for an agent.

    Mirrors getAgentSystemPrompt() from typescript/src/tools/AgentTool/runAgent.ts.

    For built-in agents: uses the agent's own system prompt.
    For fork agents: uses the parent's system prompt.
    For custom agents: uses the agent's system prompt, falling back to a default.
    """
    from .constants import DEFAULT_AGENT_PROMPT, FORK_SUBAGENT_TYPE

    # Fork agents inherit parent system prompt
    if agent_definition.agent_type == FORK_SUBAGENT_TYPE and parent_system_prompt:
        return parent_system_prompt

    # Use agent's own prompt generator
    prompt = agent_definition.get_system_prompt()
    if prompt:
        return prompt

    # Fallback to default
    return DEFAULT_AGENT_PROMPT
