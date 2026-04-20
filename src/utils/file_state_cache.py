from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_ENTRIES = 500


class FileStateCache:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, path: str, *, read_through: bool = True) -> str | None:
        abs_path = os.path.abspath(path)
        async with self._lock:
            if abs_path in self._cache:
                self._cache.move_to_end(abs_path)
                return self._cache[abs_path]

        if not read_through:
            return None

        content = await self._read_file(abs_path)
        if content is not None:
            await self.set(abs_path, content)
        return content

    async def set(self, path: str, content: str) -> None:
        abs_path = os.path.abspath(path)
        async with self._lock:
            if abs_path in self._cache:
                self._cache.move_to_end(abs_path)
                self._cache[abs_path] = content
            else:
                self._cache[abs_path] = content
                while len(self._cache) > self._max_entries:
                    self._cache.popitem(last=False)

    async def invalidate(self, path: str) -> bool:
        abs_path = os.path.abspath(path)
        async with self._lock:
            if abs_path in self._cache:
                del self._cache[abs_path]
                return True
            return False

    async def invalidate_many(self, paths: list[str]) -> int:
        count = 0
        for path in paths:
            if await self.invalidate(path):
                count += 1
        return count

    def clone(self) -> FileStateCache:
        new_cache = FileStateCache(max_entries=self._max_entries)
        new_cache._cache = OrderedDict(self._cache)
        return new_cache

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def contains(self, path: str) -> bool:
        abs_path = os.path.abspath(path)
        return abs_path in self._cache

    async def _read_file(self, abs_path: str) -> str | None:
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._sync_read, abs_path)
        except (OSError, IOError):
            return None

    @staticmethod
    def _sync_read(path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def get_sync(self, path: str) -> str | None:
        abs_path = os.path.abspath(path)
        if abs_path in self._cache:
            self._cache.move_to_end(abs_path)
            return self._cache[abs_path]
        return None

    def set_sync(self, path: str, content: str) -> None:
        abs_path = os.path.abspath(path)
        if abs_path in self._cache:
            self._cache.move_to_end(abs_path)
            self._cache[abs_path] = content
        else:
            self._cache[abs_path] = content
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
