"""Facade — entrypoints/orchestrator.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.entrypoints.orchestrator import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.entrypoints.orchestrator`` directly.
"""

from clawcodex_ext.entrypoints.orchestrator import (  # noqa: F401
    run_orchestrator_subcommand,
)

__all__ = [
    "run_orchestrator_subcommand",
]
