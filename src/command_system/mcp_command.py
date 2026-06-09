"""mcp — ``/mcp`` MCP-server list (port of TS local-jsx).

TS ``/mcp`` (``commands/mcp/``) is a *manager* — a settings panel + enable/disable +
reconnect, all driven by the MCP connection-manager subsystem. Python's ``/mcp`` is
**display-only** (``McpListScreen`` lists the configured servers; the dialog has no
enable/disable action). So this port lists the servers and treats the management verbs
as unsupported (the same subsystem boundary ``/model`` hit with discovery).

Follows the **output-style precedent**: a ``local-jsx`` → :class:`InteractiveCommand`
whose ``run()`` returns text **without touching ``ctx.ui``**, so it behaves identically
on every surface (REPL, Textual, and ``NullUIHost`` headless — no raise).

Coexistence: **inversion** — the TUI keeps intercepting ``/mcp``
(``commands.py`` → ``open_dialog="mcp"`` → ``McpListScreen``); this registry command
serves the non-TUI surfaces (REPL/SDK) + the help/aggregator listings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)

# TS management verbs (mcp.tsx call): enable/disable -> MCPToggle, reconnect ->
# MCPReconnect, no-redirect -> MCPSettings. All need the MCP connection-manager.
_MGMT_ARGS = frozenset({"enable", "disable", "reconnect", "no-redirect"})
_NOT_SUPPORTED = (
    "MCP server management (enable/disable/reconnect/settings) is not supported in "
    "this build."
)


def _collect_mcp_servers() -> list[dict[str, Any]]:
    """Read configured MCP servers from the SAME config keys as the TUI's
    ``_collect_mcp_servers`` (app.py) — ``mcp_servers`` / ``mcpServers`` — PLUS
    ``error`` (which the TUI helper leaves unset but ``_status_summary`` renders).
    Runtime ``status``/``tools`` aren't in the config schema, so real servers come
    back ``disconnected`` (see module note); the richer branches are synthetic-only."""
    from src.config import load_config

    try:
        cfg = load_config() or {}
        raw = cfg.get("mcp_servers") or cfg.get("mcpServers") or {}
    except Exception:
        raw = {}
    servers: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for server_id, entry in raw.items():
            e = entry if isinstance(entry, dict) else {}
            tools = e.get("tools")
            servers.append(
                {
                    "id": str(server_id),
                    "name": str(e.get("name", server_id)),
                    "status": e.get("status", "disconnected"),
                    "tools": list(tools) if isinstance(tools, list) else [],
                    "error": e.get("error"),
                }
            )
    return servers


def _status_summary(server: dict[str, Any]) -> str:
    """Verbatim port of ``_mcp_status_summary`` (mcp_dialogs.py:94-101), quirks intact:
    ``tool_suffix`` is always-plural (``"1 tools"``) and appears ONLY on ``connected``."""
    tools = server.get("tools") or []
    tool_suffix = f" ({len(tools)} tools)" if tools else ""
    status = server.get("status")
    if status == "error":
        err = server.get("error")
        return f"error: {err}" if err else "error"
    if status == "connected":
        return f"connected{tool_suffix}"
    return "disconnected"


@dataclass(frozen=True)
class McpCommand(InteractiveCommand):
    """List the configured MCP servers. Frozen + no new fields (the
    ``OutputStyleCommand`` pattern); ``run()`` returns text without touching ``ctx.ui``."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        parts = (args or "").strip().split()
        if parts and parts[0].lower() in _MGMT_ARGS:
            # TS enable/disable/reconnect/settings — need the unported MCP
            # connection-manager subsystem.
            return InteractiveOutcome(message=_NOT_SUPPORTED, display="system")

        servers = _collect_mcp_servers()
        if not servers:
            return InteractiveOutcome(
                message="No MCP servers configured.", display="system"
            )
        lines = [f"• {s['name']} — {_status_summary(s)}" for s in servers]
        return InteractiveOutcome(
            message="MCP servers:\n" + "\n".join(lines), display="system"
        )


MCP_COMMAND = McpCommand(
    name="mcp",
    description="Manage MCP servers",  # verbatim TS index.ts
    argument_hint="[enable|disable [server-name]]",  # verbatim TS index.ts
)


__all__ = ["MCP_COMMAND", "McpCommand"]
