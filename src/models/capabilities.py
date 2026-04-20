"""Model capability detection matching TypeScript model/modelCapabilities.ts."""

from __future__ import annotations

from dataclasses import dataclass

from .configs import get_model_config


@dataclass(frozen=True)
class ModelCapabilities:
    """Capabilities of a model."""
    thinking: bool = False
    tools: bool = True
    vision: bool = True
    computer_use: bool = False
    cache: bool = True


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """Get capabilities for a model."""
    config = get_model_config(model_id)
    if config is None:
        # Default: assume basic capabilities
        return ModelCapabilities(thinking=False, tools=True, vision=True)
    return ModelCapabilities(
        thinking=config.supports_thinking,
        tools=config.supports_tools,
        vision=config.supports_vision,
        computer_use=config.supports_computer_use,
        cache=config.supports_cache,
    )


def supports_thinking(model_id: str) -> bool:
    return get_model_capabilities(model_id).thinking


def supports_tools(model_id: str) -> bool:
    return get_model_capabilities(model_id).tools


def supports_vision(model_id: str) -> bool:
    return get_model_capabilities(model_id).vision


def supports_computer_use(model_id: str) -> bool:
    return get_model_capabilities(model_id).computer_use
