"""
Context analysis for /context command.

Collects token estimates across all context categories and returns
a structured ContextData object that can be formatted as Markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..token_estimation import count_tokens, count_messages_tokens

# Model context window sizes
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 128_000,
    # GLM / Minimax defaults
    "glm-4": 128_000,
    "glm-4-flash": 128_000,
    "minimax": 128_000,
    "abab": 128_000,
}

DEFAULT_CONTEXT_WINDOW = 200_000


@dataclass
class ContextCategory:
    name: str
    tokens: int
    is_deferred: bool = False


@dataclass
class ContextData:
    categories: list[ContextCategory] = field(default_factory=list)
    total_tokens: int = 0
    max_tokens: int = DEFAULT_CONTEXT_WINDOW
    percentage: float = 0.0
    model: str = ""
    memory_files: list[dict[str, Any]] = field(default_factory=list)
    mcp_tools: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    skills_tokens: int = 0
    skills_count: int = 0
    api_usage: Optional[dict[str, int]] = None
    auto_compact_threshold: Optional[int] = None
    is_auto_compact_enabled: bool = False


def get_context_window_for_model(model: str) -> int:
    """Get the context window size for a model."""
    model_lower = model.lower()
    for name, window in MODEL_CONTEXT_WINDOWS.items():
        if name in model_lower:
            return window
    # Try to extract a numeric window from model name (e.g., "gpt-4-32k")
    match = re.search(r'(\d+)k', model_lower)
    if match:
        return int(match.group(1)) * 1_000
    # Check for 1m suffix
    if '1m' in model_lower or 'million' in model_lower:
        return 1_000_000
    return DEFAULT_CONTEXT_WINDOW


def count_tool_definition_tokens(tool_schemas: list[dict[str, Any]]) -> int:
    """
    Count tokens in a list of tool definition schemas.
    """
    total = 0
    for schema in tool_schemas:
        total += count_tokens(str(schema.get("description", "")))
        total += count_tokens(str(schema.get("name", "")))
        total += count_tokens(str(schema.get("input_schema", {})))
    return total


def count_system_prompt_tokens(system_prompt: str) -> int:
    """Count tokens in the system prompt."""
    return count_tokens(system_prompt)


def count_claude_md_tokens(claude_md_content: str) -> int:
    """Count tokens in CLAUDE.md content."""
    return count_tokens(claude_md_content)


def count_message_breakdown_tokens(
    messages: list[dict[str, Any]],
) -> dict[str, int]:
    """
    Break down message tokens by category.

    Returns dict with: tool_call_tokens, tool_result_tokens,
    attachment_tokens, assistant_message_tokens, user_message_tokens
    """
    breakdown = {
        "tool_call_tokens": 0,
        "tool_result_tokens": 0,
        "attachment_tokens": 0,
        "assistant_message_tokens": 0,
        "user_message_tokens": 0,
    }

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, str):
            tokens = count_tokens(content)
            if role == "user":
                breakdown["user_message_tokens"] += tokens
            elif role == "assistant":
                breakdown["assistant_message_tokens"] += tokens
            continue

        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "tool_use":
                name = block.get("name", "")
                input_str = str(block.get("input", {}))
                breakdown["tool_call_tokens"] += count_tokens(name) + count_tokens(input_str)
            elif block_type == "tool_result":
                breakdown["tool_result_tokens"] += count_tokens(str(block.get("content", "")))
            elif block_type == "text":
                if role == "assistant":
                    breakdown["assistant_message_tokens"] += count_tokens(block.get("text", ""))
                elif role == "user":
                    breakdown["user_message_tokens"] += count_tokens(block.get("text", ""))
            elif block_type in ("image", "document", "video"):
                breakdown["attachment_tokens"] += 500

    return breakdown


def analyze_context(
    conversation_api_messages: list[dict[str, Any]],
    model: str,
    system_prompt: str,
    tool_schemas: list[dict[str, Any]],
    claude_md_content: str,
    skills_frontmatter_tokens: int = 0,
    skills_count: int = 0,
    api_usage: Optional[dict[str, int]] = None,
    mcp_tools: Optional[list[dict[str, Any]]] = None,
    custom_agents: Optional[list[dict[str, Any]]] = None,
    auto_compact_threshold: Optional[int] = None,
    is_auto_compact_enabled: bool = False,
) -> ContextData:
    """
    Analyze context usage across all categories.

    Args:
        conversation_api_messages: Messages in API format (from conversation.get_messages())
        model: Model name (e.g., "claude-sonnet-4-6")
        system_prompt: The active system prompt string
        tool_schemas: List of tool schema dicts (from tool registry)
        claude_md_content: Concatenated CLAUDE.md content
        skills_frontmatter_tokens: Tokens from skills frontmatter
        skills_count: Number of skills loaded
        api_usage: Actual usage from last API response (optional)
        mcp_tools: List of MCP tool info dicts with 'name', 'tokens'
        custom_agents: List of agent info dicts with 'name', 'tokens'
        auto_compact_threshold: Threshold for auto-compact
        is_auto_compact_enabled: Whether auto-compact is enabled

    Returns:
        ContextData with per-category token breakdowns
    """
    max_tokens = get_context_window_for_model(model)

    categories: list[ContextCategory] = []

    # System prompt
    system_tokens = count_system_prompt_tokens(system_prompt)
    if system_tokens > 0:
        categories.append(ContextCategory(name="System prompt", tokens=system_tokens))

    # Tool definitions
    tool_tokens = count_tool_definition_tokens(tool_schemas)
    if tool_tokens > 0:
        categories.append(ContextCategory(name="System tools", tokens=tool_tokens))

    # MCP tools
    mcp_total = sum(t.get("tokens", 0) for t in (mcp_tools or []))
    if mcp_total > 0:
        categories.append(ContextCategory(name="MCP tools", tokens=mcp_total))

    # Custom agents
    agent_total = sum(a.get("tokens", 0) for a in (custom_agents or []))
    if agent_total > 0:
        categories.append(ContextCategory(name="Custom agents", tokens=agent_total))

    # Memory files (CLAUDE.md)
    claude_md_tokens = count_claude_md_tokens(claude_md_content)
    if claude_md_tokens > 0:
        categories.append(ContextCategory(name="Memory files", tokens=claude_md_tokens))

    # Skills
    if skills_frontmatter_tokens > 0:
        categories.append(ContextCategory(name="Skills", tokens=skills_frontmatter_tokens))

    # Messages
    message_tokens = count_messages_tokens(conversation_api_messages)
    if message_tokens > 0:
        categories.append(ContextCategory(name="Messages", tokens=message_tokens))

    # Calculate total (non-deferred)
    total_tokens = sum(c.tokens for c in categories if not c.is_deferred)

    # Free space
    free_tokens = max(0, max_tokens - total_tokens)
    categories.append(ContextCategory(name="Free space", tokens=free_tokens))

    # Use API usage if provided for total
    if api_usage:
        usage_total = (
            api_usage.get("input_tokens", 0)
            + api_usage.get("cache_creation_input_tokens", 0)
            + api_usage.get("cache_read_input_tokens", 0)
        )
        total_tokens = usage_total

    percentage = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0

    # Build memory files list
    memory_files: list[dict[str, Any]] = []
    if claude_md_tokens > 0:
        memory_files.append({"path": "CLAUDE.md", "tokens": claude_md_tokens})

    return ContextData(
        categories=categories,
        total_tokens=total_tokens,
        max_tokens=max_tokens,
        percentage=round(percentage, 1),
        model=model,
        memory_files=memory_files,
        mcp_tools=mcp_tools or [],
        agents=custom_agents or [],
        skills_tokens=skills_frontmatter_tokens,
        skills_count=skills_count,
        api_usage=api_usage,
        auto_compact_threshold=auto_compact_threshold,
        is_auto_compact_enabled=is_auto_compact_enabled,
    )


def format_context_as_markdown(data: ContextData) -> str:
    """
    Format ContextData as a Markdown table for the REPL.

    This is the non-interactive fallback output, mirroring
    the TypeScript context-noninteractive.ts output.
    """
    lines: list[str] = [
        "## Context Usage",
        "",
        f"**Model:** {data.model}",
        f"**Tokens:** {data.total_tokens:,} / {data.max_tokens:,} ({data.percentage}%)",
        "",
    ]

    # Auto-compact status
    if data.auto_compact_threshold is not None:
        lines.append(f"**Auto-compact threshold:** {data.auto_compact_threshold:,} tokens")
    if data.is_auto_compact_enabled:
        lines.append(f"**Auto-compact:** enabled")
    lines.append("")

    # Main categories table
    visible_categories = [
        c for c in data.categories
        if c.tokens > 0 and c.name != "Free space"
    ]

    if visible_categories:
        lines.append("### Estimated usage by category")
        lines.append("")
        lines.append("| Category | Tokens | Percentage |")
        lines.append("|----------|--------|------------|")

        for cat in visible_categories:
            pct = (cat.tokens / data.max_tokens * 100) if data.max_tokens > 0 else 0
            lines.append(f"| {cat.name} | {cat.tokens:,} | {pct:.1f}% |")

        free_cat = next((c for c in data.categories if c.name == "Free space"), None)
        if free_cat and free_cat.tokens > 0:
            pct = (free_cat.tokens / data.max_tokens * 100) if data.max_tokens > 0 else 0
            lines.append(f"| Free space | {free_cat.tokens:,} | {pct:.1f}% |")

        lines.append("")

    # Memory files
    if data.memory_files:
        lines.append("### Memory Files")
        lines.append("")
        lines.append("| Type | Path | Tokens |")
        lines.append("|------|------|--------|")
        for f in data.memory_files:
            lines.append(f"| memory | {f.get('path', 'unknown')} | {f.get('tokens', 0):,} |")
        lines.append("")

    # MCP tools
    if data.mcp_tools:
        lines.append("### MCP Tools")
        lines.append("")
        lines.append("| Tool | Server | Tokens |")
        lines.append("|------|--------|--------|")
        for t in data.mcp_tools:
            server = t.get("server_name", "unknown")
            lines.append(f"| {t.get('name', 'unknown')} | {server} | {t.get('tokens', 0):,} |")
        lines.append("")

    # Custom agents
    if data.agents:
        lines.append("### Custom Agents")
        lines.append("")
        lines.append("| Agent Type | Source | Tokens |")
        lines.append("|------------|--------|--------|")
        for a in data.agents:
            lines.append(f"| {a.get('agent_type', 'unknown')} | {a.get('source', 'unknown')} | {a.get('tokens', 0):,} |")
        lines.append("")

    # Skills
    if data.skills_tokens > 0:
        lines.append("### Skills")
        lines.append("")
        lines.append(f"**Total skills:** {data.skills_count} ({data.skills_tokens:,} tokens)")
        lines.append("")

    # API Usage
    if data.api_usage:
        lines.append("### API Usage (last response)")
        lines.append("")
        lines.append(f"- Input tokens: {data.api_usage.get('input_tokens', 0):,}")
        lines.append(f"- Cache creation: {data.api_usage.get('cache_creation_input_tokens', 0):,}")
        lines.append(f"- Cache read: {data.api_usage.get('cache_read_input_tokens', 0):,}")
        lines.append(f"- Output tokens: {data.api_usage.get('output_tokens', 0):,}")
        lines.append("")

    return "\n".join(lines)
