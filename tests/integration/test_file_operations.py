import asyncio

import pytest

from src.utils.file_state_cache import FileStateCache
from src.utils.file_history import FileHistory


class TestFileOperationsIntegration:
    def test_cache_and_history_together(self, tmp_path):
        cache = FileStateCache(max_entries=100)
        history = FileHistory()

        test_file = tmp_path / "test.py"
        test_file.write_text("original content")

        async def _run():
            content = await cache.get(str(test_file))
            assert content == "original content"

            history.snapshot_file(str(test_file), content)

            new_content = "modified content"
            test_file.write_text(new_content)
            await cache.set(str(test_file), new_content)

            cached = await cache.get(str(test_file))
            assert cached == "modified content"

            restored = history.undo_file_change(str(test_file))
            assert restored == "original content"

            await cache.set(str(test_file), restored)
            cached = await cache.get(str(test_file))
            assert cached == "original content"

        asyncio.run(_run())

    def test_checkpoint_workflow(self, tmp_path):
        history = FileHistory()

        file_a = str(tmp_path / "a.py")
        file_b = str(tmp_path / "b.py")

        history.snapshot_file(file_a, "a-v1")
        history.snapshot_file(file_b, "b-v1")
        cp = history.create_checkpoint("initial")

        history.snapshot_file(file_a, "a-v2")
        history.snapshot_file(file_b, "b-v2")

        restored = history.undo_to_checkpoint(cp)
        assert file_a in restored
        assert file_b in restored

    def test_cache_clone_isolation(self, tmp_path):
        cache = FileStateCache(max_entries=100)

        async def _run():
            await cache.set("file1", "content1")
            clone = cache.clone()

            await cache.set("file1", "modified")
            original = await clone.get("file1", read_through=False)
            assert original == "content1"

        asyncio.run(_run())

    def test_generated_file_tracking(self):
        history = FileHistory()

        history.mark_generated("new_file.py")
        assert history.is_generated("new_file.py")
        assert not history.is_generated("existing_file.py")

    def test_lines_changed(self, tmp_path):
        history = FileHistory()
        test_file = str(tmp_path / "test.py")

        (tmp_path / "test.py").write_text("line1\nline2\nline3\n")
        history.snapshot_file(test_file, "line1\nline2\nline3\n")

        (tmp_path / "test.py").write_text("line1\nline2_mod\nline3\nline4\n")

        lc = history.get_lines_changed(test_file)
        assert lc.added > 0 or lc.removed > 0
