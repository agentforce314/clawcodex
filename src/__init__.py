"""Claw Codex - Claude Code Python Implementation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clawcodex-cli")
except PackageNotFoundError:  # Running directly from an unpackaged checkout.
    __version__ = "1.2.1"
__author__ = "Claw Codex Team"

from .config import load_config, get_provider_config

try:  # pragma: no cover
    from .providers.base import BaseProvider
except Exception:  # pragma: no cover
    BaseProvider = None  # type: ignore[assignment]

__all__ = [
    "__version__",
    "__author__",
    "load_config",
    "get_provider_config",
    "BaseProvider",
]
