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
class SpinnerVerbsSettings:
    """Custom spinner-verb configuration.

    Mirrors TS ``settings/types.ts:695`` (``spinnerVerbs``). ``mode``:
    ``"append"`` adds ``verbs`` to the built-in defaults; ``"replace"``
    uses only ``verbs``. See :mod:`src.constants.spinner_verbs`.
    """
    mode: str = "append"  # "append" | "replace"
    verbs: list[str] = field(default_factory=list)


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
    # Provider key that ``model`` was chosen under (ch03 round-3 G1).
    # Multi-provider port: a persisted model is meaningful only with the
    # provider that served it — TS utils/model/model.ts:109-135 documents
    # the cross-provider staleness failure (a stale settings.model kept
    # firing at the wrong endpoint and 400ing after a provider switch).
    # Same pairing rationale as advisor_model/advisor_provider below.
    # Read-side guard: the persisted model applies ONLY when this matches
    # the session's active provider. Distinct from ``provider`` (the
    # active-provider selection) — reusing that key would make the guard
    # vacuous and let /model mutate provider selection.
    model_provider: str = ""
    small_fast_model: str = ""
    # Advisor — reviewer tool. Empty string = unset (no /advisor).
    # Persisted analogue of TS appState.advisorModel; the /advisor slash
    # command writes here, and _call_model_sync reads from here at request
    # time. See src/utils/advisor.py.
    advisor_model: str = ""
    # Provider key (matches ~/.clawcodex/config.json's providers map) that
    # serves the advisor call. REQUIRED when advisor_model is set —
    # clawcodex is multi-provider and the same model name (e.g.
    # ``claude-opus-4-7``) can live behind anthropic, openai (litellm),
    # openrouter, bedrock, ... so name-based inference is ambiguous.
    # The /advisor command writes both fields together via the
    # ``<provider>:<model>`` syntax. Empty + advisor_model set = misconfig
    # that surfaces as a clear error at the first advisor call.
    advisor_provider: str = ""
    # Force client-side advisor mode (tool dispatch + separate API call)
    # even when the main provider is first-party Anthropic. Default False
    # lets the server-side beta path engage when applicable. Set via
    # ``/advisor <model> --client``. See decide_advisor_mode() in
    # src/utils/advisor.py for the full activation table.
    advisor_client_mode: bool = False
    # Master on/off switch for the advisor (reviewer tool). Default False —
    # the advisor is OFF unless the user explicitly opts in, even when
    # advisor_model/advisor_provider are configured. Enable via
    # ``"advisor_enabled": true`` in ~/.clawcodex/config.json's "settings"
    # block, or by running ``/advisor <provider>:<model>`` (which flips it on).
    # ``decide_advisor_mode`` returns INACTIVE whenever this is False.
    advisor_enabled: bool = False

    # Auto-mode transcript classifier (ch06 round-4 PR-B). The
    # ``feature('TRANSCRIPT_CLASSIFIER')`` analog: default OFF, so `auto`
    # mode keeps today's zero-extra-cost STATIC heuristic. When True, the
    # static heuristic stays the fast-path pre-filter (safe reads/edits/
    # bash resolve with no LLM call) and only the residual asks fire a
    # per-ask LLM security classification on the session provider. Enable
    # only where a classifier model + prompt caching are affordable.
    auto_mode_classifier_enabled: bool = False
    # Optional classifier model/provider (TS getClassifierModel default =
    # the main-loop model). Empty → the session provider + its model.
    auto_mode_classifier_model: str = ""
    auto_mode_classifier_provider: str = ""
    # Iron gate on classifier ERROR (timeout/parse/abort). False (default)
    # = fail-CLOSED (deny) — TS tengu_iron_gate_closed default true. True =
    # fail-open (return the original ask).
    auto_mode_iron_gate_open: bool = False

    # ch11 round-4 — the LLM memory-relevance recall. Default OFF, faithful
    # to TS (its recall is behind isAutoMemoryEnabled() &&
    # feature('tengu_moth_copse'), off in OSS) and because firing an LLM
    # side-query per user turn on the session provider is a real
    # multi-provider cost. When True, the shared adapter recalls up to 5
    # query-relevant memory files and injects their bodies as a
    # <system-reminder>; the static MEMORY.md index injection is unaffected.
    memory_relevance_prefetch_enabled: bool = False

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

    # Spinner verbs (None = built-in defaults)
    spinner_verbs: SpinnerVerbsSettings | None = None

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

    # Disable dynamic workflows (also honored via CLAUDE_CODE_DISABLE_WORKFLOWS
    # and the camelCase ``disableWorkflows`` JSON key). See src/workflow/gating.py.
    disable_workflows: bool = False

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
        if "spinner_verbs" in known and isinstance(known["spinner_verbs"], dict):
            known["spinner_verbs"] = SpinnerVerbsSettings(**known["spinner_verbs"])
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
