"""Model system — resolution, capabilities, aliases, validation."""

from __future__ import annotations

from .aliases import MODEL_ALIASES, resolve_alias
from .configs import ModelConfig, MODEL_CONFIGS, get_model_config
from .capabilities import (
    get_model_capabilities,
    supports_thinking,
    supports_tools,
    supports_vision,
    supports_computer_use,
)
from .model import (
    resolve_model,
    display_name,
    canonical_model_name,
    deprecation_warning,
)
from .validation import validate_model_name, is_model_allowed
from .bedrock import (
    BEDROCK_MODEL_MAP,
    to_bedrock_model_id,
    from_bedrock_model_id,
)
from .context import (
    get_context_window_for_model,
    get_model_max_output_tokens,
)
from .agent_routing import get_model_for_agent, AgentModelConfig

# Legacy porting types — previously in src/models.py, now shadowed by this package.
# Re-exported for backward compatibility with src/commands.py etc.
from dataclasses import dataclass as _dataclass, field as _field


@_dataclass(frozen=True)
class Subsystem:
    name: str
    path: str
    file_count: int
    notes: str


@_dataclass(frozen=True)
class PortingModule:
    name: str
    responsibility: str
    source_hint: str
    status: str = "planned"


@_dataclass(frozen=True)
class PermissionDenial:
    tool_name: str
    reason: str


@_dataclass(frozen=True)
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0

    def add_turn(self, prompt: str, output: str) -> "UsageSummary":
        return UsageSummary(
            input_tokens=self.input_tokens + len(prompt.split()),
            output_tokens=self.output_tokens + len(output.split()),
        )


@_dataclass
class PortingBacklog:
    title: str
    modules: list[PortingModule] = _field(default_factory=list)

    def summary_lines(self) -> list[str]:
        return [
            f"- {m.name} [{m.status}] — {m.responsibility} (from {m.source_hint})"
            for m in self.modules
        ]


__all__ = [
    "BEDROCK_MODEL_MAP",
    "MODEL_ALIASES",
    "MODEL_CONFIGS",
    "AgentModelConfig",
    "ModelConfig",
    "canonical_model_name",
    "deprecation_warning",
    "display_name",
    "from_bedrock_model_id",
    "get_context_window_for_model",
    "get_model_capabilities",
    "get_model_config",
    "get_model_for_agent",
    "get_model_max_output_tokens",
    "is_model_allowed",
    "resolve_alias",
    "resolve_model",
    "supports_computer_use",
    "supports_thinking",
    "supports_tools",
    "supports_vision",
    "to_bedrock_model_id",
    "validate_model_name",
]
