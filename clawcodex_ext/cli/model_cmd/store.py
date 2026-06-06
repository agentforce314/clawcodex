"""User-scope provider/model preference persistence."""

from __future__ import annotations

from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from clawcodex_ext.cli.model_cmd.errors import UnsupportedScopeError as ModelUnsupportedScopeError
from clawcodex_ext.cli.provider_cmd.errors import UnsupportedScopeError as ProviderUnsupportedScopeError


class ModelStore:
    """Persist default provider and provider default model preferences."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def set_default_provider(self, provider: str, *, scope: str = "user") -> None:
        if scope != "user":
            raise ProviderUnsupportedScopeError(scope)
        self.registry.validate_provider(provider)
        from src.config import set_default_provider

        set_default_provider(provider)

    def unset_default_provider(self, *, scope: str = "user") -> str:
        if scope != "user":
            raise ProviderUnsupportedScopeError(scope)
        provider = "anthropic"
        self.set_default_provider(provider, scope=scope)
        return provider

    def set_default_model(self, provider: str, model: str, *, scope: str = "user") -> None:
        if scope != "user":
            raise ModelUnsupportedScopeError(scope)
        self.registry.validate_model(model, provider)

        from src.config import get_provider_config, set_api_key
        from src.providers import PROVIDER_INFO

        current = get_provider_config(provider)
        set_api_key(
            provider,
            api_key=current.get("api_key", ""),
            base_url=current.get("base_url") or PROVIDER_INFO[provider]["default_base_url"],
            default_model=model,
        )
