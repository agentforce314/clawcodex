"""Fast-path model CLI commands."""

from __future__ import annotations

import sys

from clawcodex_ext.cli.model_cmd.errors import ModelCommandError
from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from clawcodex_ext.cli.model_cmd.resolver import resolve
from clawcodex_ext.cli.model_cmd.store import ModelStore
from clawcodex_ext.cli.subcommand_registry import register


@register("model")
def run_model_command(args: list[str]) -> int:
    command = args[0] if args else "current"
    rest = args[1:] if args else []

    try:
        if command == "list":
            provider = _parse_provider_flag(rest)
            print(format_model_list(provider))
            return 0
        if command == "show":
            model, provider = _parse_show_args(rest)
            print(format_model_show(model, provider))
            return 0
        if command == "current":
            print(format_model_current())
            return 0
        if command == "use" and rest:
            model, provider, scope = _parse_use_args(rest)
            messages = use_model(model, provider=provider, scope=scope)
            print("\n".join(messages))
            return 0
    except ModelCommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("usage: clawcodex model [list [--provider NAME]|show [NAME] [--provider NAME]|current|use NAME [--provider NAME] [--scope user]]", file=sys.stderr)
    return 2


def format_model_list(provider: str | None = None) -> str:
    registry = ModelRegistry()
    providers = [provider] if provider else registry.provider_names()
    lines = ["Models:"]
    for provider_name in providers:
        registry.validate_provider(provider_name)
        lines.append(f"  {provider_name}:")
        for model in registry.available_models(provider_name):
            marker = " *" if model == registry.provider_default_model(provider_name) else ""
            lines.append(f"    {model}{marker}")
    return "\n".join(lines)


def format_model_show(model: str | None = None, provider: str | None = None) -> str:
    registry = ModelRegistry()
    if model is None:
        current = resolve(registry=registry)
        model = current.model
        provider = provider or current.provider
    elif provider is None:
        provider = registry.infer_provider_for_model(model)
    registry.validate_model(model, provider)
    return "\n".join([f"Model: {model}", f"Provider: {provider}"])


def format_model_current() -> str:
    resolution = resolve()
    return "\n".join(
        [
            f"provider: {resolution.provider} [{resolution.provider_source}]",
            f"model: {resolution.model} [{resolution.model_source}]",
        ]
    )


def use_model(model: str, *, provider: str | None = None, scope: str = "user") -> list[str]:
    registry = ModelRegistry()
    if provider is None:
        provider = registry.infer_provider_for_model(model)
    registry.validate_model(model, provider)

    store = ModelStore(registry)
    store.set_default_provider(provider, scope=scope)
    store.set_default_model(provider, model, scope=scope)
    return [
        f"Default provider set to: {provider}",
        f"Default model for {provider} set to: {model}",
    ]


def _parse_provider_flag(args: list[str]) -> str | None:
    provider = None
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--provider" and idx + 1 < len(args):
            provider = args[idx + 1]
            idx += 2
            continue
        raise ModelCommandError(f"Unknown argument: {token}")
    return provider


def _parse_show_args(args: list[str]) -> tuple[str | None, str | None]:
    model = None
    provider = None
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--provider" and idx + 1 < len(args):
            provider = args[idx + 1]
            idx += 2
            continue
        if model is None:
            model = token
            idx += 1
            continue
        raise ModelCommandError(f"Unknown argument: {token}")
    return model, provider


def _parse_use_args(args: list[str]) -> tuple[str, str | None, str]:
    model = args[0]
    provider = None
    scope = "user"
    idx = 1
    while idx < len(args):
        token = args[idx]
        if token == "--provider" and idx + 1 < len(args):
            provider = args[idx + 1]
            idx += 2
            continue
        if token == "--scope" and idx + 1 < len(args):
            scope = args[idx + 1]
            idx += 2
            continue
        raise ModelCommandError(f"Unknown argument: {token}")
    return model, provider, scope
