"""Analytics subsystem.

Event logging, session metadata, and event sinks.
Mirrors TypeScript analytics/ directory.
"""
from __future__ import annotations

from .events import AnalyticsEvent, EventType, log_event
from .metadata import SessionAnalyticsMetadata, collect_session_metadata
from .sink import AnalyticsSink, ConsoleSink, FileSink, NullSink

__all__ = [
    "AnalyticsEvent",
    "AnalyticsSink",
    "ConsoleSink",
    "EventType",
    "FileSink",
    "NullSink",
    "SessionAnalyticsMetadata",
    "collect_session_metadata",
    "log_event",
]
