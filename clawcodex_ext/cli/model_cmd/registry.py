"""Provider/model metadata helpers for F-43 commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clawcodex_ext.cli.model_cmd.errors import (
    AmbiguousModelError,
    ProviderMismatchError,
    UnknownModelError,
)
from clawcodex_ext.cli.provider_cmd.errors import UnknownProviderError


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    label: str
    default_model: str
    configured_model: str | None
    authenticated: bool | None
    auth_detail: str | None = None


class ModelRegistry:
    """Wrap built-in provider metadata with validation helpers."""

    def __init__(self, provider_info: dict[str, Any] | None = None) -> None:
        if provider_info is None:
            from src.providers import PROVIDER_INFO

            provider_info = PROVIDER_INFO
        self.provider_info = provider_info

    def provider_names(self) -> list[str]:
        return list(self.provider_info.keys())

    def validate_provider(self, provider: str) -> str:
        if provider not in self.provider_info:
            raise UnknownProviderError(provider)
        return provider

    def provider_default_model(self, provider: str) -> str:
        self.validate_provider(provider)
        return self.provider_info[provider]["default_model"]

    def available_models(self, provider: str) -> list[str]:
        self.validate_provider(provider)
        return list(self.provider_info[provider].get("available_models", []))

    def validate_model(self, model: str, provider: str) -> str:
        self.validate_provider(provider)
        if model in self.available_models(provider):
            return model
        if any(model in self.available_models(name) for name in self.provider_names()):
            raise ProviderMismatchError(model, provider)
        raise UnknownModelError(model)

    def infer_provider_for_model(self, model: str) -> str:
        matches = [
            provider
            for provider in self.provider_names()
            if model in self.available_models(provider)
        ]
        if not matches:
            raise UnknownModelError(model)
        if len(matches) > 1:
            raise AmbiguousModelError(model, matches)
        return matches[0]

    def provider_statuses(self) -> list[ProviderStatus]:
        from src.config import get_provider_config

        statuses: list[ProviderStatus] = []
        for name, info in self.provider_info.items():
            configured_model = None
            authenticated: bool | None = None
            auth_detail = None
            try:
                cfg = get_provider_config(name)
                configured_model = cfg.get("default_model")
                if name == "openai-codex":
                    try:
                        from src.auth.codex_oauth import get_codex_auth_status

                        status = get_codex_auth_status()
                        authenticated = status.is_authenticated
                        auth_detail = status.error or status.source
                    except Exception as exc:
                        authenticated = False
                        auth_detail = str(exc)
                else:
                    authenticated = bool(cfg.get("api_key"))
            except Exception:
                authenticated = False

            statuses.append(
                ProviderStatus(
                    name=name,
                    label=info["label"],
                    default_model=info["default_model"],
                    configured_model=configured_model,
                    authenticated=authenticated,
                    auth_detail=auth_detail,
                )
            )
        return statuses
