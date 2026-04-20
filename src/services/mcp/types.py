from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ConfigScope = Literal[
    "local", "user", "project", "dynamic", "enterprise", "claudeai", "managed"
]

TransportType = Literal["stdio", "sse", "http", "ws", "sdk"]


@dataclass
class McpStdioServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    type: Literal["stdio"] | None = None


@dataclass
class McpSSEServerConfig:
    url: str
    type: Literal["sse"] = "sse"
    headers: dict[str, str] | None = None
    headers_helper: str | None = None


@dataclass
class McpHTTPServerConfig:
    url: str
    type: Literal["http"] = "http"
    headers: dict[str, str] | None = None
    headers_helper: str | None = None


@dataclass
class McpWebSocketServerConfig:
    url: str
    type: Literal["ws"] = "ws"
    headers: dict[str, str] | None = None
    headers_helper: str | None = None


@dataclass
class McpSdkServerConfig:
    name: str
    type: Literal["sdk"] = "sdk"


McpServerConfig = (
    McpStdioServerConfig
    | McpSSEServerConfig
    | McpHTTPServerConfig
    | McpWebSocketServerConfig
    | McpSdkServerConfig
)


@dataclass
class ScopedMcpServerConfig:
    config: McpServerConfig
    scope: ConfigScope
    plugin_source: str | None = None

    @property
    def server_type(self) -> str | None:
        return getattr(self.config, "type", None)


@dataclass
class McpJsonConfig:
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)


@dataclass
class ServerCapabilities:
    tools: bool = False
    prompts: bool = False
    resources: bool = False


@dataclass
class ServerInfo:
    name: str
    version: str


@dataclass
class ConnectedMCPServer:
    name: str
    type: Literal["connected"] = "connected"
    capabilities: ServerCapabilities = field(default_factory=ServerCapabilities)
    server_info: ServerInfo | None = None
    instructions: str | None = None
    config: ScopedMcpServerConfig | None = None

    async def cleanup(self) -> None:
        pass


@dataclass
class FailedMCPServer:
    name: str
    type: Literal["failed"] = "failed"
    config: ScopedMcpServerConfig | None = None
    error: str | None = None


@dataclass
class NeedsAuthMCPServer:
    name: str
    type: Literal["needs-auth"] = "needs-auth"
    config: ScopedMcpServerConfig | None = None


@dataclass
class PendingMCPServer:
    name: str
    type: Literal["pending"] = "pending"
    config: ScopedMcpServerConfig | None = None
    reconnect_attempt: int | None = None
    max_reconnect_attempts: int | None = None


@dataclass
class DisabledMCPServer:
    name: str
    type: Literal["disabled"] = "disabled"
    config: ScopedMcpServerConfig | None = None


MCPServerConnection = (
    ConnectedMCPServer
    | FailedMCPServer
    | NeedsAuthMCPServer
    | PendingMCPServer
    | DisabledMCPServer
)


@dataclass
class McpToolSchema:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


@dataclass
class McpToolResult:
    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    meta: dict[str, Any] | None = None
    structured_content: dict[str, Any] | None = None


@dataclass(frozen=True)
class SerializedTool:
    name: str
    description: str
    input_json_schema: dict[str, Any] | None = None
    is_mcp: bool = False
    original_tool_name: str | None = None


@dataclass(frozen=True)
class SerializedClient:
    name: str
    type: str
    capabilities: ServerCapabilities | None = None


@dataclass
class MCPCliState:
    clients: list[SerializedClient] = field(default_factory=list)
    configs: dict[str, ScopedMcpServerConfig] = field(default_factory=dict)
    tools: list[SerializedTool] = field(default_factory=list)
    resources: dict[str, list[Any]] = field(default_factory=dict)
    normalized_names: dict[str, str] | None = None


def parse_server_config(data: dict[str, Any]) -> McpServerConfig | None:
    server_type = data.get("type")
    if server_type == "sse":
        return McpSSEServerConfig(
            url=data["url"],
            headers=data.get("headers"),
            headers_helper=data.get("headersHelper"),
        )
    elif server_type == "http":
        return McpHTTPServerConfig(
            url=data["url"],
            headers=data.get("headers"),
            headers_helper=data.get("headersHelper"),
        )
    elif server_type == "ws":
        return McpWebSocketServerConfig(
            url=data["url"],
            headers=data.get("headers"),
            headers_helper=data.get("headersHelper"),
        )
    elif server_type == "sdk":
        return McpSdkServerConfig(name=data["name"])
    elif server_type == "stdio" or server_type is None:
        command = data.get("command")
        if not command:
            return None
        return McpStdioServerConfig(
            command=command,
            args=data.get("args", []),
            env=data.get("env"),
            type=server_type,
        )
    return None
