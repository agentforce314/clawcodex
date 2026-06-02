"""Fast-path provider CLI commands."""

from __future__ import annotations

import sys

from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from clawcodex_ext.cli.model_cmd.resolver import resolve
from clawcodex_ext.cli.model_cmd.store import ModelStore
from clawcodex_ext.cli.provider_cmd.errors import ProviderCommandError
from clawcodex_ext.cli.subcommand_registry import register


@register("provider")
def run_provider_command(args: list[str]) -> int:
    command = args[0] if args else "current"
    rest = args[1:] if args else []

    try:
        if command == "list":
            print(format_provider_list())
            return 0
        if command == "show":
            name = rest[0] if rest else None
            print(format_provider_show(name))
            return 0
        if command == "current":
            print(format_provider_current())
            return 0
        if command == "use" and rest:
            provider, scope = _parse_use_args(rest)
            ModelStore().set_default_provider(provider, scope=scope)
            print(f"Default provider set to: {provider}")
            return 0
        if command == "unset":
            scope = _parse_scope(rest)
            provider = ModelStore().unset_default_provider(scope=scope)
            print(f"Default provider reset to: {provider}")
            return 0
    except ProviderCommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("usage: clawcodex provider [list|show [NAME]|current|use NAME [--scope user]|unset]", file=sys.stderr)
    return 2


def format_provider_list() -> str:
    lines = ["Providers:"]
    for status in ModelRegistry().provider_statuses():
        configured = "yes" if status.authenticated else "no"
        model = status.configured_model or status.default_model
        lines.append(f"  {status.name}\t{status.label}\tmodel={model}\tconfigured={configured}")
    return "\n".join(lines)


def format_provider_show(name: str | None = None) -> str:
    registry = ModelRegistry()
    if name is None:
        name = resolve(registry=registry).provider
    registry.validate_provider(name)
    info = registry.provider_info[name]
    models = ", ".join(registry.available_models(name))
    return "\n".join(
        [
            f"Provider: {name}",
            f"Label: {info['label']}",
            f"Default Base URL: {info['default_base_url']}",
            f"Default Model: {info['default_model']}",
            f"Available Models: {models}",
        ]
    )


def format_provider_current() -> str:
    resolution = resolve()
    return "\n".join(
        [
            f"provider: {resolution.provider} [{resolution.provider_source}]",
            f"model: {resolution.model} [{resolution.model_source}]",
        ]
    )


def _parse_use_args(args: list[str]) -> tuple[str, str]:
    provider = args[0]
    scope = _parse_scope(args[1:])
    return provider, scope


def _parse_scope(args: list[str]) -> str:
    scope = "user"
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--scope" and idx + 1 < len(args):
            scope = args[idx + 1]
            idx += 2
            continue
        raise ProviderCommandError(f"Unknown argument: {token}")
    return scope
