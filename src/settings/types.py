"""Settings schema types matching TypeScript settings/types.ts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PermissionModeType = Literal["default", "plan", "bypassPermissions"]


@dataclass
class PermissionRule:
    """A single permission rule."""
    tool: str = ""
    allow: bool = True
    glob: str | None = None
    regex: str | None = None
    description: str = ""
    source: str = "user"  # "user" | "project" | "managed" | "cli"


@dataclass
class ToolSettings:
    """Per-tool configuration."""
    enabled: bool = True
    allowed_commands: list[str] = field(default_factory=list)
    denied_commands: list[str] = field(default_factory=list)
    timeout_seconds: int = 120


@dataclass
class OutputStyleSettings:
    """Output style configuration."""
    style: str = "default"  # "default" | "concise" | "verbose" | "markdown"
    max_width: int = 120
    show_thinking: bool = False


@dataclass
class CompactSettings:
    """Compaction settings."""
    auto_compact: bool = True
    threshold_tokens: int = 100_000
    max_compact_retries: int = 3


@dataclass
class HookSettings:
    """Hook configuration."""
    enabled: bool = True
    timeout_ms: int = 30_000
    max_concurrent: int = 5


@dataclass
class McpServerSettings:
    """MCP server configuration."""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class SettingsSchema:
    """Full settings schema matching TypeScript SettingsSchema."""

    # Model
    model: str = ""
    small_fast_model: str = ""

    # Provider
    provider: str = "anthropic"

    # Permission mode
    permission_mode: PermissionModeType = "default"

    # Permission rules
    permissions: list[PermissionRule] = field(default_factory=list)

    # Tool settings
    tools: dict[str, ToolSettings] = field(default_factory=dict)

    # Output
    output_style: OutputStyleSettings = field(default_factory=OutputStyleSettings)

    # Compact
    compact: CompactSettings = field(default_factory=CompactSettings)

    # Hooks
    hooks: HookSettings = field(default_factory=HookSettings)

    # MCP servers
    mcp_servers: dict[str, McpServerSettings] = field(default_factory=dict)

    # Max turns
    max_turns: int = 0  # 0 = unlimited

    # Max cost (USD)
    max_cost_usd: float = 0.0  # 0 = unlimited

    # Effort
    effort: str = ""  # "", "low", "medium", "high", "max"

    # Plan mode
    plan_mode: bool = False

    # Non-interactive / SDK mode
    non_interactive: bool = False

    # Custom system prompt
    custom_system_prompt: str = ""

    # Append system prompt
    append_system_prompt: str = ""

    # Allowed tools list (empty = all)
    allowed_tools: list[str] = field(default_factory=list)

    # Denied tools list
    denied_tools: list[str] = field(default_factory=list)

    # Fast mode (use small model)
    fast_mode: bool = False

    # Session retention days
    session_retention_days: int = 30

    # Extra raw fields for forward compatibility
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        import dataclasses
        d = dataclasses.asdict(self)
        extra = d.pop("extra", {})
        d.update(extra)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SettingsSchema:
        """Deserialize from dict."""
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        known: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in data.items():
            if k in known_fields:
                known[k] = v
            else:
                extra[k] = v

        # Convert nested objects
        if "permissions" in known and isinstance(known["permissions"], list):
            known["permissions"] = [
                PermissionRule(**r) if isinstance(r, dict) else r
                for r in known["permissions"]
            ]
        if "output_style" in known and isinstance(known["output_style"], dict):
            known["output_style"] = OutputStyleSettings(**known["output_style"])
        if "compact" in known and isinstance(known["compact"], dict):
            known["compact"] = CompactSettings(**known["compact"])
        if "hooks" in known and isinstance(known["hooks"], dict):
            known["hooks"] = HookSettings(**known["hooks"])
        if "tools" in known and isinstance(known["tools"], dict):
            known["tools"] = {
                name: ToolSettings(**v) if isinstance(v, dict) else v
                for name, v in known["tools"].items()
            }
        if "mcp_servers" in known and isinstance(known["mcp_servers"], dict):
            known["mcp_servers"] = {
                name: McpServerSettings(**v) if isinstance(v, dict) else v
                for name, v in known["mcp_servers"].items()
            }

        known["extra"] = extra
        return cls(**known)
