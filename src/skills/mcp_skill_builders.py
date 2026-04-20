from __future__ import annotations

from typing import Any, Callable

_builders: dict[str, Callable[..., Any]] | None = None


def register_mcp_skill_builders(builders: dict[str, Callable[..., Any]]) -> None:
    global _builders
    if _builders is not None:
        return
    _builders = builders


def get_mcp_skill_builders() -> dict[str, Callable[..., Any]] | None:
    return _builders
