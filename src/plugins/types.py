from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PluginManifest:
    name: str
    description: str = ""
    version: str = "1.0.0"


@dataclass
class LoadedPlugin:
    name: str
    manifest: PluginManifest
    path: str = ""
    source: str = ""
    repository: str = ""
    enabled: bool = True
    is_builtin: bool = False
    hooks_config: dict[str, Any] | None = None
    mcp_servers: dict[str, Any] | None = None


@dataclass
class BuiltinPluginDefinition:
    name: str
    description: str
    version: str = "1.0.0"
    default_enabled: bool = True
    skills: list[Any] = field(default_factory=list)
    hooks: dict[str, Any] | None = None
    mcp_servers: dict[str, Any] | None = None
    is_available: Callable[[], bool] | None = None


class PluginError(Exception):
    def __init__(self, plugin_name: str, message: str) -> None:
        super().__init__(message)
        self.plugin_name = plugin_name
