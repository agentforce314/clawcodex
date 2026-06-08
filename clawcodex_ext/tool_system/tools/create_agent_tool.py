"""CreateAgentTool — enables agents to dynamically create new tools.

Mirrors the architecture described in docs/FEATURE_PLAN.md §3.9 and
docs/PROGRESS.md §F-18.

Agent flow::

    Agent analyses CLI/API spec → generates AgentToolSpec → calls CreateAgentTool
    → tool is validated, built, registered, and persisted → new tool is usable.

The tool supports three call types (``bash``, ``http``, ``python``) with
security enforced via whitelists and parameterised templates (no arbitrary
code execution).
"""

from __future__ import annotations

import logging
from typing import Any

from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.context import ToolContext
from src.tool_system.errors import ToolInputError
from src.tool_system.protocol import ToolResult

from clawcodex_ext.agent.tool_authoring import (
    AgentToolSpec,
    ValidationError,
    create_and_validate,
    add_tool,
    save_spec,
    list_persisted_specs,
    list_python_functions,
)

logger = logging.getLogger(__name__)

CREATE_AGENT_TOOL_NAME = "CreateAgentTool"

CREATE_AGENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "Unique name for the new tool (kebab-case, lowercase, "
                "letters/numbers/hyphens/underscores only)."
            ),
        },
        "description": {
            "type": "string",
            "description": "Human-readable description of what the tool does.",
        },
        "input_schema": {
            "type": "object",
            "description": (
                "JSON Schema describing the tool's parameters. "
                "Must include 'type', 'properties', and 'required'."
            ),
        },
        "call_type": {
            "type": "string",
            "description": "How the tool executes: 'bash', 'http', or 'python'.",
            "enum": ["bash", "http", "python"],
        },
        "call_impl": {
            "oneOf": [
                {
                    "type": "string",
                    "description": (
                        "For call_type=bash: a format-string command template. "
                        "Example: 'glab project view {project_id} --output json'"
                    ),
                },
                {
                    "type": "object",
                    "description": (
                        "For call_type=http: dict with 'method' and 'url' keys. "
                        "Example: {'method': 'GET', 'url': 'https://api.github.com/repos/{owner}/{repo}'}"
                    ),
                    "properties": {
                        "method": {
                            "type": "string",
                            "description": "HTTP method (GET, POST, PUT, DELETE, PATCH).",
                            "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                        },
                        "url": {
                            "type": "string",
                            "description": "URL template with {param} placeholders.",
                        },
                        "body": {
                            "type": "object",
                            "description": "Optional JSON body for POST/PUT/PATCH.",
                        },
                    },
                    "required": ["method", "url"],
                },
            ],
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional classification tags for searching/filtering.",
        },
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Alternative names that also trigger this tool.",
        },
    },
    "required": ["name", "description", "input_schema", "call_type", "call_impl"],
}


def _create_agent_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    """Handle a CreateAgentTool invocation."""
    name = tool_input.get("name", "").strip()
    description = tool_input.get("description", "").strip()
    input_schema = tool_input.get("input_schema", {})
    call_type = tool_input.get("call_type", "")
    call_impl = tool_input.get("call_impl")
    tags = tuple(tool_input.get("tags", []))
    aliases = tuple(tool_input.get("aliases", []))

    # Basic input validation
    if not name:
        raise ToolInputError("name is required and cannot be empty")
    if not description:
        raise ToolInputError("description is required and cannot be empty")
    if not isinstance(input_schema, dict):
        raise ToolInputError("input_schema must be a dict")
    if call_type not in ("bash", "http", "python"):
        raise ToolInputError(f"call_type must be one of: bash, http, python — got '{call_type}'")
    if call_impl is None:
        raise ToolInputError("call_impl is required")

    spec = AgentToolSpec(
        name=name,
        description=description,
        input_schema=input_schema,
        call_type=call_type,
        call_impl=call_impl,
        tags=tags,
        aliases=aliases,
        source="agent-created",
    )

    try:
        tool = create_and_validate(spec)
    except ValidationError as exc:
        return ToolResult(
            name=CREATE_AGENT_TOOL_NAME,
            output={"error": f"Validation failed: {exc}"},
            is_error=True,
        )

    add_tool(tool)
    try:
        save_spec(spec)
    except Exception as exc:
        logger.warning("Failed to persist tool spec %s: %s", name, exc)

    return ToolResult(
        name=CREATE_AGENT_TOOL_NAME,
        output={
            "status": "created",
            "name": tool.name,
            "description": tool.description,
            "call_type": call_type,
            "message": f"Tool '{name}' created and registered successfully.",
        },
    )


def _create_agent_prompt() -> str:
    registered = list_python_functions()
    available = ", ".join(sorted(registered)) if registered else "(none registered)"
    prompt = """CreateAgentTool — dynamically create a new tool at runtime.

An agent can analyse a third-party CLI or HTTP API and generate a new tool
that wraps it with validated, parameterised calls.

Call types:
  - bash: Execute a whitelisted CLI command. Allowed commands: git, gh, glab, curl, wget, kubectl, docker, npm, pip.
    Template uses {param} placeholders, e.g. 'glab project view {project_id}'.
  - http: Make an HTTP request. Allowed methods: GET, POST, PUT, DELETE, PATCH.
    URL and method use {param} placeholders.
  - python: Call a registered function. Available functions: {available}.

Example - creating a git tool:
{{
  "name": "git-status",
  "description": "Show git working tree status in porcelain format",
  "input_schema": {{
    "type": "object",
    "properties": {{"path": {{"type": "string", "description": "Repo path"}}},
    "required": []
  }},
  "call_type": "bash",
  "call_impl": "git -C {path} status --porcelain",
  "tags": ["git", "vcs"],
  "aliases": ["git-status"]
}}

Security constraints:
  - bash commands must start with a whitelisted binary
  - HTTP methods are restricted to safe whitelist
  - Python functions must be pre-registered by the host
  - No arbitrary code execution - only template substitution"""
    return prompt.format(available=available)


def make_create_agent_tool(registry: Any | None = None) -> Tool:
    """Build the CreateAgentTool."""
    return build_tool(
        name=CREATE_AGENT_TOOL_NAME,
        input_schema=CREATE_AGENT_INPUT_SCHEMA,
        call=_create_agent_call,
        prompt=_create_agent_prompt,
        description=lambda _input: "Dynamically create a new tool from a spec",
        aliases=(),
        search_hint="create tool agent dynamic runtime",
        max_result_size_chars=10_000,
        is_read_only=lambda _input: False,
        is_destructive=lambda _input: False,
        is_concurrency_safe=lambda _input: True,
    )


def load_persisted_agent_tools() -> None:
    """Restore agent-created tools from disk on startup."""
    try:
        specs = list_persisted_specs()
        for spec in specs:
            try:
                tool = create_and_validate(spec)
                add_tool(tool)
            except Exception as exc:
                logger.warning("Failed to restore agent tool %s: %s", spec.name, exc)
    except Exception as exc:
        logger.warning("Failed to load persisted agent tools: %s", exc)