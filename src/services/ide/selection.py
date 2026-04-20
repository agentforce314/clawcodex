"""IDE selection tracking.

Mirrors TypeScript ide/selection.ts — tracks the current editor selection
shared by the IDE extension.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .types import IDERange, IDESelection

logger = logging.getLogger(__name__)


@dataclass
class SelectionEntry:
    """A timestamped selection entry."""
    selection: IDESelection
    timestamp: float


class SelectionTracker:
    """Tracks IDE selections with history and expiry."""

    def __init__(self, max_history: int = 10, expiry_seconds: float = 300.0) -> None:
        self._history: list[SelectionEntry] = []
        self._max_history = max_history
        self._expiry_seconds = expiry_seconds
        self._listeners: list[Callable[[IDESelection], None]] = []

    @property
    def current(self) -> IDESelection | None:
        """Get the most recent non-expired selection."""
        self._prune_expired()
        if self._history:
            return self._history[-1].selection
        return None

    @property
    def history(self) -> list[IDESelection]:
        """Get all non-expired selections, newest first."""
        self._prune_expired()
        return [e.selection for e in reversed(self._history)]

    def update(self, selection: IDESelection) -> None:
        """Record a new selection from the IDE."""
        entry = SelectionEntry(selection=selection, timestamp=time.time())
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        for listener in self._listeners:
            try:
                listener(selection)
            except Exception:
                logger.exception("Error in selection listener")

    def on_selection(self, listener: Callable[[IDESelection], None]) -> Callable[[], None]:
        """Register a selection change listener. Returns unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def clear(self) -> None:
        """Clear all selection history."""
        self._history.clear()

    def _prune_expired(self) -> None:
        now = time.time()
        self._history = [
            e for e in self._history
            if (now - e.timestamp) < self._expiry_seconds
        ]
