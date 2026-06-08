"""Provider factory — 二开 provider construction and registration.

Moved from ``src/providers/__init__.py`` so that the upstream package
remains a clean registry of built-in providers.  All call sites that
need ``create_provider``, ``register_provider``, or
``register_provider_info`` should import from this module (or via the
facade re-exports in ``src/providers/__init__.py``).

Architecture::

    src/providers/__init__.py           ← upstream built-in registry (get_provider_class, PROVIDER_INFO)
        ↑ import                        ← _EXTRA_PROVIDER_CLASSES dict lives here
    clawcodex_ext/providers/factory.py  ← this module (二开 factory + registration)
        ↑ import
    extensions/providers_ext/           ← LiteLLM fallback provider
"""

from __future__ import annotations

from src.providers import PROVIDER_INFO, _EXTRA_PROVIDER_CLASSES, get_provider_class
from src.providers.base import BaseProvider


# ---------------------------------------------------------------------------
# LiteLLM switch
# ---------------------------------------------------------------------------

def should_use_litellm() -> bool:
    """Return whether runtime provider creation should use LiteLLM."""
    from os import getenv

    return getenv("CLAW_USE_LITELLM", "").lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def create_provider(provider_name: str, *args, **kwargs) -> BaseProvider:
    """Create a provider instance for runtime use."""
    if should_use_litellm():
        from extensions.providers_ext import create_litellm_provider

        return create_litellm_provider(provider_name, *args, **kwargs)

    try:
        provider_cls = get_provider_class(provider_name)
    except ValueError:
        # Unknown provider — fallback to LiteLLM
        from extensions.providers_ext import create_litellm_provider

        return create_litellm_provider(provider_name, *args, **kwargs)

    return provider_cls(*args, **kwargs)


# ---------------------------------------------------------------------------
# Extension registration API
# ---------------------------------------------------------------------------

def register_provider(name: str, info: "ProviderInfo", cls: type | callable) -> None:
    """Register a new provider at runtime.

    Adds *info* to ``PROVIDER_INFO`` and registers *cls* so that
    ``get_provider_class(name)`` returns it.

    *cls* may be a ``BaseProvider`` subclass **or** a zero-arg callable
    that returns one.  The callable form supports lazy imports — it is
    invoked the first time ``get_provider_class(name)`` is called,
    and the result replaces the callable in the registry so subsequent
    lookups are direct.

    Idempotent: calling twice with the same *name* is a no-op
    (first registration wins).
    """
    register_provider_info(name, info)
    if name not in _EXTRA_PROVIDER_CLASSES:
        _EXTRA_PROVIDER_CLASSES[name] = cls


def register_provider_info(name: str, info: "ProviderInfo") -> None:
    """Add or update *info* in ``PROVIDER_INFO`` without a class mapping.

    Useful when the provider is served by LiteLLM or another generic
    backend that doesn't have a dedicated ``BaseProvider`` subclass.

    Also refreshes ``AVAILABLE_PROVIDERS`` so the new provider shows up
    in UI/CLI listings.
    """
    if name not in PROVIDER_INFO:
        PROVIDER_INFO[name] = info
        # Refresh the display dict so it reflects the new provider.
        # Use in-place update so that existing ``from src.providers import
        # AVAILABLE_PROVIDERS`` references (which bound at import time)
        # see the change.
        import src.providers as _pkg
        _pkg.AVAILABLE_PROVIDERS[name] = info["label"]


__all__ = [
    "create_provider",
    "should_use_litellm",
    "register_provider",
    "register_provider_info",
]
