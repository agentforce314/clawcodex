"""
Compact service — boundary markers and command-facing compaction wrapper.

``compact_service.messages`` provides boundary-marker dataclasses and
factory functions used by the compaction pipeline (``services/compact/``).

``compact_service.service`` provides the ``compact_conversation()`` wrapper
that the ``/compact`` command handler expects: it accepts a ``Conversation``
object, delegates to the unified pipeline, and mutates the conversation in place.
"""

from __future__ import annotations

from .messages import (
    CompactBoundaryMetadata,
    PreservedSegment,
    annotate_boundary_with_preserved_segment,
    create_compact_boundary_message,
    create_compact_summary_message,
    get_messages_after_boundary,
    is_compact_boundary_message,
)
from .service import CompactResult, compact_conversation

__all__ = [
    # — service —
    "CompactResult",
    "compact_conversation",
    # — messages —
    "CompactBoundaryMetadata",
    "PreservedSegment",
    "annotate_boundary_with_preserved_segment",
    "create_compact_boundary_message",
    "create_compact_summary_message",
    "get_messages_after_boundary",
    "is_compact_boundary_message",
]
