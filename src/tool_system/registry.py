from __future__ import annotations

from typing import Any, Iterable

from .build_tool import Tool, Tools, tool_matches_name
from .context import ToolContext
from .protocol import ToolCall, ToolResult
from .schema_validation import validate_json_schema
from src.permissions.check import has_permissions_to_use_tool
from src.permissions.handler import handle_permission_ask
from src.permissions.types import (
    PermissionAskDecision,
    ToolPermissionContext,
)


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: Tools = []
        self._by_name: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        key = tool.name.lower()
        if key in self._by_name:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools.append(tool)
        self._by_name[key] = tool
        for alias in tool.aliases:
            alias_key = alias.lower()
            if alias_key in self._by_name:
                raise ValueError(f"duplicate tool alias: {alias}")
            self._by_name[alias_key] = tool

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name.lower())

    def list_tools(self) -> Tools:
        return list(self._tools)

    def dispatch(self, call: ToolCall, context: ToolContext) -> ToolResult:
        tool = self._by_name.get(call.name.lower())
        if tool is None:
            return ToolResult(
                name=call.name,
                output={"error": f"unknown tool: {call.name}"},
                is_error=True,
                tool_use_id=call.tool_use_id,
            )

        context.ensure_tool_allowed(tool.name)
        validate_json_schema(call.input, tool.input_schema, root_name=tool.name)

        if tool.validate_input is not None:
            validation = tool.validate_input(call.input, context)
            if not validation.result:
                return ToolResult(
                    name=tool.name,
                    output={"error": validation.message},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                )

        decision = has_permissions_to_use_tool(
            tool, call.input, context.permission_context, tool_use_context=context,
        )

        if decision.behavior == "deny":
            return ToolResult(
                name=tool.name,
                output={"error": getattr(decision, "message", None) or "permission denied"},
                is_error=True,
                tool_use_id=call.tool_use_id,
            )

        if decision.behavior == "ask":
            assert isinstance(decision, PermissionAskDecision)
            handler_cb = None
            if context.permission_handler is not None:
                raw_handler = context.permission_handler

                def _adapted_handler(
                    tn: str, msg: str, suggestions: Any,
                ) -> tuple[bool, dict[str, Any] | None]:
                    allowed, _ = raw_handler(tn, msg, None)
                    return allowed, None

                handler_cb = _adapted_handler

            final = handle_permission_ask(tool.name, decision, handler_cb)

            if final.behavior == "deny":
                return ToolResult(
                    name=tool.name,
                    output={"error": getattr(final, "message", None) or "permission denied by user"},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                )

            if hasattr(final, "updated_input") and final.updated_input:
                call = ToolCall(
                    name=call.name,
                    input=final.updated_input,
                    tool_use_id=call.tool_use_id,
                )

        elif decision.behavior == "allow":
            if hasattr(decision, "updated_input") and decision.updated_input:
                call = ToolCall(
                    name=call.name,
                    input=decision.updated_input,
                    tool_use_id=call.tool_use_id,
                )

        result = tool.call(call.input, context)
        if result.tool_use_id is None and call.tool_use_id is not None:
            return ToolResult(
                name=result.name,
                output=result.output,
                is_error=result.is_error,
                tool_use_id=call.tool_use_id,
                content_type=result.content_type,
                new_messages=result.new_messages,
                context_modifier=result.context_modifier,
            )
        return result


def get_all_base_tools(registry: ToolRegistry) -> Tools:
    return registry.list_tools()


def filter_tools_by_deny_rules(
    tools: Tools,
    permission_context: ToolPermissionContext,
) -> Tools:
    return [
        t for t in tools
        if not permission_context.blocks(t.name)
    ]


def get_tools(
    registry: ToolRegistry,
    permission_context: ToolPermissionContext,
) -> Tools:
    all_tools = get_all_base_tools(registry)
    allowed = filter_tools_by_deny_rules(all_tools, permission_context)
    return [t for t in allowed if t.is_enabled()]


def assemble_tool_pool(
    registry: ToolRegistry,
    permission_context: ToolPermissionContext,
    mcp_tools: Tools | None = None,
) -> Tools:
    builtin_tools = get_tools(registry, permission_context)
    if not mcp_tools:
        builtin_tools.sort(key=lambda t: t.name)
        return builtin_tools

    allowed_mcp = filter_tools_by_deny_rules(mcp_tools, permission_context)
    builtin_tools.sort(key=lambda t: t.name)
    allowed_mcp.sort(key=lambda t: t.name)

    seen: set[str] = set()
    merged: Tools = []
    for t in builtin_tools:
        if t.name not in seen:
            seen.add(t.name)
            merged.append(t)
    for t in allowed_mcp:
        if t.name not in seen:
            seen.add(t.name)
            merged.append(t)
    return merged


def get_merged_tools(
    registry: ToolRegistry,
    permission_context: ToolPermissionContext,
    mcp_tools: Tools | None = None,
) -> Tools:
    builtin_tools = get_tools(registry, permission_context)
    if not mcp_tools:
        return builtin_tools
    return builtin_tools + list(mcp_tools)
