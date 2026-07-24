from __future__ import annotations

from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..registry import ToolRegistry


def _map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    """Expose matches as tool_reference blocks so request filtering can load them."""
    matches = output.get("matches", []) if isinstance(output, dict) else []
    if not matches:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": "No matching deferred tools found",
        }
    references = [
        {"type": "tool_reference", "tool_name": name}
        for name in matches
        if isinstance(name, str) and name
    ]
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": references,
    }


def make_tool_search_tool(registry: ToolRegistry) -> Tool:
    def _is_available_tool(tool: Tool | None) -> bool:
        """Only advertise tools that can be present on the next request."""
        if tool is None:
            return False
        try:
            return bool(tool.is_enabled())
        except Exception:
            # A broken runtime gate must not produce a reference whose schema
            # the request builder will subsequently omit.
            return False

    def _deferred_count() -> int:
        return sum(
            1
            for tool in registry.list_tools()
            if (tool.should_defer or tool.is_mcp) and _is_available_tool(tool)
        )

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
            matches = [tool.name] if _is_available_tool(tool) else []
            return ToolResult(
                name="ToolSearch",
                output={
                    "matches": matches,
                    "query": query,
                    "total_deferred_tools": _deferred_count(),
                },
            )

        scored: list[tuple[int, str]] = []
        for t in registry.list_tools():
            if not _is_available_tool(t):
                continue
            hay = f"{t.name}\n{t.prompt()}".lower()
            if lowered in t.name.lower():
                scored.append((0, t.name))
            elif lowered in hay:
                scored.append((1, t.name))
        scored.sort(key=lambda x: (x[0], x[1].lower()))
        matches = [name for _, name in scored[:max_results]]
        return ToolResult(
            name="ToolSearch",
            output={
                "matches": matches,
                "query": query,
                "total_deferred_tools": _deferred_count(),
            },
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
        map_result_to_api=_map_result_to_api,
        prompt=(
            "Fetch full schema definitions for deferred tools. Deferred tools "
            "are announced by name in <available-deferred-tools>. Use "
            "'select:ToolName' for an exact tool or capability keywords to "
            "search. A matched tool becomes callable on the next turn."
        ),
        description=(
            "Fetch full schema definitions for deferred tools by exact name "
            "or capability keywords."
        ),
        strict=True,
        max_result_size_chars=100_000,
        is_read_only=lambda _input: True,
        is_concurrency_safe=lambda _input: True,
        # ToolSearch's classifier-input is the query the model wants
        # to search for -- already compact enough for the classifier.
        to_auto_classifier_input=lambda input_data: (input_data or {}).get("query", "") or "",
    )
