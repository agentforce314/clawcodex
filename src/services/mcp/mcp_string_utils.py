from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .normalization import normalize_name_for_mcp


@dataclass(frozen=True)
class McpInfoParsed:
    server_name: str
    tool_name: Optional[str]


def mcp_info_from_string(tool_string: str) -> McpInfoParsed | None:
    parts = tool_string.split("__")
    if len(parts) < 2:
        return None
    mcp_part = parts[0]
    server_name = parts[1]
    if mcp_part != "mcp" or not server_name:
        return None
    tool_name_parts = parts[2:]
    tool_name = "__".join(tool_name_parts) if tool_name_parts else None
    return McpInfoParsed(server_name=server_name, tool_name=tool_name)


def get_mcp_prefix(server_name: str) -> str:
    return f"mcp__{normalize_name_for_mcp(server_name)}__"


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"{get_mcp_prefix(server_name)}{normalize_name_for_mcp(tool_name)}"


def get_tool_name_for_permission_check(
    tool_name: str,
    mcp_info: Optional[object] = None,
) -> str:
    if mcp_info is not None:
        server_name = getattr(mcp_info, "server_name", None)
        tool_n = getattr(mcp_info, "tool_name", None)
        if server_name and tool_n:
            return build_mcp_tool_name(server_name, tool_n)
    return tool_name


def get_mcp_display_name(full_name: str, server_name: str) -> str:
    prefix = f"mcp__{normalize_name_for_mcp(server_name)}__"
    if full_name.startswith(prefix):
        return full_name[len(prefix):]
    return full_name


def extract_mcp_tool_display_name(user_facing_name: str) -> str:
    without_suffix = re.sub(r"\s*\(MCP\)\s*$", "", user_facing_name).strip()
    dash_index = without_suffix.find(" - ")
    if dash_index != -1:
        return without_suffix[dash_index + 3:].strip()
    return without_suffix
