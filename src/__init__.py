"""Claw Codex - Claude Code Python Implementation."""

__version__ = "0.1.0"
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
