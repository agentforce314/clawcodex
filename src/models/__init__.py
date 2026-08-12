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
# NOTE: the old ``agent_routing`` module (get_model_for_agent /
# AgentModelConfig) was deleted 2026-08: it was a dead parallel path with
# inherit-parent semantics that contradicted the shipped resolver
# (src/agent/agent_model.get_agent_model), and nothing wrote the
# ``agent_models`` config key it read.

__all__ = [
    "BEDROCK_MODEL_MAP",
    "MODEL_ALIASES",
    "MODEL_CONFIGS",
    "ModelConfig",
    "canonical_model_name",
    "deprecation_warning",
    "display_name",
    "from_bedrock_model_id",
    "get_context_window_for_model",
    "get_model_capabilities",
    "get_model_config",
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
