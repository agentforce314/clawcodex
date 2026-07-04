"""``clawcodex mcp serve`` — re-expose clawcodex's tools as an MCP stdio server.

Port of ``typescript/src/entrypoints/mcp.ts`` (``startMCPServer``): any MCP
host (Claude Desktop, another agent, an IDE) can connect over stdio and use
this port's built-in tools — plus the tools of MCP servers configured for
this workspace, re-exposed through the same connection
(``loadReexposedMcpTools``, mcp.ts:56-70).

Design decisions (mirroring the TS engine unless noted):

* **Permission posture (security-relevant, plan §W1/P1):** TS builds the
  tool context with ``getEmptyToolPermissionContext()`` — an ENFORCING
  posture (``mode:'default'``, bypass unavailable; Tool.ts:142) — and routes
  execution through ``hasPermissionsToUseTool``. Python's ``ToolContext``
  default is the OPPOSITE (``mode="bypassPermissions"``, context.py) — so
  this module explicitly constructs ``ToolPermissionContext()`` (mode
  "default", empty rules, bypass unavailable) and executes via
  ``registry.dispatch``, which runs the full pipeline
  (schema validation → ``validate_input`` → ``has_permissions_to_use_tool``
  → ask, registry.py:144-224). With no ``permission_handler`` on the
  context, ask-requiring tools **fail closed** (``handle_permission_ask``
  with a ``None`` handler denies) — exactly the TS non-interactive
  behavior.
* **Identity:** ``clawcodex`` + this port's version (TS: ``claude/tengu`` +
  MACRO.VERSION — the established branding divergence).
* **No output schemas:** this port's tools declare none, so none are
  emitted (pre-empts TS's object-rooted-only branch, mcp.ts:106-120).
* **SDK input validation disabled** (``validate_input=False``): the
  registry's ``validate_json_schema`` runs inside ``dispatch`` and produces
  this port's error text — the analog of TS shaping its own ZodError
  message (mcp.ts:231-241) rather than letting the transport layer do it.
* **No provider:** the registry is built with ``provider=None`` — the
  serve surface exposes the tool set, not the model stack; agent-spawning
  tools report "no provider configured" honestly at call time (divergence
  from TS, which threads ``getMainLoopModel()``; revisit if a use case
  needs Agent-via-serve).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src import __version__

logger = logging.getLogger(__name__)


def _build_serve_context(cwd: Path) -> Any:
    """The synthetic non-interactive ToolContext for serve calls.

    ``ToolPermissionContext()`` — NOT the ToolContext default factory —
    pins ``mode="default"`` + empty rules +
    ``is_bypass_permissions_mode_available=False`` (the
    ``getEmptyToolPermissionContext`` analog; see module docstring).
    """
    from src.permissions.types import ToolPermissionContext
    from src.tool_system.context import ToolContext

    return ToolContext(
        workspace_root=cwd,
        cwd=cwd,
        permission_context=ToolPermissionContext(),
    )


def get_combined_tools(builtins: list[Any], mcp_tools: list[Any]) -> list[Any]:
    """MCP tools first; builtins shadowed by an MCP tool name are dropped.

    Mirrors ``getCombinedTools`` (mcp.ts:46-54).
    """
    mcp_names = {t.name for t in mcp_tools}
    return [*mcp_tools, *[t for t in builtins if t.name not in mcp_names]]


async def load_reexposed_mcp_tools() -> tuple[list[Any], list[Any]]:
    """Connect configured MCP servers and collect their tools.

    Mirrors ``loadReexposedMcpTools`` (mcp.ts:56-70) via the same client
    machinery the TS engine uses (``get_mcp_tools_commands_and_resources``
    is the port of ``getMcpToolsCommandsAndResources``). Best-effort: a
    failing server is logged and skipped — it must not kill serve.
    """
    from src.services.mcp.manager import (
        ConnectionAttemptResult,
        get_mcp_tools_commands_and_resources,
    )

    clients: list[Any] = []
    tools: list[Any] = []

    def _on_attempt(result: ConnectionAttemptResult) -> None:
        clients.append(result.client)
        tools.extend(result.tools or [])

    try:
        await get_mcp_tools_commands_and_resources(_on_attempt)
    except Exception:  # noqa: BLE001 — serve must come up even if MCP config is broken
        logger.exception("mcp serve: loading configured MCP servers failed; continuing without")
    return clients, tools


def _tool_description(tool: Any) -> str:
    """Model-facing description — ``tool.prompt()`` (a string), NOT the raw
    ``description`` field, which may be a callable for dynamic tools. Same
    rule as the agent-server's ``_tool_schemas``."""
    try:
        prompt = tool.prompt
        return prompt() if callable(prompt) else str(prompt or "")
    except Exception:  # noqa: BLE001 — a broken prompt() must not hide the tool
        logger.exception("mcp serve: tool.prompt() failed for %s", getattr(tool, "name", "?"))
        return str(getattr(tool, "description", "") or "")


