"""Cache warning module with LRU eviction for memory safety.

This module provides a CacheWarning class that maintains a bounded cache of
cache warning states by source, with automatic eviction of the oldest entry
when the cache reaches capacity.

Designed to prevent memory leaks in long-running daemon/swarm sessions where
querySource might be typed as `any`, producing many unique source values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Maximum number of source entries before oldest eviction
MAX_SOURCE_ENTRIES = 50


@dataclass
class CacheWarningState:
    """State for a cache warning entry.

    Attributes:
        warned: Whether a warning has been issued for this source.
        count: Number of times this source has been seen.
    """

    warned: bool = False
    count: int = 0


class CacheWarning:
    """Cache warning manager with LRU-style eviction.

    Maintains a dictionary of cache warning states keyed by source string.
    When capacity is reached, the oldest entry (FIFO) is evicted before
    inserting the new entry.

    Note:
        This class is not thread-safe. Callers must ensure serialized access.
    """

    def __init__(self) -> None:
        self.cache_warning_state_by_source: dict[str, CacheWarningState] = {}

    def update(self, source: str, state: CacheWarningState) -> None:
        """Update or insert a cache warning state for a source.

        If the cache has reached capacity, the oldest entry is evicted
        before inserting the new state.

        Args:
            source: The source identifier for this warning state.
            state: The cache warning state to store.
        """
        if len(self.cache_warning_state_by_source) >= MAX_SOURCE_ENTRIES:
            oldest_key = next(iter(self.cache_warning_state_by_source))
            del self.cache_warning_state_by_source[oldest_key]
        self.cache_warning_state_by_source[source] = state

    def get(self, source: str) -> Optional[CacheWarningState]:
        """Retrieve the cache warning state for a source.

        Args:
            source: The source identifier to look up.

        Returns:
            The CacheWarningState if found, None otherwise.
        """
        return self.cache_warning_state_by_source.get(source)

    def reset_for_test(self) -> None:
        """Clear all entries from the cache.

        Intended for test isolation. Not for use in production code.
        """
        self.cache_warning_state_by_source.clear()