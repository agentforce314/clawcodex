"""Downstream Frontend registry — plugin registration and lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clawcodex_ext.frontend.protocol import FrontendPlugin


_FRONTENDS: dict[str, type[FrontendPlugin]] = {}
_INSTANCES: dict[str, FrontendPlugin] = {}


def register_frontend(cls: type[FrontendPlugin]) -> type[FrontendPlugin]:
    """Decorator to register a frontend plugin.

    Usage::

        @register_frontend
        class MyFrontend(FrontendPlugin):
            name = "myfrontend"
            display_name = "My Frontend"
            ...
    """
    _FRONTENDS[cls.name] = cls
    _INSTANCES[cls.name] = cls()  # singleton instance
    return cls


def get_frontend(name: str) -> FrontendPlugin | None:
    """Return the registered frontend instance for ``name``, or None."""
    return _INSTANCES.get(name)


def list_frontends() -> list[FrontendPlugin]:
    """Return all registered frontend instances."""
    return list(_INSTANCES.values())