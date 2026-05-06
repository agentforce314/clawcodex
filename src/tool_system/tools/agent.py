"""Agent tool — launches subagents with context isolation.

Mirrors typescript/src/tools/AgentTool/AgentTool.tsx.

Supports three modes:
1. **Sync child** — Parent waits for the agent to finish and returns the result.
2. **Async background** — Agent runs independently; parent gets an agent_id back
   immediately and can later query results via SendMessage.
3. **Fork** — Inherits parent context for prompt cache sharing (future).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any
from uuid import uuid4

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..registry import ToolRegistry

from src.agent.agent_definitions import (
    AgentDefinition,
    find_agent_by_type,
    get_built_in_agents,
)
from src.agent.agent_tool_utils import (
    extract_partial_result,
    finalize_agent_tool,
)
from src.agent.constants import (
    AGENT_TOOL_NAME,
    LEGACY_AGENT_TOOL_NAME,
    ONE_SHOT_BUILTIN_AGENT_TYPES,
)
from src.agent.prompt import get_agent_prompt
from src.agent.run_agent import RunAgentParams, run_agent

logger = logging.getLogger(__name__)

# Input schema matching typescript/src/tools/AgentTool/AgentTool.tsx
AGENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "description": {
            "type": "string",
            "description": "A short (3-5 word) description of the task",
        },
        "prompt": {
            "type": "string",
            "description": "The task for the agent to perform",
        },
        "subagent_type": {
            "type": "string",
            "description": "The type of specialized agent to use for this task",
        },
        "model": {
            "type": "string",
            "description": (
                "Optional model override for this agent. Takes precedence over "
                "the agent definition's model frontmatter. If omitted, uses the "
                "agent definition's model, or inherits from the parent."
            ),
            "enum": ["sonnet", "opus", "haiku"],
        },
        "run_in_background": {
            "type": "boolean",
            "description": (
                "Set to true to run this agent in the background. "
                "You will be notified when it completes."
            ),
        },
        "isolation": {
            "type": "string",
            "description": (
                "Isolation mode. \"worktree\" creates a temporary git worktree "
                "so the agent works on an isolated copy of the repo."
            ),
            "enum": ["worktree"],
        },
    },
    "required": ["description", "prompt"],
}


def make_agent_tool(
    registry: ToolRegistry,
    provider: Any | None = None,
) -> Tool:
    """Build the Agent tool.

    Mirrors the AgentTool definition from typescript/src/tools/AgentTool/AgentTool.tsx.

    Args:
        registry: Tool registry providing the available tool pool.
        provider: BaseProvider for API calls. If None, agent execution is a no-op
                  (useful for testing tool registration only).
    """
    def _get_agent_definitions(context: ToolContext) -> list[AgentDefinition]:
        """Get agent definitions from context options or built-in defaults."""
        agent_defs = getattr(context.options, "agent_definitions", None)
        if agent_defs and isinstance(agent_defs, dict):
            active = agent_defs.get("active_agents")
            if active and isinstance(active, list):
                return active
        return get_built_in_agents()

    def _agent_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        prompt = tool_input.get("prompt", "")
        if not prompt:
            raise ToolInputError("prompt is required")

        description = tool_input.get("description", prompt[:50])
        subagent_type = tool_input.get("subagent_type")
        model = tool_input.get("model")
        run_in_background = bool(tool_input.get("run_in_background", False))

        # Resolve agent definition
        agent_definitions = _get_agent_definitions(context)
        if subagent_type:
            agent_def = find_agent_by_type(agent_definitions, subagent_type)
            if agent_def is None:
                available = [a.agent_type for a in agent_definitions]
                raise ToolInputError(
                    f"Unknown subagent_type: {subagent_type}. "
                    f"Available: {', '.join(available)}"
                )
        else:
            # Default to general-purpose
            agent_def = (
                find_agent_by_type(agent_definitions, "general-purpose")
                or agent_definitions[0]
                if agent_definitions
                else None
            )
            if agent_def is None:
                raise ToolInputError("No agent definitions available")

        # Resolve available tools
        available_tools = registry.list_tools()

        agent_id = uuid4().hex
        start_time = time.time()
        is_async = run_in_background

        if provider is None:
            return ToolResult(
                name=AGENT_TOOL_NAME,
                output={
                    "status": "error",
                    "error": "No provider configured — agent execution unavailable.",
                },
                is_error=True,
            )

        run_params = RunAgentParams(
            parent_context=context,
            agent_definition=agent_def,
            prompt=prompt,
            available_tools=available_tools,
            tool_registry=registry,
            provider=provider,
            model=model,
            agent_id=agent_id,
            is_async=is_async,
            max_turns=agent_def.max_turns,
        )

        if is_async:
            return _launch_async_agent(
                run_params=run_params,
                context=context,
                agent_id=agent_id,
                description=description,
                prompt=prompt,
                agent_type=agent_def.agent_type,
            )
        else:
            return _run_sync_agent(
                run_params=run_params,
                agent_id=agent_id,
                start_time=start_time,
                prompt=prompt,
                agent_type=agent_def.agent_type,
            )

    def _run_sync_agent(
        *,
        run_params: RunAgentParams,
        agent_id: str,
        start_time: float,
        prompt: str,
        agent_type: str,
    ) -> ToolResult:
        """Run an agent synchronously and return the result."""
        from ..protocol import ToolResult as TR
        from src.types.messages import Message

        agent_messages: list[Message] = []

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — use a nested run
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_sync_collect_agent_messages, run_params)
                    agent_messages = future.result()
            else:
                agent_messages = loop.run_until_complete(
                    _collect_agent_messages(run_params)
                )
        except RuntimeError:
            # No event loop — create one
            agent_messages = asyncio.run(
                _collect_agent_messages(run_params)
            )

        # Finalize result
        metadata = {
            "start_time": start_time,
            "agent_type": agent_type,
        }
        result = finalize_agent_tool(agent_messages, agent_id, metadata)

        return TR(
            name=AGENT_TOOL_NAME,
            output={
                "status": "completed",
                "prompt": prompt,
                "agent_id": result.agent_id,
                "agent_type": result.agent_type,
                "content": result.content,
                "total_duration_ms": result.total_duration_ms,
                "total_tokens": result.total_tokens,
                "total_tool_use_count": result.total_tool_use_count,
            },
        )

    def _launch_async_agent(
        *,
        run_params: RunAgentParams,
        context: ToolContext,
        agent_id: str,
        description: str,
        prompt: str,
        agent_type: str,
    ) -> ToolResult:
        """Launch an agent in the background and return immediately."""
        context.tasks[agent_id] = {
            "id": agent_id,
            "subject": description,
            "description": prompt,
            "status": "in_progress",
            "owner": agent_type,
            "blocks": [],
            "blockedBy": [],
            "metadata": {"_internal": True, "task_type": "agent"},
            "output": "",
        }

        async def _background_lifecycle() -> None:
            try:
                messages = await _collect_agent_messages(run_params)
                metadata = {
                    "start_time": time.time(),
                    "agent_type": agent_type,
                }
                result = finalize_agent_tool(messages, agent_id, metadata)
                result_text = "\n".join(
                    block.get("text", "")
                    for block in result.content
                    if isinstance(block, dict) and block.get("type") == "text"
                ).strip()
                if not result_text:
                    result_text = "(Subagent completed with no textual output.)"
                context.tasks[agent_id]["status"] = "completed"
                context.tasks[agent_id]["output"] = result_text
                logger.info(
                    "Async agent %s (%s) finished: %d messages",
                    agent_id, agent_type, len(messages),
                )
            except Exception as exc:
                partial = extract_partial_result(locals().get("messages", []))
                context.tasks[agent_id]["status"] = "failed"
                context.tasks[agent_id]["output"] = partial or str(exc)
                logger.exception(
                    "Async agent %s (%s) failed",
                    agent_id, agent_type,
                )

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None:
            running_loop.create_task(_background_lifecycle())
        else:
            def _runner(_stop_event: Any) -> None:
                asyncio.run(_background_lifecycle())

            context.task_manager.start(name=f"agent:{agent_type}", target=_runner)

        return ToolResult(
            name=AGENT_TOOL_NAME,
            output={
                "status": "async_launched",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "description": description,
                "prompt": prompt,
                "task_output_key": agent_id,
            },
        )

    def _agent_prompt() -> str:
        """Build the prompt for the Agent tool."""
        agents = get_built_in_agents()
        return get_agent_prompt(agents)

    def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}
        content = result.get("content", "")
        if result.get("status") == "error":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result.get("error", content),
                "is_error": True,
            }
        if result.get("status") == "async_launched":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": (
                    "Async agent launched successfully.\n"
                    f"agent_id: {result.get('agent_id', '')}\n"
                    f"task_output_key: {result.get('task_output_key', '')}\n"
                    "Use TaskOutput with task_id equal to task_output_key to check completion."
                ),
            }
        if result.get("status") == "completed":
            text_parts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text.strip())
            elif isinstance(content, str) and content.strip():
                text_parts.append(content.strip())
            rendered = "\n\n".join(text_parts).strip() or "(Subagent completed with no textual output.)"
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": rendered,
            }
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}

    return build_tool(
        name=AGENT_TOOL_NAME,
        input_schema=AGENT_INPUT_SCHEMA,
        call=_agent_call,
        prompt=_agent_prompt,
        description=lambda _input: "Launch a new agent to handle a task",
        aliases=(LEGACY_AGENT_TOOL_NAME,),
        map_result_to_api=_map_result_to_api,
        max_result_size_chars=200_000,
        is_destructive=lambda _input: True,
        search_hint="agent spawn subagent delegate task",
        to_auto_classifier_input=lambda input_data: (input_data or {}).get("prompt", "")[:200],
        get_activity_description=lambda input_data: (input_data or {}).get("description", "Running agent") if input_data else None,
    )


def _sync_collect_agent_messages(params: RunAgentParams) -> list[Any]:
    """Collect agent messages synchronously in a new event loop."""
    return asyncio.run(_collect_agent_messages(params))


async def _collect_agent_messages(params: RunAgentParams) -> list[Any]:
    """Collect all messages from the run_agent generator.

    Prints intermediate agent messages (explanatory text, tool use summaries)
    to stderr so the user sees progress in real-time instead of a silent wait.
    """
    from src.types.messages import Message, AssistantMessage
    from src.types.content_blocks import TextBlock, ToolUseBlock

    agent_type = getattr(params.agent_definition, 'agent_type', 'agent')
    messages: list[Message] = []
    async for msg in run_agent(params):
        messages.append(msg)

        # Print intermediate progress to stderr for real-time feedback
        if isinstance(msg, AssistantMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                sys.stderr.write(f"  ⎿ [{agent_type}] {content.strip()[:200]}\n")
                sys.stderr.flush()
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        sys.stderr.write(f"  ⎿ [{agent_type}] {block.text.strip()[:200]}\n")
                        sys.stderr.flush()
                    elif isinstance(block, ToolUseBlock):
                        sys.stderr.write(f"  ⎿ [{agent_type}] {block.name}(...)\n")
                        sys.stderr.flush()
    return messages
