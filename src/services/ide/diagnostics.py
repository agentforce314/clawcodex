"""IDE diagnostics collection.

Mirrors TypeScript ide/diagnostics.ts — collects LSP diagnostics from the IDE
and makes them available to the agent context.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from .types import IDEDiagnostic, IDEDiagnosticSeverity

logger = logging.getLogger(__name__)


class DiagnosticsCollector:
    """Collects and queries LSP diagnostics from the IDE."""

    def __init__(self) -> None:
        self._diagnostics: dict[str, list[IDEDiagnostic]] = defaultdict(list)
        self._listeners: list[Callable[[str, list[IDEDiagnostic]], None]] = []

    def update_file(self, file_path: str, diagnostics: list[IDEDiagnostic]) -> None:
        """Replace all diagnostics for a file."""
        self._diagnostics[file_path] = list(diagnostics)
        for listener in self._listeners:
            try:
                listener(file_path, diagnostics)
            except Exception:
                logger.exception("Error in diagnostics listener")

    def get_file(self, file_path: str) -> list[IDEDiagnostic]:
        """Get diagnostics for a specific file."""
        return list(self._diagnostics.get(file_path, []))

    def get_errors(self, file_path: str | None = None) -> list[IDEDiagnostic]:
        """Get error-severity diagnostics, optionally filtered by file."""
        result: list[IDEDiagnostic] = []
        files = [file_path] if file_path else list(self._diagnostics.keys())
        for f in files:
            for d in self._diagnostics.get(f, []):
                if d.severity == IDEDiagnosticSeverity.ERROR:
                    result.append(d)
        return result

    def get_all(self) -> dict[str, list[IDEDiagnostic]]:
        """Get all diagnostics by file."""
        return dict(self._diagnostics)

    def clear(self, file_path: str | None = None) -> None:
        """Clear diagnostics for a file, or all if file_path is None."""
        if file_path:
            self._diagnostics.pop(file_path, None)
        else:
            self._diagnostics.clear()

    @property
    def total_error_count(self) -> int:
        return len(self.get_errors())

    @property
    def file_count(self) -> int:
        return len(self._diagnostics)

    def on_update(self, listener: Callable[[str, list[IDEDiagnostic]], None]) -> Callable[[], None]:
        """Register a listener for diagnostic updates. Returns unsubscribe."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe
