from __future__ import annotations

from typing import Any, Protocol

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult


class LSPClient(Protocol):
    def request(self, method: str, params: dict[str, Any] | None = None) -> Any: ...


def _lsp_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    method = tool_input["method"]
    params = tool_input.get("params")
    if not isinstance(method, str) or not method:
        raise ToolInputError("method must be a non-empty string")
    if params is not None and not isinstance(params, dict):
        raise ToolInputError("params must be an object when provided")

    client = context.lsp_client
    if client is None:
        return ToolResult(name="LSP", output={"error": "no lsp client configured"}, is_error=True)

    out = client.request(method, params)
    return ToolResult(name="LSP", output={"method": method, "response": out})


LSPTool: Tool = build_tool(
    name="LSP",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {"method": {"type": "string"}, "params": {"type": "object"}},
        "required": ["method"],
    },
    call=_lsp_call,
    prompt="Send a request to the configured Language Server Protocol client.",
    description="Send a request to the configured Language Server Protocol client.",
    max_result_size_chars=100_000,
    is_read_only=lambda _input: True,
    is_concurrency_safe=lambda _input: True,
)
