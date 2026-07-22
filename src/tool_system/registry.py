from __future__ import annotations

import inspect
import logging
from typing import Any, Iterable

from .build_tool import Tool, Tools, tool_matches_name
from .context import ToolContext
from .protocol import ToolCall, ToolResult
from .schema_validation import validate_tool_input
from src.permissions.check import has_permissions_to_use_tool
from src.permissions.handler import handle_permission_ask
from src.permissions.types import (
    PermissionAskDecision,
    PermissionUpdate,
    ToolPermissionContext,
)

log = logging.getLogger(__name__)


def _apply_and_persist_updates(
    context: ToolContext, updates: tuple[PermissionUpdate, ...]
) -> None:
    """Apply accepted "don't ask again" updates and persist them.

    In-memory application makes the rule effective for later dispatches
    through THIS ToolContext (note: a subagent created earlier holds a
    reference to the PREVIOUS permission_context object — sharing is
    by-reference at creation, `agent/subagent_context.py` — so an
    "always" accepted mid-session does not reach already-running
    subagents until restart; they fail safe by re-prompting. TS shares
    one live context — documented divergence). Persistence (for
    userSettings/projectSettings/localSettings destinations) makes it
    survive restarts, read back at startup via ``setup_permissions``.
    Both halves are best-effort: a failed settings write must never fail
    the already-approved tool call — but it is logged, since the user
    was just promised "don't ask again".
    """

    from src.permissions.settings_paths import settings_path_for_destination
    from src.permissions.updates import (
        apply_permission_updates,
        persist_permission_updates,
        supports_persistence,
    )

    try:
        updates_list = list(updates)
        base_ctx = context.permission_context
        prev_mode = getattr(base_ctx, "mode", None)
        # Plan-mode port: a dialog-accepted setMode (e.g. the plan-approval
        # "Yes, auto-accept edits") changes the LIVE mode mid-turn. Run the
        # transition seam (plan enter/exit attachment flags, pre_plan_mode
        # stash/clear) BEFORE applying — the entry-side stash reads the
        # PRE-switch mode, so running it after apply would no-op.
        final_mode = prev_mode
        for u in updates_list:
            if getattr(u, "type", "") == "setMode":
                final_mode = getattr(u, "mode", final_mode)
        mode_changed = (
            final_mode != prev_mode
            and prev_mode is not None
            and final_mode is not None
        )
        if mode_changed:
            try:
                from src.permissions.plan_transitions import (
                    transition_permission_mode,
                )

                base_ctx = transition_permission_mode(
                    prev_mode, final_mode, base_ctx
                )
            except Exception:  # noqa: BLE001 — transition side effects are best-effort
                log.debug("permission-mode transition seam failed", exc_info=True)
        # apply_permission_updates returns a FRESH context (input unchanged)
        # — rebind it so every later dispatch sees the new rules.
        context.permission_context = apply_permission_updates(
            base_ctx, updates_list
        )
        if mode_changed:
            cb = getattr(context, "on_permission_mode_change", None)
            if cb is not None:
                try:
                    cb(str(final_mode))
                except Exception:  # noqa: BLE001
                    log.debug("permission-mode change notify failed", exc_info=True)
    except Exception:
        log.exception("failed to apply accepted permission updates in-memory")
    try:
        cwd = str(context.workspace_root) if context.workspace_root else None
        results = persist_permission_updates(
            list(updates),
            settings_path_for_destination=lambda destination: (
                settings_path_for_destination(destination, cwd)
            ),
        )
        for update, ok in zip(updates, results):
            if not ok and supports_persistence(update.destination):
                log.warning(
                    "permission update not persisted (destination=%s); "
                    "the rule applies this session only",
                    update.destination,
                )
    except Exception:
        log.exception("failed to persist accepted permission updates")


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: Tools = []
        self._by_name: dict[str, Tool] = {}
        # MCP servers whose tools are hidden (the original's MCPServerMultiselect-
        # Dialog). A tool named ``mcp__<server>__<tool>`` is hidden when <server>
        # is in this set — so list_tools() (the agent's view) excludes it.
        self.disabled_servers: set[str] = set()
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

    def remove_tool(self, name: str) -> bool:
        """ch15 round-4 — remove a tool (and its aliases) by name. Returns
        True if a tool was removed. Needed so MCP tools can be SWAPPED live
        when a server sends notifications/tools/list_changed — the registry
        was otherwise append-only, so a re-fetch couldn't reach the agent.

        Also backs ``_filter_registry`` (--allowedTools/--disallowedTools in
        agent_server.py + headless.py): those call sites now call this method
        directly. They historically called a non-existent
        ``registry.unregister`` inside a try/except, so the registry-level
        filtering silently no-op'd — the flags removed nothing from the pool
        the model saw. Both paths only ever REMOVE tools, so activating them
        can only narrow the toolset."""
        key = name.lower()
        tool = self._by_name.pop(key, None)
        if tool is None:
            return False
        self._tools = [t for t in self._tools if t is not tool]
        for alias in getattr(tool, "aliases", ()) or ():
            # Only drop the alias if it still points at THIS tool (another
            # tool may have claimed it — don't clobber that).
            if self._by_name.get(alias.lower()) is tool:
                self._by_name.pop(alias.lower(), None)
        return True

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name.lower())

    def list_tools(self) -> Tools:
        """Tools visible to the agent — excludes disabled-MCP-server tools."""
        if not self.disabled_servers:
            return list(self._tools)
        return [t for t in self._tools if not self._server_disabled(t.name)]

    def all_tools(self) -> Tools:
        """Every registered tool, including hidden ones (for the multiselect UI)."""
        return list(self._tools)

    def _server_disabled(self, tool_name: str) -> bool:
        return any(tool_name.startswith(f"mcp__{s}__") for s in self.disabled_servers)

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
        # Semantic-coerce + validate; the coerced input (string "true"/"30" →
        # bool/number) replaces the raw model input for permissions and call,
        # mirroring TS carrying ``parsedInput.data`` forward.
        coerced_input = validate_tool_input(tool.name, call.input, tool.input_schema)
        if coerced_input is not call.input:
            call = ToolCall(
                name=call.name, input=coerced_input, tool_use_id=call.tool_use_id,
            )

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
                context=context,
                tool_use_id=call.tool_use_id,
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

    # Async tool — drive the coroutine to completion via the shared bridge
    # (extracted to utils/async_bridge in HOOKS-1 so the permission-ask
    # seam reuses the exact same run-or-thread semantics). No timeout on
    # the wait — async tools are expected to self-bound (TaskOutput uses
    # its own ``timeout`` knob; TaskStop uses ``asyncio.wait_for`` inside
    # its body). A hang here is a tool bug, not something the dispatcher
    # papers over with a global cap.
    from src.utils.async_bridge import run_coroutine_blocking

    return run_coroutine_blocking(
        fn(input, context),
        thread_name=f"tool-async-bridge:{getattr(tool, 'name', '?')}",
    )


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
