from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    get_all_mcp_configs,
    get_mcp_configs_by_scope,
    ValidationError,
)
from .types import (
    McpStdioServerConfig,
    McpSSEServerConfig,
    McpHTTPServerConfig,
    McpWebSocketServerConfig,
    ScopedMcpServerConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class ServerDiagnostic:
    name: str
    scope: str
    transport_type: str
    status: str
    error: str | None = None
    latency_ms: int | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"


@dataclass
class DiagnosticReport:
    servers: list[ServerDiagnostic] = field(default_factory=list)
    config_errors: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def healthy_count(self) -> int:
        return sum(1 for s in self.servers if s.is_healthy)

    @property
    def unhealthy_count(self) -> int:
        return sum(1 for s in self.servers if not s.is_healthy)

    @property
    def total_count(self) -> int:
        return len(self.servers)

    def format_report(self) -> str:
        lines: list[str] = []
        lines.append("=== MCP Server Diagnostics ===")
        lines.append("")

        if self.config_errors:
            lines.append("Configuration Errors:")
            for err in self.config_errors:
                lines.append(f"  ✗ {err}")
            lines.append("")

        if not self.servers:
            lines.append("No MCP servers configured.")
            return "\n".join(lines)

        lines.append(f"Servers: {self.total_count} total, {self.healthy_count} healthy, {self.unhealthy_count} unhealthy")
        lines.append("")

        for diag in self.servers:
            icon = "✓" if diag.is_healthy else "✗"
            latency = f" ({diag.latency_ms}ms)" if diag.latency_ms is not None else ""
            lines.append(f"  {icon} {diag.name} [{diag.scope}] ({diag.transport_type}){latency}")

            if diag.error:
                lines.append(f"    Error: {diag.error}")

            if diag.capabilities:
                caps = ", ".join(k for k, v in diag.capabilities.items() if v)
                if caps:
                    lines.append(f"    Capabilities: {caps}")

            for warning in diag.warnings:
                lines.append(f"    Warning: {warning}")

        lines.append("")
        return "\n".join(lines)


def _get_transport_type(config: ScopedMcpServerConfig) -> str:
    inner = config.config
    if isinstance(inner, McpStdioServerConfig):
        return "stdio"
    elif isinstance(inner, McpSSEServerConfig):
        return "sse"
    elif isinstance(inner, McpHTTPServerConfig):
        return "http"
    elif isinstance(inner, McpWebSocketServerConfig):
        return "ws"
    return "unknown"


def _validate_stdio_config(
    name: str,
    config: McpStdioServerConfig,
) -> list[str]:
    warnings: list[str] = []

    command = config.command
    if not command:
        warnings.append("No command specified")
        return warnings

    if os.sep not in command and not shutil.which(command):
        resolved = shutil.which(command)
        if resolved is None:
            warnings.append(f"Command '{command}' not found in PATH")

    if config.env:
        for key, value in config.env.items():
            if "${" in value:
                warnings.append(f"Unexpanded environment variable in {key}")

    return warnings


def _validate_url_config(
    name: str,
    url: str,
) -> list[str]:
    warnings: list[str] = []

    if not url:
        warnings.append("No URL specified")
        return warnings

    if not url.startswith(("http://", "https://")):
        warnings.append(f"URL scheme not recognized: {url}")

    if "${" in url:
        warnings.append("Unexpanded environment variable in URL")

    return warnings


async def check_server_health(
    name: str,
    config: ScopedMcpServerConfig,
) -> ServerDiagnostic:
    transport_type = _get_transport_type(config)
    scope = config.scope

    warnings: list[str] = []
    inner = config.config

    if isinstance(inner, McpStdioServerConfig):
        warnings.extend(_validate_stdio_config(name, inner))
    elif isinstance(inner, (McpSSEServerConfig, McpHTTPServerConfig)):
        warnings.extend(_validate_url_config(name, inner.url))
    elif isinstance(inner, McpWebSocketServerConfig):
        warnings.extend(_validate_url_config(name, inner.url))

    start_time = time.monotonic()

    try:
        from .client import McpClient
        client = McpClient()
        connection = await asyncio.wait_for(
            client.connect(name, config),
            timeout=15.0,
        )
        latency_ms = int((time.monotonic() - start_time) * 1000)

        from .types import ConnectedMCPServer, FailedMCPServer
        if isinstance(connection, ConnectedMCPServer):
            caps = {}
            if connection.capabilities:
                caps = {
                    "tools": connection.capabilities.tools,
                    "prompts": connection.capabilities.prompts,
                    "resources": connection.capabilities.resources,
                }

            await client.close()
            return ServerDiagnostic(
                name=name,
                scope=scope,
                transport_type=transport_type,
                status="healthy",
                latency_ms=latency_ms,
                capabilities=caps,
                warnings=warnings,
            )
        elif isinstance(connection, FailedMCPServer):
            await client.close()
            return ServerDiagnostic(
                name=name,
                scope=scope,
                transport_type=transport_type,
                status="failed",
                error=connection.error,
                latency_ms=latency_ms,
                warnings=warnings,
            )
        else:
            await client.close()
            return ServerDiagnostic(
                name=name,
                scope=scope,
                transport_type=transport_type,
                status=getattr(connection, "type", "unknown"),
                latency_ms=latency_ms,
                warnings=warnings,
            )

    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        return ServerDiagnostic(
            name=name,
            scope=scope,
            transport_type=transport_type,
            status="timeout",
            error=f"Connection timed out after {latency_ms}ms",
            latency_ms=latency_ms,
            warnings=warnings,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        return ServerDiagnostic(
            name=name,
            scope=scope,
            transport_type=transport_type,
            status="error",
            error=str(e),
            latency_ms=latency_ms,
            warnings=warnings,
        )


async def run_diagnostics(
    *,
    skip_connection_test: bool = False,
) -> DiagnosticReport:
    configs, config_errors = get_all_mcp_configs()

    report = DiagnosticReport(
        config_errors=[e.message for e in config_errors],
    )

    if not configs:
        return report

    if skip_connection_test:
        for name, config in configs.items():
            transport_type = _get_transport_type(config)
            warnings: list[str] = []
            inner = config.config
            if isinstance(inner, McpStdioServerConfig):
                warnings.extend(_validate_stdio_config(name, inner))
            elif isinstance(inner, (McpSSEServerConfig, McpHTTPServerConfig)):
                warnings.extend(_validate_url_config(name, inner.url))

            report.servers.append(ServerDiagnostic(
                name=name,
                scope=config.scope,
                transport_type=transport_type,
                status="unchecked",
                warnings=warnings,
            ))
        return report

    tasks = [
        check_server_health(name, config)
        for name, config in configs.items()
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            report.servers.append(ServerDiagnostic(
                name="unknown",
                scope="unknown",
                transport_type="unknown",
                status="error",
                error=str(result),
            ))
        else:
            report.servers.append(result)

    return report
