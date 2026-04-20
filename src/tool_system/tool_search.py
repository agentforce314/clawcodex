"""
Tool Search utilities — aligned with typescript/src/utils/toolSearch.ts.

When enabled, deferred tools (MCP and should_defer tools) are sent with
defer_loading=True and discovered via ToolSearchTool rather than being
loaded upfront.  This reduces initial context window usage.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

from .build_tool import Tool, Tools, tool_matches_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_SEARCH_TOOL_NAME = "ToolSearch"
DEFAULT_AUTO_TOOL_SEARCH_PERCENTAGE = 10  # 10% of context window
CHARS_PER_TOKEN = 2.5

# Model context window sizes (subset; full list in context_analyzer.py)
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000

# Default patterns for models that do NOT support tool_reference
DEFAULT_UNSUPPORTED_MODEL_PATTERNS = ["haiku"]


# ---------------------------------------------------------------------------
# ToolSearchMode enum — mirrors TS ToolSearchMode
# ---------------------------------------------------------------------------

class ToolSearchMode(str, Enum):
    """
    Determines how deferrable tools are surfaced.

    - tst: Tool Search Tool — deferred tools always discovered via ToolSearchTool
    - tst_auto: Auto — tools deferred only when they exceed threshold
    - standard: Tool search disabled — all tools exposed inline
    """

    TST = "tst"
    TST_AUTO = "tst-auto"
    STANDARD = "standard"


# ---------------------------------------------------------------------------
# Mode determination from env vars
# ---------------------------------------------------------------------------

def _parse_auto_percentage(value: str) -> int | None:
    """Parse auto:N syntax. Returns percentage 0-100 or None."""
    if not value.startswith("auto:"):
        return None
    try:
        pct = int(value[5:])
        return max(0, min(100, pct))
    except ValueError:
        return None


def _is_auto_tool_search_mode(value: str | None) -> bool:
    if not value:
        return False
    return value == "auto" or value.startswith("auto:")


def get_auto_tool_search_percentage() -> int:
    """Get the auto-enable percentage from env var or default."""
    value = os.environ.get("ENABLE_TOOL_SEARCH", "")
    if not value or value == "auto":
        return DEFAULT_AUTO_TOOL_SEARCH_PERCENTAGE
    parsed = _parse_auto_percentage(value)
    if parsed is not None:
        return parsed
    return DEFAULT_AUTO_TOOL_SEARCH_PERCENTAGE


def get_tool_search_mode() -> ToolSearchMode:
    """
    Determine tool search mode from ENABLE_TOOL_SEARCH env var.

    Mirrors TS getToolSearchMode from toolSearch.ts.

    ENABLE_TOOL_SEARCH    Mode
    auto / auto:1-99      tst-auto
    true / auto:0         tst
    false / auto:100      standard
    (unset)               tst (default)
    """
    # Kill switch
    if os.environ.get("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "").lower() in ("1", "true", "yes"):
        return ToolSearchMode.STANDARD

    value = os.environ.get("ENABLE_TOOL_SEARCH", "")

    # Handle auto:N syntax
    auto_pct = _parse_auto_percentage(value) if value else None
    if auto_pct == 0:
        return ToolSearchMode.TST
    if auto_pct == 100:
        return ToolSearchMode.STANDARD
    if _is_auto_tool_search_mode(value):
        return ToolSearchMode.TST_AUTO

    if value.lower() in ("1", "true", "yes"):
        return ToolSearchMode.TST
    if value.lower() in ("0", "false", "no"):
        return ToolSearchMode.STANDARD

    # Default: always defer
    return ToolSearchMode.TST


# ---------------------------------------------------------------------------
# Model support checks
# ---------------------------------------------------------------------------

def _get_context_window_for_model(model: str) -> int:
    """Get context window size for a model."""
    model_lower = model.lower()
    for name, window in _MODEL_CONTEXT_WINDOWS.items():
        if name in model_lower:
            return window
    return DEFAULT_CONTEXT_WINDOW


def model_supports_tool_reference(model: str) -> bool:
    """
    Check if a model supports tool_reference blocks.

    Uses negative test: models assumed to support unless they match
    an unsupported pattern. Mirrors TS modelSupportsToolReference.
    """
    normalized = model.lower()
    for pattern in DEFAULT_UNSUPPORTED_MODEL_PATTERNS:
        if pattern.lower() in normalized:
            return False
    return True


# ---------------------------------------------------------------------------
# Deferred tool detection
# ---------------------------------------------------------------------------

def is_deferred_tool(tool: Tool) -> bool:
    """
    Check if a tool should be deferred (not loaded inline).

    A tool is deferred if it's an MCP tool or has should_defer=True.
    Mirrors TS isDeferredTool from toolSearch.ts.
    """
    if tool.is_mcp:
        return True
    if tool.should_defer:
        return True
    return False


# ---------------------------------------------------------------------------
# Tool search availability checks
# ---------------------------------------------------------------------------

def is_tool_search_tool_available(tools: list[Any]) -> bool:
    """Check if ToolSearchTool is in the tools list."""
    return any(
        getattr(t, "name", None) == TOOL_SEARCH_TOOL_NAME
        for t in tools
    )


def is_tool_search_enabled_optimistic() -> bool:
    """
    Optimistic check — returns True if tool search *might* be enabled.

    Returns False only when definitively disabled (standard mode).
    Mirrors TS isToolSearchEnabledOptimistic.
    """
    mode = get_tool_search_mode()
    return mode != ToolSearchMode.STANDARD


async def is_tool_search_enabled(
    model: str,
    tools: Tools,
) -> bool:
    """
    Definitive check if tool search is enabled for a specific request.

    Checks: mode, model support, ToolSearchTool availability, threshold.
    Mirrors TS isToolSearchEnabled from toolSearch.ts.
    """
    if not model_supports_tool_reference(model):
        logger.debug("Tool search disabled: model %s doesn't support tool_reference", model)
        return False

    if not is_tool_search_tool_available(tools):
        logger.debug("Tool search disabled: ToolSearchTool not in tools list")
        return False

    mode = get_tool_search_mode()

    if mode == ToolSearchMode.TST:
        return True

    if mode == ToolSearchMode.TST_AUTO:
        return _check_auto_threshold(tools, model)

    return False  # STANDARD


def _check_auto_threshold(tools: Tools, model: str) -> bool:
    """Check if deferred tools exceed the auto-enable threshold."""
    deferred = [t for t in tools if is_deferred_tool(t)]
    if not deferred:
        return False

    # Character-based heuristic
    total_chars = 0
    for tool in deferred:
        total_chars += len(tool.name)
        try:
            total_chars += len(tool.prompt())
        except Exception:
            total_chars += 100
        total_chars += len(str(dict(tool.input_schema)))

    context_window = _get_context_window_for_model(model)
    percentage = get_auto_tool_search_percentage() / 100
    char_threshold = int(context_window * percentage * CHARS_PER_TOKEN)

    enabled = total_chars >= char_threshold
    logger.debug(
        "Auto tool search: %d chars (threshold: %d, %d%% of context) -> %s",
        total_chars, char_threshold, get_auto_tool_search_percentage(),
        "enabled" if enabled else "disabled",
    )
    return enabled


# ---------------------------------------------------------------------------
# Message history scanning for tool_reference blocks
# ---------------------------------------------------------------------------

def extract_discovered_tool_names(messages: list[Any]) -> set[str]:
    """
    Extract tool names from tool_reference blocks in message history.

    When dynamic tool loading is enabled, deferred tools are discovered
    via ToolSearchTool which returns tool_reference blocks. This function
    scans the message history to find all discovered tool names.

    Mirrors TS extractDiscoveredToolNames from toolSearch.ts.
    """
    discovered: set[str] = set()

    for msg in messages:
        msg_type = getattr(msg, "type", None) or (msg.get("type") if isinstance(msg, dict) else None)

        # Compact boundary carries pre-compact discovered set
        if msg_type == "system":
            subtype = getattr(msg, "subtype", None) or (msg.get("subtype") if isinstance(msg, dict) else None)
            if subtype == "compact_boundary":
                metadata = getattr(msg, "compact_metadata", None) or (msg.get("compact_metadata") if isinstance(msg, dict) else None)
                if metadata:
                    carried = metadata.get("pre_compact_discovered_tools") if isinstance(metadata, dict) else getattr(metadata, "pre_compact_discovered_tools", None)
                    if carried:
                        discovered.update(carried)
                continue

        # Only user messages contain tool_result blocks
        if msg_type != "user":
            continue

        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                block_dict = {}
                if hasattr(block, "type"):
                    block_dict["type"] = block.type
                if hasattr(block, "content"):
                    block_dict["content"] = block.content
                block = block_dict

            if block.get("type") != "tool_result":
                continue
            inner_content = block.get("content")
            if not isinstance(inner_content, list):
                continue
            for item in inner_content:
                if isinstance(item, dict) and item.get("type") == "tool_reference":
                    tool_name = item.get("tool_name")
                    if tool_name:
                        discovered.add(tool_name)

    if discovered:
        logger.debug(
            "Dynamic tool loading: found %d discovered tools in message history",
            len(discovered),
        )

    return discovered


# ---------------------------------------------------------------------------
# Tool filtering for API calls
# ---------------------------------------------------------------------------

def filter_tools_for_request(
    tools: Tools,
    model: str,
    messages: list[Any] | None = None,
) -> Tools:
    """
    Filter tools for an API request based on tool search mode.

    When tool search is enabled, deferred tools are excluded unless
    they've been discovered in message history.

    Returns the filtered tools list.
    """
    if not is_tool_search_enabled_optimistic():
        return tools

    if not model_supports_tool_reference(model):
        return tools

    discovered = extract_discovered_tool_names(messages or [])

    result: Tools = []
    for tool in tools:
        if is_deferred_tool(tool):
            if tool.name in discovered:
                result.append(tool)
            # else: skip — will be discovered via ToolSearchTool
        else:
            result.append(tool)

    return result
