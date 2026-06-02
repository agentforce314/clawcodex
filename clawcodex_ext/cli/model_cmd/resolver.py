"""Resolve effective provider/model from CLI, env, config, and defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from src.config import get_default_provider, get_provider_config


@dataclass(frozen=True)
class Resolution:
    provider: str
    model: str
    provider_source: str
    model_source: str


def resolve(
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    project_root: Path | None = None,
    registry: ModelRegistry | None = None,
) -> Resolution:
    del project_root
    registry = registry or ModelRegistry()

    env_provider = _nonempty(os.environ.get("CLAWCODEX_PROVIDER"))
    env_model = _nonempty(os.environ.get("CLAWCODEX_MODEL"))

    provider = _nonempty(cli_provider)
    provider_source = "cli" if provider else ""
    if provider is None and env_provider:
        provider = env_provider
        provider_source = "env"
    if provider is None and cli_model:
        try:
            provider = registry.infer_provider_for_model(cli_model)
            provider_source = "cli-model"
        except Exception:
            pass
    if provider is None and env_model:
        try:
            provider = registry.infer_provider_for_model(env_model)
            provider_source = "env-model"
        except Exception:
            pass
    if provider is None:
        provider = get_default_provider()
        provider_source = "user"

    registry.validate_provider(provider)

    model = _nonempty(cli_model)
    model_source = "cli" if model else ""
    if model is None and env_model:
        model = env_model
        model_source = "env"
    if model is None:
        provider_cfg = get_provider_config(provider) or {}
        configured_model = _nonempty(provider_cfg.get("default_model"))
        if configured_model:
            try:
                registry.validate_model(configured_model, provider)
                model = configured_model
                model_source = "user"
            except Exception:
                model = None
    if model is None:
        model = registry.provider_default_model(provider)
        model_source = "default"

    registry.validate_model(model, provider)
    return Resolution(
        provider=provider,
        model=model,
        provider_source=provider_source,
        model_source=model_source,
    )


def _nonempty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
