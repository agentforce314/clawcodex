from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChannelPermission:
    server_name: str
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    allow_all: bool = False


class ChannelPermissionManager:
    def __init__(self) -> None:
        self._permissions: dict[str, ChannelPermission] = {}

    def set_permission(
        self,
        server_name: str,
        permission: ChannelPermission,
    ) -> None:
        self._permissions[server_name] = permission

    def get_permission(self, server_name: str) -> ChannelPermission | None:
        return self._permissions.get(server_name)

    def is_tool_allowed(
        self,
        server_name: str,
        tool_name: str,
    ) -> bool:
        perm = self._permissions.get(server_name)
        if perm is None:
            return True

        if perm.allow_all:
            if tool_name in perm.denied_tools:
                return False
            return True

        if perm.denied_tools and tool_name in perm.denied_tools:
            return False

        if perm.allowed_tools:
            return tool_name in perm.allowed_tools

        return True

    def filter_tools(
        self,
        server_name: str,
        tool_names: list[str],
    ) -> list[str]:
        return [t for t in tool_names if self.is_tool_allowed(server_name, t)]

    def remove_permission(self, server_name: str) -> bool:
        if server_name in self._permissions:
            del self._permissions[server_name]
            return True
        return False

    def clear(self) -> None:
        self._permissions.clear()

    def list_servers(self) -> list[str]:
        return list(self._permissions.keys())

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ChannelPermissionManager:
        manager = cls()
        for server_name, perm_config in config.items():
            if not isinstance(perm_config, dict):
                continue
            perm = ChannelPermission(
                server_name=server_name,
                allowed_tools=perm_config.get("allowedTools", []),
                denied_tools=perm_config.get("deniedTools", []),
                allow_all=perm_config.get("allowAll", False),
            )
            manager.set_permission(server_name, perm)
        return manager
