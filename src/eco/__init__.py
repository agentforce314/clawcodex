"""eco — RTK-style token compression for Bash tool output.

Ported methods from the RTK reference analysis (my-docs/token-compression/RTK/):
deterministic, filter-based compression of the *model-bound* rendering of Bash
tool results, guarded so it can never make things worse and never lose data
unrecoverably. Toggled per session with the ``/eco`` slash command.

Layering (mirrors RTK's core): pure string filters (:mod:`filters`), a
never-worse guard + chars/4 estimator (:mod:`guard`), raw-output tee recovery
(:mod:`tee`), session state + savings stats (:mod:`state`), and the dispatch
engine (:mod:`engine`). Nothing in this package imports tool_system — the Bash
tool calls in, not the other way around.
"""

from .engine import EcoOutcome, compress_bash_output
from .guard import estimate_tokens, never_worse
from .state import (
    eco_stats,
    is_eco_session,
    record_compression,
    reset_eco,
    set_eco_session,
)

__all__ = [
    "EcoOutcome",
    "compress_bash_output",
    "estimate_tokens",
    "never_worse",
    "eco_stats",
    "is_eco_session",
    "record_compression",
    "reset_eco",
    "set_eco_session",
]
