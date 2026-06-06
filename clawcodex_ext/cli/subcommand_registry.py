"""Fast-path downstream CLI subcommand registry."""

from __future__ import annotations

from collections.abc import Callable

SubcommandHandler = Callable[[list[str]], int]

_SUBCOMMANDS: dict[str, SubcommandHandler] = {}
_LOADED = False


def register(name: str) -> Callable[[SubcommandHandler], SubcommandHandler]:
    """Register a fast-path subcommand handler."""

    def decorator(handler: SubcommandHandler) -> SubcommandHandler:
        _SUBCOMMANDS[name] = handler
        return handler

    return decorator


def get_subcommand(name: str) -> SubcommandHandler | None:
    load_builtin_subcommands()
    return _SUBCOMMANDS.get(name)


def load_builtin_subcommands() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    from clawcodex_ext.cli.provider_cmd import commands as _provider_commands  # noqa: F401
    from clawcodex_ext.cli.model_cmd import commands as _model_commands  # noqa: F401
    from clawcodex_ext.cli.pos_cmd import commands as _pos_commands  # noqa: F401
