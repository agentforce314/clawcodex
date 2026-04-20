"""Default settings values matching TypeScript settings/constants.ts."""

from __future__ import annotations

from .types import (
    CompactSettings,
    HookSettings,
    OutputStyleSettings,
    SettingsSchema,
)

DEFAULT_SETTINGS = SettingsSchema(
    model="claude-sonnet-4-20250514",
    small_fast_model="claude-3-5-haiku-20241022",
    provider="anthropic",
    permission_mode="default",
    permissions=[],
    tools={},
    output_style=OutputStyleSettings(
        style="default",
        max_width=120,
        show_thinking=False,
    ),
    compact=CompactSettings(
        auto_compact=True,
        threshold_tokens=100_000,
        max_compact_retries=3,
    ),
    hooks=HookSettings(
        enabled=True,
        timeout_ms=30_000,
        max_concurrent=5,
    ),
    mcp_servers={},
    max_turns=0,
    max_cost_usd=0.0,
    effort="",
    plan_mode=False,
    non_interactive=False,
    custom_system_prompt="",
    append_system_prompt="",
    allowed_tools=[],
    denied_tools=[],
    fast_mode=False,
    session_retention_days=30,
)

# Known valid effort values
VALID_EFFORT_VALUES = ("", "low", "medium", "high", "max")

# Known valid output styles
VALID_OUTPUT_STYLES = ("default", "concise", "verbose", "markdown")

# Known valid permission modes
VALID_PERMISSION_MODES = ("default", "plan", "bypassPermissions")
