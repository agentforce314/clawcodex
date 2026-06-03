"""Resolve effective provider/model from CLI, env, config, and defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from clawcodex_ext.cli.provider_cmd.errors import UnknownProviderError
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

    provider_unknown = False
    try:
        registry.validate_provider(provider)
    except UnknownProviderError:
        provider_unknown = True
        print(
            f"Warning: provider '{provider}' is not in the built-in list — "
            f"proceeding anyway",
            file=sys.stderr,
        )

    model = _nonempty(cli_model)
    model_source = "cli" if model else ""
    if model is None and env_model:
        model = env_model
        model_source = "env"
    if model is None:
        try:
            provider_cfg = get_provider_config(provider) or {}
        except ValueError:
            provider_cfg = {}
        configured_model = _nonempty(provider_cfg.get("default_model"))
        if configured_model:
            if not provider_unknown:
                try:
                    registry.validate_model(configured_model, provider)
                    model = configured_model
                    model_source = "user"
                except Exception:
                    # User-configured model is not in the known list —
                    # the API may have added it dynamically. Trust the
                    # user's configured value and log a warning instead
                    # of silently dropping back to the built-in default.
                    print(
                        f"Warning: model '{configured_model}' is not in the known list "
                        f"for provider '{provider}' — using it anyway (saved config)",
                        file=sys.stderr,
                    )
                    model = configured_model
                    model_source = "user-warn"
            else:
                # Unknown provider — trust the configured model as-is
                model = configured_model
                model_source = "user"
    if model is None and not provider_unknown:
        model = registry.provider_default_model(provider)
        model_source = "default"
    elif model is None and provider_unknown:
        # No configured model and unknown provider — use provider name as model
        model = provider
        model_source = "fallback"

    if not provider_unknown and model_source != "user-warn":
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
