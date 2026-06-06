"""Downstream frontend extensions — plugin-based frontend registry."""

from clawcodex_ext.frontend.protocol import Frontend, FrontendPlugin
from clawcodex_ext.frontend.registry import get_frontend, list_frontends, register_frontend

# Import all plugins to trigger @register_frontend decorator
from clawcodex_ext.frontend import headless  # noqa: F401
from clawcodex_ext.frontend import repl  # noqa: F401
from clawcodex_ext.frontend import tui  # noqa: F401

__all__ = [
    "Frontend",
    "FrontendPlugin",
    "get_frontend",
    "list_frontends",
    "register_frontend",
]