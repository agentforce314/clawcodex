from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .types import LoadedPlugin, PluginManifest

logger = logging.getLogger(__name__)


@dataclass
class McpPluginTool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class McpPluginWrapper:
    plugin: LoadedPlugin
    server_name: str
    tools: list[McpPluginTool] = field(default_factory=list)
    connected: bool = False


_mcp_plugins: dict[str, McpPluginWrapper] = {}


def wrap_mcp_server_as_plugin(
    server_name: str,
    tools: list[dict[str, Any]],
    *,
    description: str = "",
) -> McpPluginWrapper:
    manifest = PluginManifest(
        name=f"mcp-{server_name}",
        description=description or f"MCP server: {server_name}",
        version="1.0.0",
    )

    plugin = LoadedPlugin(
        name=manifest.name,
        manifest=manifest,
        source=f"mcp:{server_name}",
        enabled=True,
        mcp_servers={server_name: {"type": "stdio"}},
    )

    mcp_tools: list[McpPluginTool] = []
    for tool in tools:
        mcp_tools.append(McpPluginTool(
            name=tool.get("name", ""),
            description=tool.get("description", ""),
            input_schema=tool.get("inputSchema", {}),
            server_name=server_name,
        ))

    wrapper = McpPluginWrapper(
        plugin=plugin,
        server_name=server_name,
        tools=mcp_tools,
        connected=True,
    )

    _mcp_plugins[server_name] = wrapper
    return wrapper


def get_mcp_plugin(server_name: str) -> McpPluginWrapper | None:
    return _mcp_plugins.get(server_name)


def get_all_mcp_plugins() -> list[McpPluginWrapper]:
    return list(_mcp_plugins.values())


def get_mcp_plugin_tools(server_name: str) -> list[McpPluginTool]:
    wrapper = _mcp_plugins.get(server_name)
    if wrapper is None:
        return []
    return list(wrapper.tools)


def remove_mcp_plugin(server_name: str) -> bool:
    if server_name in _mcp_plugins:
        del _mcp_plugins[server_name]
        return True
    return False


def clear_mcp_plugins() -> None:
    _mcp_plugins.clear()