def _result_to_content(result: Any) -> list[mcp_types.ContentBlock]:
    """Map a ToolResult's output to MCP content blocks.

    Mirrors mcp.ts:199-227: str → text; list of blocks → text/image
    (``source.data``/``media_type`` → data/mimeType), unknown block types
    json-stringified with a warning; anything else → json-stringified text.
    (A plain non-block list would json-stringify per element via the unknown
    branch — same as TS, whose list branch assumes content blocks too.)
    """
    data = result.output if not isinstance(result, (str, list)) else result
    if isinstance(data, str):
        return [mcp_types.TextContent(type="text", text=data)]
    if isinstance(data, list):
        blocks: list[mcp_types.ContentBlock] = []
        for block in data:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "text":
                blocks.append(mcp_types.TextContent(type="text", text=str(block.get("text") or "")))
            elif btype == "image" and isinstance(block.get("source"), dict):
                source = block["source"]
                blocks.append(mcp_types.ImageContent(
                    type="image",
                    data=str(source.get("data") or ""),
                    mimeType=str(source.get("media_type") or "application/octet-stream"),
                ))
            else:
                logger.warning("mcp serve: unmapped content block type: %s", btype or "unknown")
                blocks.append(mcp_types.TextContent(
                    type="text", text=json.dumps(block, ensure_ascii=False, default=str),
                ))
        return blocks
    return [mcp_types.TextContent(
        type="text", text=json.dumps(data, ensure_ascii=False, default=str),
    )]


def _error_result(text: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text or "Error")],
        isError=True,
    )


