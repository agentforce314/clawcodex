"""Analytics event sinks.

Mirrors TypeScript analytics/sink.ts — pluggable destinations for analytics events.
"""
from __future__ import annotations

import json
import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .events import AnalyticsEvent

logger = logging.getLogger(__name__)


class AnalyticsSink(ABC):
    """Base class for analytics event destinations."""

    @abstractmethod
    def emit(self, event: AnalyticsEvent) -> None:
        """Emit an analytics event."""

    def flush(self) -> None:
        """Flush any buffered events."""

    def close(self) -> None:
        """Close the sink and release resources."""


class NullSink(AnalyticsSink):
    """Discards all events. Default sink."""

    def emit(self, event: AnalyticsEvent) -> None:
        pass


class ConsoleSink(AnalyticsSink):
    """Prints events to stderr."""

    def emit(self, event: AnalyticsEvent) -> None:
        print(
            f"[analytics] {event.type.value} session={event.session_id} model={event.model}",
            file=sys.stderr,
        )


class FileSink(AnalyticsSink):
    """Appends events as JSONL to a file."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._buffer: list[str] = []
        self._max_buffer = 50

    def emit(self, event: AnalyticsEvent) -> None:
        entry = {
            "type": event.type.value,
            "timestamp": event.timestamp,
            "session_id": event.session_id,
            "model": event.model,
            **event.data,
        }
        self._buffer.append(json.dumps(entry, default=str))
        if len(self._buffer) >= self._max_buffer:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        try:
            with self._path.open("a") as f:
                for line in self._buffer:
                    f.write(line + "\n")
            self._buffer.clear()
        except OSError:
            logger.exception("Failed to flush analytics to %s", self._path)

    def close(self) -> None:
        self.flush()
