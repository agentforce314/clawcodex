from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from src.tool_system.build_tool import Tool

from .client import McpClient, connect_to_server
from .config import get_all_mcp_configs, is_mcp_server_disabled
from .tool_wrapper import wrap_mcp_tools_for_server
from .types import (
    ConnectedMCPServer,
    DisabledMCPServer,
    FailedMCPServer,
    MCPServerConnection,
    ScopedMcpServerConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_BATCH_SIZE = 3
DEFAULT_REMOTE_BATCH_SIZE = 20


@dataclass
class ConnectionAttemptResult:
    client: MCPServerConnection
    tools: list[Tool] = field(default_factory=list)
    resources: list[Any] = field(default_factory=list)


OnConnectionAttempt = Callable[[ConnectionAttemptResult], None]


def _is_local_mcp_server(config: ScopedMcpServerConfig) -> bool:
    server_type = config.server_type
    return server_type is None or server_type == "stdio" or server_type == "sdk"


async def _process_server(
    name: str,
    config: ScopedMcpServerConfig,
    on_connection_attempt: OnConnectionAttempt,
) -> None:
    if is_mcp_server_disabled(name):
        on_connection_attempt(
            ConnectionAttemptResult(
                client=DisabledMCPServer(name=name, config=config),
            )
        )
        return

    try:
        mcp_client, connection = await connect_to_server(name, config)

        if not isinstance(connection, ConnectedMCPServer):
            on_connection_attempt(
                ConnectionAttemptResult(client=connection)
            )
            return

        tools_raw = await mcp_client.list_tools()
        wrapped_tools = wrap_mcp_tools_for_server(
            connection, tools_raw, mcp_client
        )

        resources: list[Any] = []
        if connection.capabilities.resources:
            try:
                resources = await mcp_client.list_resources()
            except Exception as e:
                logger.debug("Failed to list resources for %s: %s", name, e)

        on_connection_attempt(
            ConnectionAttemptResult(
                client=connection,
                tools=wrapped_tools,
                resources=resources,
            )
        )

    except Exception as e:
        logger.debug("Error processing server %s: %s", name, e)
        on_connection_attempt(
            ConnectionAttemptResult(
                client=FailedMCPServer(
                    name=name,
                    error=str(e),
                    config=config,
                ),
            )
        )


async def _process_batched(
    servers: list[tuple[str, ScopedMcpServerConfig]],
    batch_size: int,
    on_connection_attempt: OnConnectionAttempt,
) -> None:
    semaphore = asyncio.Semaphore(batch_size)

    async def _run(name: str, config: ScopedMcpServerConfig) -> None:
        async with semaphore:
            await _process_server(name, config, on_connection_attempt)

    await asyncio.gather(
        *[_run(name, config) for name, config in servers],
        return_exceptions=True,
    )


async def get_mcp_tools_commands_and_resources(
    on_connection_attempt: OnConnectionAttempt,
    mcp_configs: dict[str, ScopedMcpServerConfig] | None = None,
) -> None:
    if mcp_configs is None:
        configs, _ = get_all_mcp_configs()
        mcp_configs = configs

    all_entries = list(mcp_configs.items())

    active: list[tuple[str, ScopedMcpServerConfig]] = []
    for name, config in all_entries:
        if is_mcp_server_disabled(name):
            on_connection_attempt(
                ConnectionAttemptResult(
                    client=DisabledMCPServer(name=name, config=config),
                )
            )
        else:
            active.append((name, config))

    local_servers = [(n, c) for n, c in active if _is_local_mcp_server(c)]
    remote_servers = [(n, c) for n, c in active if not _is_local_mcp_server(c)]

    await asyncio.gather(
        _process_batched(
            local_servers, DEFAULT_LOCAL_BATCH_SIZE, on_connection_attempt
        ),
        _process_batched(
            remote_servers, DEFAULT_REMOTE_BATCH_SIZE, on_connection_attempt
        ),
    )


async def prefetch_all_mcp_resources(
    mcp_configs: dict[str, ScopedMcpServerConfig],
) -> tuple[list[MCPServerConnection], list[Tool]]:
    clients: list[MCPServerConnection] = []
    tools: list[Tool] = []

    def _on_attempt(result: ConnectionAttemptResult) -> None:
        clients.append(result.client)
        tools.extend(result.tools)

    await get_mcp_tools_commands_and_resources(
        on_connection_attempt=_on_attempt,
        mcp_configs=mcp_configs,
    )

    return clients, tools
