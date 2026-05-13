from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, get_args

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
    # Phase 4 WI-4.1 escape hatch: bypass discovery for OAuth servers
    # that implement neither RFC 9728 nor RFC 8414.
    auth_server_metadata_url: str | None = None


@dataclass
class McpHTTPServerConfig:
    url: str
    type: Literal["http"] = "http"
    headers: dict[str, str] | None = None
    headers_helper: str | None = None
    auth_server_metadata_url: str | None = None


@dataclass
class McpWebSocketServerConfig:
    url: str
    type: Literal["ws"] = "ws"
    headers: dict[str, str] | None = None
    headers_helper: str | None = None
    auth_server_metadata_url: str | None = None


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
    """Connection state when the server requires OAuth before tools work.

    Phase 4 WI-4.5 / assumption A7: carry the auth URL on the state so
    the UI/runtime manager doesn't need to hold a parallel map. Mirrors
    TS' NeedsAuthMCPServer shape.
    """

    name: str
    type: Literal["needs-auth"] = "needs-auth"
    config: ScopedMcpServerConfig | None = None
    auth_url: str | None = None
    auth_method: Literal["oauth", "xaa", "unknown"] = "unknown"
    requires_user_action: bool = True
    error: str | None = None


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


# Derived from ``TransportType`` so the validator (and any caller that
# needs the enumerated list, e.g. for error suggestions) stays in sync
# if the Literal gains a new entry.
KNOWN_TRANSPORT_TYPES: tuple[str, ...] = get_args(TransportType)


def _validate_str_str_dict(
    value: Any, field_name: str, errors: list[str]
) -> dict[str, str] | None:
    """Validate that ``value`` is a ``dict[str, str]``; append errors and return None on failure."""
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object mapping strings to strings")
        return None
    cleaned: dict[str, str] = {}
    ok = True
    for k, v in value.items():
        if not isinstance(k, str):
            errors.append(f"{field_name} key {k!r} must be a string")
            ok = False
            continue
        if not isinstance(v, str):
            errors.append(f"{field_name} value for {k!r} must be a string")
            ok = False
            continue
        cleaned[k] = v
    return cleaned if ok else None


def _validate_string_field(
    value: Any,
    field_name: str,
    errors: list[str],
    *,
    required: bool = False,
    allow_empty: bool = True,
) -> str | None:
    """Validate that ``value`` is a string. Append errors as needed."""
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return None
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string")
        return None
    if not allow_empty and value == "":
        errors.append(f"{field_name} cannot be empty")
        return None
    return value


def validate_server_config(data: Any) -> tuple[McpServerConfig | None, list[str]]:
    """Validate raw MCP server config data with rich error messages.

    Mirrors the Zod schemas in ``typescript/src/services/mcp/types.ts``
    (``McpStdioServerConfigSchema`` and friends, lines 28-122). Returns a
    ``(config, errors)`` tuple: on success ``config`` is the parsed dataclass
    and ``errors`` is empty; on failure ``config`` is ``None`` and ``errors``
    is a list of human-readable validation messages.

    Unlike the older ``parse_server_config()`` (which silently returned
    ``None`` on bad input and *raised* ``KeyError`` for missing ``url`` / ``name``
    on remote/sdk configs), this function never raises on user-supplied data
    and surfaces every per-field violation. Callers that want the legacy
    ``Optional[McpServerConfig]`` return type can call ``parse_server_config``
    which delegates here and discards the messages.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return None, ["server config must be an object"]

    server_type_raw = data.get("type")
    if server_type_raw is not None and not isinstance(server_type_raw, str):
        return None, [f"type must be a string, got {type(server_type_raw).__name__}"]

    server_type: str | None = server_type_raw
    if server_type is not None and server_type not in KNOWN_TRANSPORT_TYPES:
        expected = ", ".join(KNOWN_TRANSPORT_TYPES)
        return None, [
            f"unknown transport type: {server_type!r}. Expected one of: {expected}"
        ]

    # ``authServerMetadataUrl`` is the camelCase JSON key (Phase 4 WI-4.1
    # escape hatch). Accept both spellings so existing configs work either way.
    asm_raw = data.get("authServerMetadataUrl")
    if asm_raw is None:
        asm_raw = data.get("auth_server_metadata_url")
    asm_url: str | None = None
    if asm_raw is not None:
        asm_url = _validate_string_field(asm_raw, "authServerMetadataUrl", errors)
        if asm_url is not None and not asm_url.startswith("https://"):
            errors.append("authServerMetadataUrl must use https://")
            asm_url = None

    if server_type in ("sse", "http", "ws"):
        url = _validate_string_field(
            data.get("url"), "url", errors, required=True, allow_empty=False
        )
        headers = _validate_str_str_dict(data.get("headers"), "headers", errors)
        headers_helper = _validate_string_field(
            data.get("headersHelper"), "headersHelper", errors
        )
        if errors or url is None:
            return None, errors
        remote_ctor = {
            "sse": McpSSEServerConfig,
            "http": McpHTTPServerConfig,
            "ws": McpWebSocketServerConfig,
        }[server_type]
        return remote_ctor(
            url=url,
            headers=headers,
            headers_helper=headers_helper,
            auth_server_metadata_url=asm_url,
        ), errors

    if server_type == "sdk":
        name = _validate_string_field(
            data.get("name"), "name", errors, required=True, allow_empty=False
        )
        if errors or name is None:
            return None, errors
        return McpSdkServerConfig(name=name), errors

    # stdio (default branch — type is "stdio" or omitted)
    command = _validate_string_field(
        data.get("command"), "command", errors, required=True, allow_empty=False
    )

    args_raw = data.get("args")
    args: list[str] = []
    if args_raw is not None:
        if not isinstance(args_raw, list):
            errors.append("args must be a list of strings")
        else:
            invalid_indices = [
                i for i, v in enumerate(args_raw) if not isinstance(v, str)
            ]
            if invalid_indices:
                errors.append(
                    f"args[{invalid_indices[0]}] must be a string"
                )
            else:
                args = list(args_raw)

    env = _validate_str_str_dict(data.get("env"), "env", errors)

    if errors or command is None:
        return None, errors
    return McpStdioServerConfig(
        command=command,
        args=args,
        env=env,
        type=server_type,  # preserves "stdio" if explicitly set, else None
    ), errors


def parse_server_config(data: dict[str, Any]) -> McpServerConfig | None:
    """Back-compat wrapper. Returns the parsed config or ``None`` on any failure.

    Prefer :func:`validate_server_config` when you want the error messages.
    """
    config, _errors = validate_server_config(data)
    return config
