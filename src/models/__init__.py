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
