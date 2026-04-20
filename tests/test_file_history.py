import os
import tempfile
import pytest

from src.utils.file_history import FileHistory, LinesChanged, _compute_lines_changed


class TestLinesChanged:
    def test_no_changes(self):
        lc = _compute_lines_changed("hello\n", "hello\n")
        assert lc.added == 0
        assert lc.removed == 0

    def test_added_lines(self):
        lc = _compute_lines_changed("a\n", "a\nb\nc\n")
        assert lc.added == 2
        assert lc.removed == 0

    def test_removed_lines(self):
        lc = _compute_lines_changed("a\nb\nc\n", "a\n")
        assert lc.added == 0
        assert lc.removed == 2

    def test_replaced_lines(self):
        lc = _compute_lines_changed("a\nb\n", "a\nc\n")
        assert lc.added == 1
        assert lc.removed == 1

    def test_total(self):
        lc = LinesChanged(added=5, removed=3)
        assert lc.total == 8

    def test_empty_to_content(self):
        lc = _compute_lines_changed("", "hello\nworld\n")
        assert lc.added == 2
        assert lc.removed == 0


class TestFileHistory:
    def test_empty_history(self):
        history = FileHistory()
        assert history.file_count == 0
        assert history.get_modified_files() == []

    def test_snapshot_file_with_content(self):
        history = FileHistory()
        snap = history.snapshot_file("/tmp/test.txt", "original")
        assert snap.content == "original"
        assert history.file_count == 1

    def test_snapshot_file_from_disk(self):
        history = FileHistory()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("disk content")
            f.flush()
            snap = history.snapshot_file(f.name)
        os.unlink(f.name)
        assert snap.content == "disk content"

    def test_undo_file_change(self):
        history = FileHistory()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("original")
            f.flush()
            history.snapshot_file(f.name, "original")
            with open(f.name, "w") as fw:
                fw.write("modified")
            restored = history.undo_file_change(f.name)
        content = open(f.name).read()
        os.unlink(f.name)
        assert restored == "original"
        assert content == "original"

    def test_undo_nonexistent(self):
        history = FileHistory()
        result = history.undo_file_change("/nonexistent.txt")
        assert result is None

    def test_multiple_snapshots(self):
        history = FileHistory()
        history.snapshot_file("/tmp/test.txt", "v1")
        history.snapshot_file("/tmp/test.txt", "v2")
        history.snapshot_file("/tmp/test.txt", "v3")
        assert history.get_snapshot_count("/tmp/test.txt") == 3

    def test_mark_generated(self):
        history = FileHistory()
        assert not history.is_generated("/tmp/new_file.txt")
        history.mark_generated("/tmp/new_file.txt")
        assert history.is_generated("/tmp/new_file.txt")

    def test_get_generated_files(self):
        history = FileHistory()
        history.mark_generated("/tmp/a.txt")
        history.mark_generated("/tmp/b.txt")
        generated = history.get_generated_files()
        assert len(generated) == 2

    def test_get_lines_changed(self):
        history = FileHistory()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\n")
            f.flush()
            history.snapshot_file(f.name, "line1\n")
            with open(f.name, "w") as fw:
                fw.write("line1\nline2\n")
            lc = history.get_lines_changed(f.name)
        os.unlink(f.name)
        assert lc.added == 1
        assert lc.removed == 0

    def test_checkpoint_create(self):
        history = FileHistory()
        name = history.create_checkpoint("before-refactor")
        assert name == "before-refactor"
        assert "before-refactor" in history.checkpoint_names

    def test_undo_to_checkpoint(self):
        history = FileHistory()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("original")
            f.flush()
            history.snapshot_file(f.name, "original")
            history.create_checkpoint("cp1")

            with open(f.name, "w") as fw:
                fw.write("modified after checkpoint")

            restored = history.undo_to_checkpoint("cp1")
        content = open(f.name).read()
        os.unlink(f.name)

        assert f.name in [os.path.abspath(p) for p in restored]
        assert content == "original"

    def test_undo_to_nonexistent_checkpoint(self):
        history = FileHistory()
        result = history.undo_to_checkpoint("nonexistent")
        assert result == []

    def test_clear(self):
        history = FileHistory()
        history.snapshot_file("/tmp/test.txt", "content")
        history.mark_generated("/tmp/test.txt")
        history.create_checkpoint("cp1")
        history.clear()
        assert history.file_count == 0
        assert history.get_generated_files() == []
        assert history.checkpoint_names == []

    def test_get_modified_files(self):
        history = FileHistory()
        history.snapshot_file("/tmp/a.txt", "a")
        history.snapshot_file("/tmp/b.txt", "b")
        files = history.get_modified_files()
        assert len(files) == 2

    def test_get_total_lines_changed(self):
        history = FileHistory()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("a\nb\nc\n")
            f.flush()
            history.snapshot_file(f.name, "a\n")
            total = history.get_total_lines_changed()
        os.unlink(f.name)
        assert total.added == 2
