"""Ch5/G.1 — narrow dependency injection container for the query loop.

Mirrors TS query/deps.ts:21-40. The loop carries an immutable
``QueryDeps`` snapshot with four function references (call_model,
microcompact, autocompact, uuid). Tests pass a custom QueryDeps into
QueryParams to inject fakes without monkey-patching module imports.

The ``production_deps()`` factory wires up the real implementations
from the canonical source paths (see import comments below — critic
review flagged that the canonical sources are ``context_system`` for
microcompact and ``services.compact.autocompact`` for
``auto_compact_if_needed``, NOT re-exports through
``services.compact.compact``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4


@dataclass
class QueryDeps:
    """Four injected dependencies for the query loop.

    Mirrors TS ``QueryDeps`` at query/deps.ts:21-31. Using callable
    slots (not class instances) keeps the deps narrow and the test
    seam tiny — a fake ``call_model`` can be a one-line lambda.
    """
    call_model: Callable[..., Any]
    microcompact: Callable[..., Any]
    autocompact: Callable[..., Any]
    uuid: Callable[[], str] = field(
        default_factory=lambda: lambda: uuid4().hex
    )


def production_deps() -> QueryDeps:
    """Wire the real implementations.

    Imports the canonical source paths (per critic review):
      - ``microcompact_messages`` lives in
        ``src/context_system/microcompact.py`` (re-exported through
        ``services/compact/compact.py`` but the canonical source is
        the context_system package).
      - ``auto_compact_if_needed`` is the real export name in
        ``src/services/compact/autocompact.py`` (NOT
        ``autocompact_if_needed`` as a previous draft assumed).
    """
    # Local imports keep the deps module's import graph minimal and
    # avoid pulling in heavy provider/registry code at startup.
    from ..context_system.microcompact import microcompact_messages
    from ..services.compact.autocompact import auto_compact_if_needed
    from .query import _call_model_sync

    return QueryDeps(
        call_model=_call_model_sync,
        microcompact=microcompact_messages,
        autocompact=auto_compact_if_needed,
    )
