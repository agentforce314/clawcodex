"""Runtime slash commands for provider/model switching.

Both ``/provider`` and ``/model`` share a unified surface that mirrors the
CLI subcommands in :mod:`clawcodex_ext.cli.provider_cmd` and
:mod:`clawcodex_ext.cli.model_cmd`:

* ``/provider`` (no args)            — show current provider + list all
* ``/provider <NAME>``               — switch to ``<NAME>``
* ``/model``    (no args)            — show current provider/model + list all
* ``/model <NAME> [--provider P]``   — switch to ``<NAME>`` (inferred or
  explicit provider)

The legacy ``list`` / ``current`` / ``use <NAME>`` subcommand spellings are
no longer recognised — they were folded into the unified form so the
slash command behaves identically in REPL and TUI.
"""

from __future__ import annotations

from typing import Any

from clawcodex_ext.cli.model_cmd.commands import format_model_list
from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
from clawcodex_ext.cli.model_cmd.store import ModelStore
from clawcodex_ext.cli.model_cmd.errors import UnknownModelError, ProviderMismatchError
from clawcodex_ext.cli.provider_cmd.commands import format_provider_list
from clawcodex_ext.cli.provider_cmd.errors import UnknownProviderError
from src.command_system.types import LocalCommand, LocalCommandResult


def register_runtime_commands(registry: Any | None = None) -> None:
    from src.command_system.registry import get_command_registry

    reg = registry or get_command_registry()
    for command in (_provider_command(), _model_command()):
        reg.register(command)


def _provider_command() -> LocalCommand:
    command = LocalCommand(
        name="provider",
        description="Show current provider (and available list), or switch to a named provider",
        argument_hint="[NAME]",
    )
    command.set_call(_provider_call)
    return command


def _model_command() -> LocalCommand:
    command = LocalCommand(
        name="model",
        description="Show current model (and available list), or switch to a named model",
        argument_hint="[NAME [--provider NAME]]",
    )
    command.set_call(_model_call)
    return command


def _provider_call(args: str, context: Any) -> LocalCommandResult:
    tokens = args.split()

    if not tokens:
        lines = [
            _format_runtime_current(context),
            "",
            format_provider_list(),
        ]
        return _text("\n".join(lines))

    provider = tokens[0]
    runtime = _runtime(context)

    warnings: list[str] = []
    try:
        ModelStore().set_default_provider(provider)
    except UnknownProviderError:
        warnings.append(f"Warning: unknown provider '{provider}' — proceeding anyway")
        from src.config import set_default_provider as _set_dp
        _set_dp(provider)

    runtime.swap_provider(provider)
    _sync_context(context, runtime)

    lines = [f"Provider switched to: {provider}"]
    lines.extend(warnings)
    lines.append("")
    lines.append(_format_runtime_current(context))
    return _text("\n".join(lines))


def _model_call(args: str, context: Any) -> LocalCommandResult:
    tokens = args.split()

    if not tokens:
        lines = [
            _format_runtime_current(context),
            "",
            format_model_list(),
        ]
        return _text("\n".join(lines))

    try:
        model, provider = _parse_model_args(tokens)
    except ValueError as exc:
        return _text(f"usage: /model [NAME [--provider NAME]]\n{exc}")

    warnings: list[str] = []
    registry = ModelRegistry()
    if provider is None:
        try:
            provider = registry.infer_provider_for_model(model)
        except UnknownModelError:
            provider = "anthropic"
            warnings.append(f"Warning: unknown model '{model}' — defaulting to provider 'anthropic'")
    else:
        try:
            registry.validate_provider(provider)
        except UnknownProviderError:
            warnings.append(f"Warning: unknown provider '{provider}' — proceeding anyway")

    try:
        registry.validate_model(model, provider)
    except UnknownModelError:
        warnings.append(f"Warning: unknown model '{model}' — proceeding anyway")
    except ProviderMismatchError:
        warnings.append(f"Warning: model '{model}' not listed for provider '{provider}' — proceeding anyway")

    store = ModelStore(registry)
    try:
        store.set_default_provider(provider)
    except UnknownProviderError:
        from src.config import set_default_provider as _set_dp
        _set_dp(provider)
    try:
        store.set_default_model(provider, model)
    except (UnknownModelError, ProviderMismatchError):
        from src.config import get_provider_config, set_api_key
        current = get_provider_config(provider)
        base_url = current.get("base_url")
        if base_url is None:
            try:
                from src.providers import PROVIDER_INFO
                base_url = PROVIDER_INFO[provider]["default_base_url"]
            except (KeyError, ImportError):
                base_url = ""
        set_api_key(
            provider,
            api_key=current.get("api_key", ""),
            base_url=base_url,
            default_model=model,
        )

    runtime = _runtime(context)
    runtime.swap_provider(provider, model)
    _sync_context(context, runtime)
    lines = [f"Model switched to: {model} (provider: {provider})"]
    lines.extend(warnings)
    lines.append("")
    lines.append(_format_runtime_current(context))
    return _text("\n".join(lines))


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
