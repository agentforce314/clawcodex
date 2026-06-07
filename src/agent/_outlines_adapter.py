"""Facade — agent/_outlines_adapter.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing from
src.agent._outlines_adapter import … call sites continue to work
during the migration.  New code should import from
clawcodex_ext.agent._outlines_adapter directly.
"""

from clawcodex_ext.agent._outlines_adapter import (  # noqa: F401
    is_outlines_available,
    OutlinesStructuredOutput,
    TokenBudgetAnalysis,
    ToolCallDecision,
    create_structured_output_handler,
)

__all__ = [
    "is_outlines_available",
    "OutlinesStructuredOutput",
    "TokenBudgetAnalysis",
    "ToolCallDecision",
    "create_structured_output_handler",
]
