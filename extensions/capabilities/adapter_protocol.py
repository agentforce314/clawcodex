"""Adapter Protocol — unified interface for optional dependency adapters.

This module provides:
- ``env_switch()`` — unified environment-variable switch helper
- ``dependency_available()`` — unified optional-dependency detection
- ``AdapterRegistry`` — central registry for discovering all adapters
- ``AdapterProtocol`` — structural typing base for adapters

All 7 adapters (outlines, gitpython, pluggy, treesitter, pydantic,
frontmatter, litellm) use these utilities instead of duplicating the
``os.getenv`` + ``try/except ImportError`` + ``is_*_available()`` pattern.

Usage in an adapter module::

    from extensions.capabilities.adapter_protocol import (
        env_switch, dependency_available, AdapterRegistry,
    )

    _USE_MYADAPTER = env_switch("CLAW_USE_MYADAPTER")
    _MYADAPTER_AVAILABLE = dependency_available("mydep")

    @AdapterRegistry.register("myadapter", env_var="CLAW_USE_MYADAPTER")
    class MyAdapter:
        ...
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "AdapterInfo",
    "AdapterProtocol",
    "AdapterRegistry",
    "dependency_available",
    "env_switch",
]


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def env_switch(var_name: str, default: str = "true") -> bool:
    """Return ``True`` when *var_name* is ``'true'``, ``'1'``, or absent.

    Example::

        _USE_GITPYTHON = env_switch("CLAW_USE_GITPYTHON")
    """
    return os.getenv(var_name, default).lower() in ("true", "1")


def dependency_available(module_name: str) -> bool:
    """Return ``True`` if *module_name* can be imported.

    Example::

        _GITPYTHON_AVAILABLE = dependency_available("git")
    """
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def is_provider_adapter(adapter: type) -> bool:
    """Return ``True`` if *adapter* is a provider adapter (has chat/stream)."""
    return hasattr(adapter, "chat") or hasattr(adapter, "chat_stream")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class AdapterInfo:
    """Metadata about a registered adapter."""

    name: str
    """Unique name (e.g. ``'outlines'``, ``'gitpython'``)."""

    env_var: str | None = None
    """Environment variable that gates this adapter (e.g. ``'CLAW_USE_GITPYTHON'``)."""

    dependency: str | None = None
    """PyPI package name of the optional dependency (e.g. ``'outlines'``)."""

    description: str = ""
    """Human-readable description."""

    is_enabled_by_default: bool = True
    """Whether the adapter is enabled when the env var is absent."""


class AdapterRegistry:
    """Central registry for optional-dependency adapters.

    Adapters self-register via the :meth:`register` classmethod decorator::

        @AdapterRegistry.register("gitpython", env_var="CLAW_USE_GITPYTHON")
        class GitPythonAdapter:
            ...

    Discovery helpers::

        AdapterRegistry.list() -> dict[str, AdapterInfo]
        AdapterRegistry.get("gitpython") -> AdapterInfo | None
    """

    _adapters: dict[str, AdapterInfo] = {}
    _loaded: bool = False

    @classmethod
    def register(
        cls,
        name: str,
        *,
        env_var: str | None = None,
        dependency: str | None = None,
        description: str = "",
        is_enabled_by_default: bool = True,
    ) -> Any:
        """Decorator that registers an adapter class.

        The decorated class is returned unchanged.
        """
        info = AdapterInfo(
            name=name,
            env_var=env_var,
            dependency=dependency,
            description=description or name,
            is_enabled_by_default=is_enabled_by_default,
        )

        def decorator(adapter_cls: type) -> type:
            cls._adapters[name] = info
            return adapter_cls

        return decorator

    @classmethod
    def get(cls, name: str) -> AdapterInfo | None:
        """Return metadata for *name*, or ``None``."""
        return cls._adapters.get(name)

    @classmethod
    def list(cls) -> dict[str, AdapterInfo]:
        """Return all registered adapters keyed by name."""
        return dict(cls._adapters)

    @classmethod
    def is_enabled(cls, name: str) -> bool:
        """Check whether adapter *name* is enabled (env-var gate)."""
        info = cls._adapters.get(name)
        if info is None:
            return False
        if info.env_var is None:
            return True
        default = "true" if info.is_enabled_by_default else "false"
        return env_switch(info.env_var, default=default)

    @classmethod
    def is_dependency_available(cls, name: str) -> bool:
        """Check whether adapter *name*'s dependency is installed."""
        info = cls._adapters.get(name)
        if info is None or info.dependency is None:
            return False
        return dependency_available(info.dependency)


# ---------------------------------------------------------------------------
# Protocol (structural typing)
# ---------------------------------------------------------------------------


class AdapterProtocol(Protocol):
    """Structural typing for optional-dependency adapters.

    An adapter module should expose at minimum:

    .. code-block:: python

        def is_available() -> bool: ...
    """

    name: str
    """Unique name, should match the registry key."""

    def is_available(self) -> bool:
        """Return ``True`` when the dependency is installed and the
        env var doesn't disable it."""
        ...
