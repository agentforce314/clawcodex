import asyncio
import os
import tempfile
import pytest

from src.utils.file_state_cache import FileStateCache


@pytest.fixture
def cache():
    return FileStateCache(max_entries=5)


class TestFileStateCache:
    @pytest.mark.asyncio
    async def test_empty_cache(self, cache):
        assert cache.size == 0
        result = await cache.get("/nonexistent/file.txt", read_through=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache):
        await cache.set("/tmp/test.txt", "hello world")
        result = await cache.get("/tmp/test.txt", read_through=False)
        assert result == "hello world"
        assert cache.size == 1

    @pytest.mark.asyncio
    async def test_invalidate(self, cache):
        await cache.set("/tmp/test.txt", "hello")
        assert cache.contains("/tmp/test.txt")
        removed = await cache.invalidate("/tmp/test.txt")
        assert removed is True
        assert not cache.contains("/tmp/test.txt")

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent(self, cache):
        removed = await cache.invalidate("/nonexistent.txt")
        assert removed is False

    @pytest.mark.asyncio
    async def test_invalidate_many(self, cache):
        await cache.set("/tmp/a.txt", "a")
        await cache.set("/tmp/b.txt", "b")
        await cache.set("/tmp/c.txt", "c")
        count = await cache.invalidate_many(["/tmp/a.txt", "/tmp/c.txt", "/tmp/d.txt"])
        assert count == 2
        assert cache.size == 1

    @pytest.mark.asyncio
    async def test_lru_eviction(self, cache):
        for i in range(7):
            await cache.set(f"/tmp/file{i}.txt", f"content{i}")

        assert cache.size == 5
        result = await cache.get("/tmp/file0.txt", read_through=False)
        assert result is None
        result = await cache.get("/tmp/file1.txt", read_through=False)
        assert result is None
        result = await cache.get("/tmp/file6.txt", read_through=False)
        assert result == "content6"

    @pytest.mark.asyncio
    async def test_lru_access_updates_order(self, cache):
        for i in range(5):
            await cache.set(f"/tmp/file{i}.txt", f"content{i}")

        await cache.get("/tmp/file0.txt", read_through=False)
        await cache.set("/tmp/file5.txt", "content5")

        assert cache.contains("/tmp/file0.txt")
        assert not cache.contains("/tmp/file1.txt")

    @pytest.mark.asyncio
    async def test_read_through(self):
        cache = FileStateCache()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("real file content")
            f.flush()
            result = await cache.get(f.name, read_through=True)
        os.unlink(f.name)

        assert result == "real file content"
        assert cache.size == 1

    @pytest.mark.asyncio
    async def test_read_through_nonexistent(self):
        cache = FileStateCache()
        result = await cache.get("/nonexistent/file.txt", read_through=True)
        assert result is None

    @pytest.mark.asyncio
    async def test_clone(self, cache):
        await cache.set("/tmp/a.txt", "a")
        await cache.set("/tmp/b.txt", "b")

        cloned = cache.clone()
        assert cloned.size == 2
        result = cloned.get_sync("/tmp/a.txt")
        assert result == "a"

        await cache.set("/tmp/c.txt", "c")
        assert cache.size == 3
        assert cloned.size == 2

    @pytest.mark.asyncio
    async def test_clear(self, cache):
        await cache.set("/tmp/a.txt", "a")
        await cache.set("/tmp/b.txt", "b")
        assert cache.size == 2
        await cache.clear()
        assert cache.size == 0

    def test_get_sync(self, cache):
        cache.set_sync("/tmp/test.txt", "sync content")
        result = cache.get_sync("/tmp/test.txt")
        assert result == "sync content"

    def test_set_sync_eviction(self):
        cache = FileStateCache(max_entries=2)
        cache.set_sync("/tmp/a.txt", "a")
        cache.set_sync("/tmp/b.txt", "b")
        cache.set_sync("/tmp/c.txt", "c")
        assert cache.size == 2
        assert cache.get_sync("/tmp/a.txt") is None

    def test_contains(self, cache):
        assert not cache.contains("/tmp/test.txt")
        cache.set_sync("/tmp/test.txt", "content")
        assert cache.contains("/tmp/test.txt")

    def test_max_entries(self, cache):
        assert cache.max_entries == 5

    @pytest.mark.asyncio
    async def test_update_existing(self, cache):
        await cache.set("/tmp/test.txt", "v1")
        await cache.set("/tmp/test.txt", "v2")
        result = await cache.get("/tmp/test.txt", read_through=False)
        assert result == "v2"
        assert cache.size == 1