async def build_server(
    cwd: Path,
    *,
    load_configured_mcp: bool = True,
) -> Server:
    """Construct the MCP server with handlers registered (testable seam).

    ``start_mcp_server`` wraps this with the stdio transport; tests drive it
    through the SDK's in-memory transport instead.
    """
    from src.tool_system.defaults import build_default_registry
    from src.tool_system.protocol import ToolCall
    from src.tool_system.registry import get_tools

    registry = build_default_registry(provider=None)
    context = _build_serve_context(cwd)
    if load_configured_mcp:
        mcp_clients, reexposed_tools = await load_reexposed_mcp_tools()
    else:
        mcp_clients, reexposed_tools = [], []

    if reexposed_tools:
        # Re-exposed MCP tools are ask-gated in this port (PR #347 kept all
        # mcp__* gated), so under the fail-closed serve posture they would
        # deny — dead on arrival. TS's serve executes them ungated
        # (mcp.ts calls tool.call directly). The coherent middle: the user's
        # CONFIGURED servers are already their grant (and the C7 .mcp.json
        # approval gate filtered unapproved project servers inside
        # get_all_mcp_configs), so grant content-less session allow rules
        # (bare tool-name rule strings — the PR #342 mechanism) for exactly
        # the re-exposed tool names — builtins stay fail-closed.
        context.permission_context.always_allow_rules.setdefault(
            "session", []
        ).extend(t.name for t in reexposed_tools)
    context.mcp_clients = {getattr(c, "name", str(i)): c for i, c in enumerate(mcp_clients)}
    for t in reexposed_tools:
        try:
            registry.register(t)
        except ValueError:
            # Name collision: TS's getCombinedTools gives the MCP tool the
            # win (mcp.ts:46-54 drops the shadowed builtin) — mirror that by
            # evicting the builtin and registering the MCP tool. (An ALIAS
            # collision would re-raise into the drop-and-log branch below —
            # acceptable: mcp__-namespaced tools can't realistically alias.)
            registry.remove_tool(t.name)
            try:
                registry.register(t)
            except Exception:  # noqa: BLE001
                logger.exception("mcp serve: could not register MCP tool %s", getattr(t, "name", "?"))
        except Exception:  # noqa: BLE001 — a single broken tool must not kill serve
            logger.exception("mcp serve: could not register MCP tool %s", getattr(t, "name", "?"))

    server: Server = Server("clawcodex", version=__version__)

    def _is_mcp_tool(tool: Any) -> bool:
        # Same predicate as agent_tool_utils.filter_tools_for_agent.
        return tool.name.startswith("mcp__") or bool(getattr(tool, "is_mcp", False))

    def _current_tools() -> list[Any]:
        # get_tools = deny-rule + is_enabled filtered view; partition it and
        # recombine MCP-first (TS's [...mcpTools, ...dedupedBuiltins] order).
        # Intentional divergence: disabled tools are HIDDEN from ListTools
        # here (TS lists them and only CallTool rejects, mcp.ts:174) — the
        # filtered view is the honest advertisement; CallTool still rejects
        # disabled tools by name for parity.
        visible = get_tools(registry, context.permission_context)
        builtins = [t for t in visible if not _is_mcp_tool(t)]
        reexposed = [t for t in visible if _is_mcp_tool(t)]
        return get_combined_tools(builtins, reexposed)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        out: list[mcp_types.Tool] = []
        for tool in _current_tools():
            out.append(mcp_types.Tool(
                name=tool.name,
                description=_tool_description(tool),
                inputSchema=tool.input_schema,
            ))
        return out

    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> mcp_types.CallToolResult:
        from src.tool_system.errors import ToolInputError, ToolPermissionError

        try:
            tool = registry.get(name)
            if tool is None:
                return _error_result(f"Tool {name} not found")
            if not tool.is_enabled():
                return _error_result(f"Tool {name} is not enabled")
            # The full pipeline: schema validation → validate_input →
            # has_permissions_to_use_tool (ask fails closed — no handler on
            # the serve context) → call. See module docstring.
            #
            # to_thread: dispatch is sync (and its async-tool bridge blocks
            # the calling thread), so running it inline would freeze the MCP
            # server's event loop for the tool's whole duration (a 30s Bash
            # would stall pings/cancellation). The worker thread has no
            # running loop, so async tools take _invoke_tool_call's clean
            # asyncio.run path. TS's `await tool.call(...)` is cooperative —
            # this is the Python equivalent.
            result = await asyncio.to_thread(
                registry.dispatch, ToolCall(name=name, input=arguments or {}), context
            )
            if result.is_error:
                error_text = (
                    result.output.get("error") if isinstance(result.output, dict) else None
                )
                return _error_result(str(error_text or result.output))
            return mcp_types.CallToolResult(content=_result_to_content(result), isError=False)
        except ToolInputError as exc:
            # The registry's schema validator renders field-path messages —
            # the analog of TS's ZodError formatting branch (mcp.ts:231-241).
            return _error_result(f"Tool {name} input is invalid:\n{exc}")
        except ToolPermissionError as exc:
            return _error_result(str(exc))
        except Exception as exc:  # noqa: BLE001 — mirror mcp.ts:243-256 (never crash the server)
            logger.exception("mcp serve: tool %s raised", name)
            return _error_result(str(exc) or "Error")

    return server


async def start_mcp_server(cwd: Path, *, debug: bool = False, verbose: bool = False) -> None:
    """Run the stdio MCP server until the client disconnects.

    Mirrors ``startMCPServer`` (mcp.ts:72-266).
    """
    server = await build_server(cwd)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_serve(cwd: str | None = None, *, debug: bool = False, verbose: bool = False) -> int:
    """Sync wrapper for the ``serve`` verb."""
    import asyncio
    import os

    target = Path(cwd or os.getcwd()).resolve()
    try:
        asyncio.run(start_mcp_server(target, debug=debug, verbose=verbose))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 — surface startup failures on stderr
        print(f"clawcodex mcp serve: {exc}", file=sys.stderr)
        return 1
