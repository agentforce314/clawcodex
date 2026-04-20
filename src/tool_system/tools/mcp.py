from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from ..build_tool import Tool, ValidationResult, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jsonschema import for input validation
# ---------------------------------------------------------------------------

try:
    import jsonschema as _jsonschema
except ImportError:  # pragma: no cover
    _jsonschema = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# MCP client protocol
# ---------------------------------------------------------------------------

class MCPClient(Protocol):
    def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any: ...
    def list_tools(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# Prompt (ported from TS -- the actual prompt/description are overridden
# per-tool in the per-tool builder, but we keep a meaningful default for
# the single-dispatch MCPTool)
# ---------------------------------------------------------------------------

MCP_TOOL_PROMPT = """\
Call a tool exposed by a connected MCP (Model Context Protocol) server.

MCP servers extend the available tools with external capabilities such as
database access, API integrations, file system operations, and more.

Parameters:
- server (required): The name of the connected MCP server to call
- tool (required): The name of the tool on that server to invoke
- input (optional): A JSON object of arguments to pass to the tool. The shape
  depends on the specific MCP tool being called.

Usage examples:
- Call a tool: `MCP({ server: "github", tool: "search_code", input: { query: "bug fix" } })`
- Call with no args: `MCP({ server: "myserver", tool: "list_items" })`

Notes:
- Use ListMcpResourcesTool and ReadMcpResourceTool for MCP resources (not this tool)
- The tool's output may be a string or an array of content blocks (text, images, etc.)
"""


# ---------------------------------------------------------------------------
# Input schema validation (ported from TS MCPTool.ts validateInput, using
# jsonschema instead of AJV)
# ---------------------------------------------------------------------------

def _validate_mcp_input(tool_input: dict[str, Any], context: ToolContext) -> ValidationResult:
    """Validate MCP tool input.

    If the target MCP tool provides a JSON schema (via input_json_schema on
    the Tool instance), validate the input against it. Falls back to basic
    structural validation if jsonschema is not installed.
    """
    server = tool_input.get("server")
    tool_name = tool_input.get("tool")

    if not server or not isinstance(server, str):
        return ValidationResult.fail("server must be a non-empty string", error_code=400)
    if not tool_name or not isinstance(tool_name, str):
        return ValidationResult.fail("tool must be a non-empty string", error_code=400)

    input_args = tool_input.get("input")
    if input_args is not None and not isinstance(input_args, dict):
        return ValidationResult.fail("input must be an object when provided", error_code=400)

    return ValidationResult.ok()


def validate_mcp_tool_input_schema(
    input_data: dict[str, Any],
    schema: dict[str, Any],
) -> ValidationResult:
    """Validate input_data against a JSON schema.

    Used by the per-tool builder (build_mcp_tool) to validate inputs against
    the MCP tool's own schema. Gracefully degrades when jsonschema is not
    installed.
    """
    if _jsonschema is None:
        return ValidationResult.ok()

    try:
        _jsonschema.validate(input_data, schema)
        return ValidationResult.ok()
    except _jsonschema.ValidationError as exc:
        return ValidationResult.fail(str(exc.message), error_code=400)
    except _jsonschema.SchemaError as exc:
        return ValidationResult.fail(f"Invalid schema: {exc.message}", error_code=500)
    except Exception as exc:
        # Catch-all for unexpected schema compilation errors (matches TS behavior)
        return ValidationResult.fail(
            f"Failed to compile JSON schema for validation: {exc}",
            error_code=500,
        )


# ---------------------------------------------------------------------------
# mapResultToApi (ported from TS MCPTool.ts
#     mapToolResultToToolResultBlockParam)
# ---------------------------------------------------------------------------

def _mcp_map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Format MCP result for the API.

    MCP tools can return strings or content block arrays. We pass them
    through directly rather than JSON-wrapping, matching the TS behavior.
    """
    if isinstance(output, dict):
        # Extract the actual MCP output from our wrapper
        mcp_output = output.get("output", output)
        # If the MCP output is a string, pass through directly
        if isinstance(mcp_output, str):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": mcp_output,
            }
        # If it's a list of content blocks, pass through
        if isinstance(mcp_output, list):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": mcp_output,
            }
        # Otherwise JSON-stringify
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(mcp_output),
        }

    if isinstance(output, str):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": output,
        }

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(output) if output is not None else "",
    }


# ---------------------------------------------------------------------------
# Call implementation
# ---------------------------------------------------------------------------

def _mcp_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    server = tool_input["server"]
    tool_name = tool_input["tool"]
    args = tool_input.get("input") or {}
    if not isinstance(server, str) or not server:
        raise ToolInputError("server must be a non-empty string")
    if not isinstance(tool_name, str) or not tool_name:
        raise ToolInputError("tool must be a non-empty string")
    if not isinstance(args, dict):
        raise ToolInputError("input must be an object when provided")

    client = context.mcp_clients.get(server)
    if client is None:
        return ToolResult(name="MCP", output={"error": f"mcp server not connected: {server}"}, is_error=True)

    out = client.call_tool(tool_name, args)
    return ToolResult(name="MCP", output={"server": server, "tool": tool_name, "output": out})


MCPTool: Tool = build_tool(
    name="MCP",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "server": {
                "type": "string",
                "description": "The name of the connected MCP server to call",
            },
            "tool": {
                "type": "string",
                "description": "The name of the tool on that server to invoke",
            },
            "input": {
                "type": "object",
                "description": "A JSON object of arguments to pass to the MCP tool",
            },
        },
        "required": ["server", "tool"],
    },
    call=_mcp_call,
    prompt=MCP_TOOL_PROMPT,
    description="Call a tool exposed by a connected MCP server.",
    map_result_to_api=_mcp_map_result_to_api,
    validate_input=_validate_mcp_input,
    max_result_size_chars=100_000,
    is_destructive=lambda _input: True,
    is_mcp=True,
)
