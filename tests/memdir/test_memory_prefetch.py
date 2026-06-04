"""Smoke test for the deprecated `src.context_system.memory_prefetch` shim.

The full recall pipeline lives in ``src.memdir`` now; coverage is in
``tests/test_memdir_scan_recall.py``. This file exists only to verify the
shim re-exports the public surface so existing imports keep working for
one release.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock


class CompatShimTest(unittest.TestCase):
    def test_public_surface_reexported(self):
        from src.context_system.memory_prefetch import (
            MAX_RELEVANT_MEMORIES,
            MemoryHeader,
            RelevantMemory,
            find_relevant_memories,
            format_memory_manifest,
            scan_memory_files,
        )

        self.assertEqual(MAX_RELEVANT_MEMORIES, 5)
        self.assertTrue(callable(find_relevant_memories))
        self.assertTrue(callable(format_memory_manifest))
        self.assertTrue(callable(scan_memory_files))
        self.assertTrue(hasattr(MemoryHeader, "__init__"))
        self.assertTrue(hasattr(RelevantMemory, "__init__"))

    def test_shim_returns_empty_without_provider(self):
        # The keyword fallback was removed (chapter rejects it). With no
        # provider, the shim should return an empty list rather than
        # retrieving via keyword match.
        from src.context_system.memory_prefetch import find_relevant_memories

        result = asyncio.new_event_loop().run_until_complete(
            find_relevant_memories(
                "anything",
                "/nonexistent/dir",
                provider=None,
            )
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
