"""Bounded persistent memory + self-improvement review (hermes-agent port).

Two subsystems, ported from ``reference_projects/hermes-agent`` per the
analysis in ``my-docs/memory-and-self-improvement/``:

* **Bounded memory** (``store``): two §-delimited, char-budgeted stores —
  ``MEMORY.md`` (agent notes) and ``USER.md`` (user profile) under
  ``<user config dir>/memories/`` — written by the ``Memory`` tool and
  injected into the system prompt as a frozen per-session snapshot.
* **Self-improvement review** (``review``): a post-turn background fork
  that replays the conversation and saves durable facts via the ``Memory``
  tool. Trigger plumbing lives in the agent-server worker.

Support modules: ``threat_patterns`` (write/snapshot injection scanning),
``provenance`` (foreground vs background-review write origin), and
``write_approval`` (optional stage-and-review gate for memory writes).
"""

from .provenance import (
    BACKGROUND_REVIEW,
    get_current_write_origin,
    is_background_review,
    reset_current_write_origin,
    set_current_write_origin,
)
from .store import (
    ENTRY_DELIMITER,
    MemoryStore,
    get_memory_dir,
    get_memory_store,
    reset_memory_store_cache,
)

__all__ = [
    "BACKGROUND_REVIEW",
    "ENTRY_DELIMITER",
    "MemoryStore",
    "get_current_write_origin",
    "get_memory_dir",
    "get_memory_store",
    "is_background_review",
    "reset_current_write_origin",
    "reset_memory_store_cache",
    "set_current_write_origin",
]
