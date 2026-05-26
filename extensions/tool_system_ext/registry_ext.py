from __future__ import annotations

"""
ToolRegistry Extension

Extends upstream ToolRegistry with bundle-based tool loading and
per-agent tool filtering. Uses composition to avoid modifying upstream.

Patches: patches/tool_system/
"""

from typing import TYPE_CHECKING, Callable

from .bundles import (
    TOOL_BUNDLES,
    MODE_BUNDLES,
    ALL_BUNDLE_NAMES,
    get_bundle_tools,
)

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool, Tools
    from src.tool_system.registry import ToolRegistry
    from .agent_config import AgentToolConfig


# Callback type for tool registration events
ToolRegistrationCallback = Callable[["Tool"], None]


class ToolRegistryExt:
    """
    Extended registry that wraps upstream ToolRegistry with bundle support.

    Does not modify upstream ToolRegistry class. Uses composition to
    provide selective tool loading per agent configuration.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._callbacks: list[ToolRegistrationCallback] = []

    @property
    def underlying_registry(self) -> ToolRegistry:
        """Access the underlying upstream registry."""
        return self._registry

    def get_tool(self, name: str) -> Tool | None:
        """Delegate to underlying registry."""
        return self._registry.get(name)

    def list_tools(self) -> Tools:
        """Delegate to underlying registry."""
        return self._registry.list_tools()

    def register(self, tool: Tool) -> None:
        """
        Register a tool and notify all callbacks.

        Args:
            tool: Tool instance to register
        """
        self._registry.register(tool)
        self._notify_tool_registered(tool)

    def unregister(self, name: str) -> bool:
        """
        Unregister a tool by name. Returns True if tool was found and removed.

        Args:
            name: Tool name to unregister

        Returns:
            True if tool was unregistered, False if not found
        """
        # Note: upstream registry doesn't have unregister, we track it here
        # For now, return False - actual removal would need upstream support
        return False

    def on_tool_registered(self, callback: ToolRegistrationCallback) -> None:
        """
        Register a callback to be notified when new tools are registered.

        Args:
            callback: Callable that takes a Tool as argument
        """
        self._callbacks.append(callback)

    def off_tool_registered(self, callback: ToolRegistrationCallback) -> None:
        """
        Remove a previously registered callback.

        Args:
            callback: Previously registered callback to remove
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_tool_registered(self, tool: Tool) -> None:
        """Notify all callbacks of a new tool registration."""
        for cb in self._callbacks:
            cb(tool)

    def load_bundle(self, bundle_name: str) -> list[str]:
        """
        Verify bundle tools are registered in underlying registry.

        Returns list of tool names that were successfully verified.
        Raises KeyError if bundle is unknown.
        """
        if bundle_name not in TOOL_BUNDLES:
            raise KeyError(f"unknown bundle: {bundle_name}")

        loaded: list[str] = []
        for tool_name in TOOL_BUNDLES[bundle_name]:
            tool = self._registry.get(tool_name)
            if tool is not None:
                loaded.append(tool_name)
        return loaded

    def get_tools_for_config(
        self,
        config: AgentToolConfig,
    ) -> Tools:
        """
        Get filtered tool list based on AgentToolConfig.

        Args:
            config: Agent tool configuration

        Returns:
            Filtered Tools list matching the configuration
        """
        if config.mode == "bare":
            return []

        all_tools = self._registry.list_tools()
        tool_names_in_bundle: set[str] = set()

        # Determine which tools to include based on mode/bundles
        if config.mode == "all":
            tool_names_in_bundle = _get_all_tool_names_from_bundles()
        elif config.bundles is not None:
            for bundle in config.bundles:
                if bundle in TOOL_BUNDLES:
                    tool_names_in_bundle.update(TOOL_BUNDLES[bundle])
        else:
            for bundle in MODE_BUNDLES.get("default", []):
                if bundle in TOOL_BUNDLES:
                    tool_names_in_bundle.update(TOOL_BUNDLES[bundle])

        result: list[Tool] = []
        for tool in all_tools:
            if tool.name not in config.exclude:
                if config.mode == "all" or tool.name in tool_names_in_bundle:
                    result.append(tool)

        return result

    def get_available_bundle_names(self) -> list[str]:
        """Return all known bundle names."""
        return ALL_BUNDLE_NAMES


def _get_all_tool_names_from_bundles() -> set[str]:
    """Get all tool names across all bundles."""
    seen: set[str] = set()
    for tools in TOOL_BUNDLES.values():
        seen.update(tools)
    return seen