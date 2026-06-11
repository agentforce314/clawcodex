from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Iterable

from .build_tool import Tool, Tools, tool_matches_name
from .context import ToolContext
from .protocol import ToolCall, ToolResult
from .schema_validation import validate_json_schema
from src.permissions.check import has_permissions_to_use_tool
from src.permissions.handler import handle_permission_ask
from src.permissions.types import (
    PermissionAskDecision,
    PermissionUpdate,
    ToolPermissionContext,
)


def _apply_and_persist_updates(
    context: ToolContext, updates: tuple[PermissionUpdate, ...]
) -> None:
    """Apply accepted "don't ask again" updates and persist them.

    In-memory application makes the rule effective for the rest of the
    session; persistence (for userSettings/projectSettings/localSettings
    destinations) makes it survive restarts, read back at startup via
    ``setup_permissions``. Both halves are best-effort: a failed settings
    write must never fail the already-approved tool call.
    """

    from src.permissions.settings_paths import settings_path_for_destination
    from src.permissions.updates import (
        apply_permission_updates,
        persist_permission_updates,
    )

    try:
        # apply_permission_updates returns a FRESH context (input unchanged)
        # — rebind it so every later dispatch sees the new rules.
        context.permission_context = apply_permission_updates(
            context.permission_context, list(updates)
        )
    except Exception:
        pass
    try:
        cwd = str(context.workspace_root) if context.workspace_root else None
        persist_permission_updates(
            list(updates),
            settings_path_for_destination=lambda destination: (
                settings_path_for_destination(destination, cwd)
            ),
        )
    except Exception:
        pass


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
            final, chosen_updates = handle_permission_ask(
                tool.name,
                decision,
                context.permission_handler,
                tool_input=call.input,
            )

            if final.behavior == "deny":
                return ToolResult(
                    name=tool.name,
                    output={"error": getattr(final, "message", None) or "permission denied by user"},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                )

            if chosen_updates:
                _apply_and_persist_updates(context, chosen_updates)

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

        result = _invoke_tool_call(tool, call.input, context)
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


def _invoke_tool_call(tool: Any, input: dict, context: ToolContext) -> ToolResult:
    """Dispatch a tool's ``call`` — sync or async — and return its
    ``ToolResult``.

    Chapter-10 / Chunk D / WI-4.0: the ``Tool.call`` signature was
    historically sync-only, but TaskOutputTool's polling loop (WI-4.1)
    needs async; broadening the dispatcher here unblocks that without
    rippling through every existing tool. Sync tools are dispatched
    unchanged; async tools (``inspect.iscoroutinefunction(tool.call)``
    is True) are awaited via ``asyncio.run`` if no loop is active or
    via a thread-bridge if we're already inside one (the same pattern
    Chunk B introduced for TaskStop's async-kill bridge).
    """
    fn = tool.call
    if not inspect.iscoroutinefunction(fn):
        return fn(input, context)

    # Async tool — drive the coroutine to completion.
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is None:
        return asyncio.run(fn(input, context))

    # Already inside a loop — schedule on a worker thread to avoid
    # nesting (Python disallows ``run_until_complete`` on a running
    # loop). Same pattern as ``task_stop.py``'s async-kill bridge.
    holder: dict[str, Any] = {}
    done = threading.Event()

    def _runner() -> None:
        try:
            holder["result"] = asyncio.run(fn(input, context))
        except BaseException as exc:  # noqa: BLE001 — re-raise
            holder["error"] = exc
        finally:
            done.set()

    threading.Thread(
        target=_runner,
        daemon=True,
        name=f"tool-async-bridge:{getattr(tool, 'name', '?')}",
    ).start()
    # No timeout on this wait — async tools are expected to self-bound
    # (TaskOutput uses its own ``timeout`` knob; TaskStop uses
    # ``asyncio.wait_for(timeout=5.0)`` inside its body). A hung
    # ``done.wait()`` here is a tool bug, not something the dispatcher
    # tries to paper over with a global cap. If a future tool grows a
    # naturally-unbounded await, give it an internal deadline first.
    done.wait()
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder["result"]  # type: ignore[no-any-return]


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
    """Assemble the full tool pool sent to the API.

    Built-ins are sorted and concatenated FIRST, then MCP tools sorted
    and concatenated AFTER. Built-ins win on name collision (insertion
    order preserved).

    The split is not aesthetic: the API server places its prompt-cache
    breakpoint after the last built-in tool. A flat sort across all
    tools would interleave MCP tools mid-list, and adding or removing
    one MCP tool would shift the position of every built-in that sorts
    after it -- invalidating the cache key for the whole tool block.
    Keeping built-ins as a contiguous prefix means MCP churn doesn't
    cost a cache miss on the built-in schemas. See
    ``claude-code-from-source/book/ch06-tools.md`` section
    "assembleToolPool: Merging Built-in and MCP Tools".
    """
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
