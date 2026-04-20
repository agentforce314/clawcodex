"""Per-model configuration matching TypeScript model/configs.ts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a specific model."""
    model_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_thinking: bool = True
    supports_tools: bool = True
    supports_vision: bool = True
    supports_computer_use: bool = False
    supports_cache: bool = True
    is_deprecated: bool = False
    deprecation_message: str = ""
    cost_input_per_mtok: float = 3.0
    cost_output_per_mtok: float = 15.0
    cost_cache_create_per_mtok: float = 3.75
    cost_cache_read_per_mtok: float = 0.30


MODEL_CONFIGS: dict[str, ModelConfig] = {
    # Claude 4 series
    "claude-sonnet-4-20250514": ModelConfig(
        model_id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4",
        context_window=200_000,
        max_output_tokens=16_384,
        supports_thinking=True,
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
        cost_cache_create_per_mtok=3.75,
        cost_cache_read_per_mtok=0.30,
    ),
    "claude-opus-4-20250514": ModelConfig(
        model_id="claude-opus-4-20250514",
        display_name="Claude Opus 4",
        context_window=200_000,
        max_output_tokens=32_768,
        supports_thinking=True,
        supports_computer_use=True,
        cost_input_per_mtok=15.0,
        cost_output_per_mtok=75.0,
        cost_cache_create_per_mtok=18.75,
        cost_cache_read_per_mtok=1.50,
    ),

    # Claude 3.7 series
    "claude-3-7-sonnet-20250219": ModelConfig(
        model_id="claude-3-7-sonnet-20250219",
        display_name="Claude 3.7 Sonnet",
        context_window=200_000,
        max_output_tokens=16_384,
        supports_thinking=True,
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),

    # Claude 3.5 series
    "claude-3-5-sonnet-20241022": ModelConfig(
        model_id="claude-3-5-sonnet-20241022",
        display_name="Claude 3.5 Sonnet (Oct 2024)",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),
    "claude-3-5-sonnet-20240620": ModelConfig(
        model_id="claude-3-5-sonnet-20240620",
        display_name="Claude 3.5 Sonnet (Jun 2024)",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
        is_deprecated=True,
        deprecation_message="Use claude-sonnet-4-20250514 instead",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),
    "claude-3-5-haiku-20241022": ModelConfig(
        model_id="claude-3-5-haiku-20241022",
        display_name="Claude 3.5 Haiku",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        cost_cache_create_per_mtok=1.25,
        cost_cache_read_per_mtok=0.10,
    ),

    # Claude 3 series
    "claude-3-opus-20240229": ModelConfig(
        model_id="claude-3-opus-20240229",
        display_name="Claude 3 Opus",
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
        is_deprecated=True,
        deprecation_message="Use claude-opus-4-20250514 instead",
        cost_input_per_mtok=15.0,
        cost_output_per_mtok=75.0,
    ),
    "claude-3-sonnet-20240229": ModelConfig(
        model_id="claude-3-sonnet-20240229",
        display_name="Claude 3 Sonnet",
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
        is_deprecated=True,
        deprecation_message="Use claude-sonnet-4-20250514 instead",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),
    "claude-3-haiku-20240307": ModelConfig(
        model_id="claude-3-haiku-20240307",
        display_name="Claude 3 Haiku",
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
        cost_input_per_mtok=0.25,
        cost_output_per_mtok=1.25,
        cost_cache_create_per_mtok=0.30,
        cost_cache_read_per_mtok=0.03,
    ),
}


def get_model_config(model_id: str) -> ModelConfig | None:
    """Get config for a model, or None if unknown."""
    if model_id in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_id]
    # Try prefix match (for date-variant models)
    for key, config in MODEL_CONFIGS.items():
        base = key.rsplit("-", 1)[0]
        if model_id.startswith(base):
            return config
    return None
