"""HeadlessOptions Protocol — interface for headless session configuration.

This Protocol defines the contract for headless run options.
The concrete implementation is in src/entrypoints/headless.py (HeadlessOptions dataclass).

This allows src/api/query.py to configure headless sessions without
importing from upstream entrypoints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, IO, Protocol

__all__ = ["HeadlessOptionsProtocol"]


class HeadlessOptionsProtocol(Protocol):
    """Protocol for headless run configuration.

    Concrete implementation: src/entrypoints/headless.HeadlessOptions
    """

    @property
    def prompt(self) -> str | None:
        """Prompt text to execute (None for stream-json input mode)."""

    @property
    def output_format(self) -> str:
        """Output format: "text", "json", or "stream-json"."""

    @property
    def input_format(self) -> str:
        """Input format: "text" or "stream-json"."""

    @property
    def provider_name(self) -> str | None:
        """Provider name override."""

    @property
    def model(self) -> str | None:
        """Model name override."""

    @property
    def max_turns(self) -> int:
        """Maximum tool-use turns before stopping."""

    @property
    def permission_mode(self) -> str:
        """Permission mode: "default", "bypassPermissions", etc."""

    @property
    def is_bypass_permissions_mode_available(self) -> bool:
        """Whether bypass-permissions mode is available."""

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        """Allowed tool names."""

    @property
    def disallowed_tools(self) -> tuple[str, ...]:
        """Disallowed tool names."""

    @property
    def include_partial_messages(self) -> bool:
        """Whether to include partial text messages."""

    @property
    def verbose(self) -> bool:
        """Verbose output flag."""

    @property
    def stdin(self) -> IO[str] | None:
        """Stdin stream override."""

    @property
    def stdout(self) -> IO[str] | None:
        """Stdout stream override."""

    @property
    def stderr(self) -> IO[str] | None:
        """Stderr stream override."""

    @property
    def workspace_root(self) -> Path | None:
        """Workspace root override (default: cwd)."""


class HeadlessRunnerProtocol(Protocol):
    """Protocol for the headless run function.

    Concrete implementation: src/entrypoints/headless.run_headless
    """

    def __call__(self, options: HeadlessOptionsProtocol) -> int:
        """Run headless session. Returns exit code."""