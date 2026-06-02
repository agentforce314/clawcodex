"""Runtime slash commands for provider/model switching."""

from __future__ import annotations

from typing import Any

from clawcodex_ext.cli.model_cmd.commands import format_model_list
from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from clawcodex_ext.cli.model_cmd.store import ModelStore
from clawcodex_ext.cli.provider_cmd.commands import format_provider_list
from src.command_system.types import LocalCommand, LocalCommandResult


def register_runtime_commands(registry: Any | None = None) -> None:
    from src.command_system.registry import get_command_registry

    reg = registry or get_command_registry()
    for command in (_provider_command(), _model_command()):
        reg.register(command)


def _provider_command() -> LocalCommand:
    command = LocalCommand(
        name="provider",
        description="Show or switch the active provider",
        argument_hint="[list|current|NAME|use NAME]",
    )
    command.set_call(_provider_call)
    return command


def _model_command() -> LocalCommand:
    command = LocalCommand(
        name="model",
        description="Show or switch the active model",
        argument_hint="[list|current|NAME|use NAME] [--provider NAME]",
    )
    command.set_call(_model_call)
    return command


def _provider_call(args: str, context: Any) -> LocalCommandResult:
    tokens = args.split()
    command = tokens[0] if tokens else "current"

    if command == "list":
        return _text(format_provider_list())
    if command == "current":
        return _text(_format_runtime_current(context))
    if command == "use" and len(tokens) >= 2:
        provider = tokens[1]
    elif command not in {"use"}:
        provider = command
    else:
        return _text("usage: /provider [list|current|NAME|use NAME]")

    runtime = _runtime(context)
    ModelStore().set_default_provider(provider)
    runtime.swap_provider(provider)
    _sync_context(context, runtime)
    return _text(_format_runtime_current(context, prefix=f"Provider switched to: {provider}"))


def _model_call(args: str, context: Any) -> LocalCommandResult:
    tokens = args.split()
    command = tokens[0] if tokens else "current"

    if command == "list":
        provider = _parse_provider_flag(tokens[1:])
        return _text(format_model_list(provider))
    if command == "current":
        return _text(_format_runtime_current(context))

    rest = tokens[1:] if command == "use" else tokens
    if not rest:
        return _text("usage: /model [list|current|NAME|use NAME] [--provider NAME]")

    model, provider = _parse_model_args(rest)
    registry = ModelRegistry()
    if provider is None:
        provider = registry.infer_provider_for_model(model)
    registry.validate_model(model, provider)

    store = ModelStore(registry)
    store.set_default_provider(provider)
    store.set_default_model(provider, model)

    runtime = _runtime(context)
    runtime.swap_provider(provider, model)
    _sync_context(context, runtime)
    return _text(_format_runtime_current(context, prefix=f"Model switched to: {model}"))


def _parse_provider_flag(tokens: list[str]) -> str | None:
    provider = None
    idx = 0
    while idx < len(tokens):
        if tokens[idx] == "--provider" and idx + 1 < len(tokens):
            provider = tokens[idx + 1]
            idx += 2
            continue
        raise ValueError(f"Unknown argument: {tokens[idx]}")
    return provider


def _parse_model_args(tokens: list[str]) -> tuple[str, str | None]:
    model = tokens[0]
    provider = _parse_provider_flag(tokens[1:])
    return model, provider


def _runtime(context: Any) -> Any:
    runtime = getattr(context, "runtime_context", None)
    if runtime is None:
        raise ValueError("Runtime context is not available")
    return runtime


def _sync_context(context: Any, runtime: Any) -> None:
    context.provider = runtime.provider
    context.tool_registry = runtime.tool_registry
    context.tool_context = runtime.tool_context


def _format_runtime_current(context: Any, *, prefix: str | None = None) -> str:
    runtime = _runtime(context)
    lines = []
    if prefix:
        lines.append(prefix)
    lines.extend(
        [
            f"provider: {runtime.provider_name}",
            f"model: {getattr(runtime.provider, 'model', runtime.options.model)}",
        ]
    )
    return "\n".join(lines)


def _text(value: str) -> LocalCommandResult:
    return LocalCommandResult(type="text", value=value)
