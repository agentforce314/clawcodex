from __future__ import annotations

"""
Tool System Extension Layer

Provides optional tool bundle loading and per-agent tool configuration
without modifying upstream tool_system code.

Architecture:
    - bundles.py: Tool bundle definitions
    - registry_ext.py: Extended ToolRegistry with bundle support
    - agent_config.py: Agent tool configuration dataclass

Upstream patches are stored in patches/tool_system/ for quick adaptation.
"""

from .bundles import (
    TOOL_BUNDLES,
    MODE_BUNDLES,
    ALL_BUNDLE_NAMES,
    get_bundle_tools,
    get_all_bundle_tools,
)

from .registry_ext import ToolRegistryExt

from .agent_config import AgentToolConfig, ToolMode, load_tool_config

__all__ = [
    "TOOL_BUNDLES",
    "MODE_BUNDLES",
    "ALL_BUNDLE_NAMES",
    "get_bundle_tools",
    "get_all_bundle_tools",
    "ToolRegistryExt",
    "AgentToolConfig",
    "ToolMode",
    "load_tool_config",
]