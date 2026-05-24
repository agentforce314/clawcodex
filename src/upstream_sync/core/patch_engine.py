# upstream_sync/core/patch_engine.py
"""Patch application engine abstraction.

Defines the ``PatchEngine`` Protocol and a factory that instantiates the
concrete adapter (quilt, git-am, or custom) based on configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from upstream_sync.config import PatchConfig


@dataclass
class ApplyResult:
    """Outcome of a patch-queue application attempt."""

    success: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)


@runtime_checkable
class PatchEngine(Protocol):
    """Protocol for patch application backends."""

    def apply_all(self, patch_dir: Path, series_file: Path) -> ApplyResult:
        """Apply the entire patch queue."""
        ...

    def pop_all(self) -> None:
        """Remove all applied patches."""
        ...

    def refresh(self, patch_name: str) -> None:
        """Refresh a single patch to match the current working-tree changes."""
        ...

    def status(self) -> dict:
        """Return engine-specific status information."""
        ...


def create_engine(config: PatchConfig) -> PatchEngine:
    """Factory: create the appropriate ``PatchEngine`` from configuration.

    Args:
        config: Patch queue configuration (engine name + engine-specific options).

    Returns:
        A concrete ``PatchEngine`` implementation.

    Raises:
        ValueError: If the engine name is unknown or missing required options.
    """
    from upstream_sync.adapters.quilt import QuiltEngine
    from upstream_sync.adapters.git_am import GitAmEngine
    from upstream_sync.adapters.custom import CustomEngine

    if config.engine == "quilt":
        return QuiltEngine()
    elif config.engine == "git-am":
        return GitAmEngine()
    elif config.engine == "custom":
        if not config.custom_command:
            raise ValueError("custom engine requires custom_command")
        return CustomEngine(config.custom_command)
    else:
        raise ValueError(f"Unknown patch engine: {config.engine}")
