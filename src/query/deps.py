"""Query deps — 4 narrow injection slots for testability.

Mirrors TS ``query/deps.ts:21-40``. Passing a custom ``QueryDeps``
into ``QueryParams`` lets tests inject fakes for ``callModel`` /
``microcompact`` / ``autocompact`` directly without monkey-patching
the module-level imports.

The pattern is intentionally narrow (4 deps, not 40). Followup
phases can add ``run_tools``, ``handle_stop_hooks``, etc., as those
become testability bottlenecks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4


def _default_uuid() -> str:
    return uuid4().hex


@dataclass
class QueryDeps:
    """I/O dependencies for ``query()``.

    Slot semantics:
      * ``call_model``: invoked with ``(provider, messages, system_prompt,
        tools, max_output_tokens_override, model)`` → returns
        ``(list[AssistantMessage], list[ToolUseBlock])``. The fallback
        loop in query.py expects this contract.
      * ``microcompact``: invoked with ``(messages,)`` →
        ``(compacted_messages, tokens_saved)``. Mirrors
        ``services/compact/compact.microcompact_messages``.
      * ``autocompact``: invoked with the full async signature of
        ``services/compact/autocompact.auto_compact_if_needed``.
      * ``uuid``: returns a new opaque string id.
    """
    call_model: Callable[..., Any] | None = None
    microcompact: Callable[..., Any] | None = None
    autocompact: Callable[..., Any] | None = None
    uuid: Callable[[], str] = field(default=_default_uuid)


def production_deps() -> QueryDeps:
    """Default deps factory. Mirrors TS ``productionDeps()`` at
    ``query/deps.ts:33``.

    The model caller is left as ``None`` so the existing in-loop
    ``_call_model_sync`` continues to drive the production path —
    swapping it requires plumbing the deps into the call site, which
    is done in query.py when ``deps.call_model`` is non-None.
    """
    from ..services.compact.autocompact import auto_compact_if_needed
    from ..services.compact.compact import microcompact_messages
    return QueryDeps(
        call_model=None,
        microcompact=microcompact_messages,
        autocompact=auto_compact_if_needed,
        uuid=_default_uuid,
    )
