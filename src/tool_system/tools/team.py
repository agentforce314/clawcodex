from __future__ import annotations

import json
import uuid
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


def _team_create_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    team_name = tool_input.get("team_name")
    if not isinstance(team_name, str) or not team_name.strip():
        raise ToolInputError("team_name must be a non-empty string")
    description = tool_input.get("description")
    if description is not None and not isinstance(description, str):
        raise ToolInputError("description must be a string when provided")
    agent_type = tool_input.get("agent_type")
    if agent_type is not None and not isinstance(agent_type, str):
        raise ToolInputError("agent_type must be a string when provided")

    if context.team is not None or context.teammate_name:
        raise ToolInputError("Only a session without an active team can create a team")
    from src.services.swarm.team_runtime import TeamRuntime

    runtime = TeamRuntime(context, team_name.strip(), description)
    return ToolResult(
        name="TeamCreate",
        output={
            "team_name": runtime.name,
            "team_file_path": str(context.workspace_root / ".clawcodex" / "team.json"),
            "lead_agent_id": runtime.lead_id,
        },
    )


TeamCreateTool: Tool = build_tool(
    name="TeamCreate",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "team_name": {"type": "string"},
            "description": {"type": "string"},
            "agent_type": {"type": "string"},
        },
        "required": ["team_name"],
    },
    call=_team_create_call,
    prompt=(
        "Create a persistent team led by this session. Use Agent with name (and optionally team_name) "
        "to start teammates, TaskCreate/TaskUpdate for shared work, and SendMessage for findings. "
        "Teammates remain available between assignments; their final prose is private. "
        "To finish, send each teammate a shutdown_request, wait for its approved exit, then TeamDelete."
    ),
    description="Create a persistent team with shared tasks and named teammates.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
    # Mirrors TS TeamCreateTool.toAutoClassifierInput.
    to_auto_classifier_input=lambda input_data: (input_data or {}).get("team_name", "")
    or "",
)


def _team_delete_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.team is None:
        return ToolResult(name="TeamDelete", output={"success": False, "message": "No active team"})
    from src.services.swarm.team_membership import is_team_lead

    if not is_team_lead(context):
        raise ToolInputError("Only the team lead can delete the team")
    if context.team_runtime is None:
        raise ToolInputError("No live team runtime owns this team")
    name = context.team_runtime.name
    context.team_runtime.delete()
    return ToolResult(name="TeamDelete", output={"success": True, "team_name": name})


TeamDeleteTool: Tool = build_tool(
    name="TeamDelete",
    input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    call=_team_delete_call,
    prompt="Delete the current team's roster, mailbox, and task board after every teammate has exited. Active teammates must first approve shutdown or be stopped with TaskStop.",
    description="Disband the current team context.",
    strict=True,
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
