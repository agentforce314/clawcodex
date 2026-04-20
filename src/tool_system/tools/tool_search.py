from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..registry import ToolRegistry


def make_tool_search_tool(registry: ToolRegistry) -> Tool:
    def _tool_search_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = tool_input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolInputError("query must be a non-empty string")
        max_results = tool_input.get("max_results", 5)
        if not isinstance(max_results, int) or max_results < 1 or max_results > 50:
            raise ToolInputError("max_results must be an integer between 1 and 50")

        q = query.strip()
        lowered = q.lower()
        if lowered.startswith("select:"):
            name = q.split(":", 1)[1].strip()
            tool = registry.get(name)
            matches = [tool.name] if tool else []
            return ToolResult(
                name="ToolSearch",
                output={
                    "matches": matches,
                    "query": query,
                    "total_deferred_tools": 0,
                },
            )

        scored: list[tuple[int, str]] = []
        for t in registry.list_tools():
            hay = f"{t.name}\n{t.prompt()}".lower()
            if lowered in t.name.lower():
                scored.append((0, t.name))
            elif lowered in hay:
                scored.append((1, t.name))
        scored.sort(key=lambda x: (x[0], x[1].lower()))
        matches = [name for _, name in scored[:max_results]]
        return ToolResult(
            name="ToolSearch",
            output={"matches": matches, "query": query, "total_deferred_tools": 0},
        )

    return build_tool(
        name="ToolSearch",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
        call=_tool_search_call,
        prompt="Search for available tools by name or keywords.",
        description="Search for available tools by name or keywords.",
        strict=True,
        max_result_size_chars=100_000,
        is_read_only=lambda _input: True,
        is_concurrency_safe=lambda _input: True,
    )
