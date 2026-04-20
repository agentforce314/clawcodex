from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderOverride:
    model: str
    base_url: str
    api_key: str


def _normalize(key: str) -> str:
    return key.lower().replace("-", "").replace("_", "")


def resolve_agent_provider(
    name: str | None,
    subagent_type: str | None,
    settings: dict[str, Any] | None,
) -> ProviderOverride | None:
    if not settings:
        return None

    routing = settings.get("agentRouting")
    models = settings.get("agentModels")
    if not routing or not models:
        return None

    normalized_routing: dict[str, str] = {}
    for key, value in routing.items():
        nk = _normalize(key)
        if nk not in normalized_routing:
            normalized_routing[nk] = value

    candidates = [c for c in [name, subagent_type, "default"] if c]
    model_name: str | None = None

    for candidate in candidates:
        match = normalized_routing.get(_normalize(candidate))
        if match:
            model_name = match
            break

    if not model_name:
        return None

    model_config = models.get(model_name)
    if not model_config:
        return None

    return ProviderOverride(
        model=model_name,
        base_url=model_config.get("base_url", ""),
        api_key=model_config.get("api_key", ""),
    )
