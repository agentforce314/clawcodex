"""Stub mirror of ``typescript/src/assistant/AssistantSessionChooser.tsx``.

The upstream TS file is itself a stub annotated
``// Stub — AssistantSessionChooser not included in source snapshot``.
We mirror the stub here so the parity audit reflects that *both* trees
are intentionally empty. The Python REPL has no interactive picker for
``claude assistant`` yet; this module exists so future ports of
``dialogLaunchers`` / ``main.tsx`` have a real import target with the
same export name (``AssistantSessionChooser``, PascalCase) as the TS
source — no rename needed at the call sites.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def AssistantSessionChooser(  # noqa: N802 — mirrors TS React component name
    sessions: Sequence[Any],
    on_select: Callable[[str], None],
    on_cancel: Callable[[], None],
) -> None:
    """Pick a session to attach to. No-op stub (returns ``None``).

    Matches the TS prop shape ``{ sessions, onSelect, onCancel }`` so
    callers can be ported without changing call sites. Name is PascalCase
    to match the TS React component export — a future port of
    ``dialogLaunchers.tsx::launchAssistantSessionChooser`` should be able
    to write ``from src.assistant.session_chooser import AssistantSessionChooser``
    without an alias.
    """
    del sessions, on_select, on_cancel  # silence unused-argument linters
    return None


__all__ = ['AssistantSessionChooser']
