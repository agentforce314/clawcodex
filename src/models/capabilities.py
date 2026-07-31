"""Model capability detection matching TypeScript model/modelCapabilities.ts."""

from __future__ import annotations

from dataclasses import dataclass

from .configs import MODEL_CONFIGS, get_model_config


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
        # Routed through the helper, not read off ``config``, so the two
        # entry points cannot disagree: ``config`` may be a PREFIX match,
        # whose ``supports_vision=False`` is not evidence about this id.
        vision=supports_vision(model_id),
        computer_use=config.supports_computer_use,
        cache=config.supports_cache,
    )


def supports_thinking(model_id: str) -> bool:
    return get_model_capabilities(model_id).thinking


def supports_tools(model_id: str) -> bool:
    return get_model_capabilities(model_id).tools


def supports_vision(model_id: str) -> bool:
    """Whether ``model_id`` accepts image input.

    A ``False`` verdict is trusted ONLY from an exact ``MODEL_CONFIGS`` hit.
    ``get_model_config`` falls back to a prefix match on
    ``key.rsplit("-", 1)[0]``, which is right for context windows (a dated
    snapshot inherits its family's window) but wrong for a capability that
    a sibling can flip: the ``glm-5.2`` row's prefix base is bare ``glm``,
    so every ``glm*`` id — including the vision-capable ``glm-4.5v`` /
    ``glm-5v-turbo`` — would inherit ``vision=False`` and be rejected by
    the fusion-model validator that this function gates. Z.ai's ``*v``
    family is exactly what that validator's error message tells users to
    pick, so the inherited ``False`` made the check reject its own advice.

    Unknown ids therefore stay permissive (the dataclass default), matching
    the rest of this module: a model absent from the table is assumed
    capable, and a real API error is a better outcome than a wrong refusal.
    """
    if model_id in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_id].supports_vision
    return True


def supports_computer_use(model_id: str) -> bool:
    return get_model_capabilities(model_id).computer_use
