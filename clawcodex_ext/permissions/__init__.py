"""Downstream permission extensions.

Registers the ``bypassPermissions → dontAsk`` cycle step so Shift+Tab
cycles through the downstream ``dontAsk`` mode after ``bypassPermissions``.
"""

from __future__ import annotations


def install_permission_extensions() -> None:
    """Register downstream permission cycle steps.

    Idempotent — safe to call more than once.
    """
    from src.permissions.cycle import register_cycle_step

    register_cycle_step(
        "bypassPermissions", "dontAsk", after="bypassPermissions"
    )
