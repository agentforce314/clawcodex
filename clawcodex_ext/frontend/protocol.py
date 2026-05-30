"""Downstream Frontend protocol and plugin base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class Frontend(Protocol):
    """Protocol for CLI frontends (REPL, TUI, headless)."""

    @property
    def name(self) -> str:
        """Unique identifier, e.g. 'repl', 'tui', 'headless'."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name, e.g. 'Interactive REPL'."""
        ...

    def run(self, ctx: Any, argv: list[str]) -> int:
        """Run the frontend with the given RuntimeContext and CLI argv.

        Args:
            ctx: RuntimeContext built from CLI args.
            argv: Remaining command-line arguments (after subcommand).

        Returns:
            CLI exit code.
        """
        ...


class FrontendPlugin(ABC):
    """Base class for frontend plugins.

    Subclass this to create a new frontend. Use :func:`register_frontend`
    as a decorator to register it with the frontend registry.
    """

    name: str
    display_name: str

    @abstractmethod
    def run(self, ctx: Any, argv: list[str]) -> int:
        """Run the frontend with the given RuntimeContext and CLI argv."""
        ...

    def argparse_group(self, parser) -> None:
        """Add frontend-specific argparse arguments (optional hook)."""
        pass