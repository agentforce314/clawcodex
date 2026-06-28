"""Project wiki init/status/ingest tests."""

from src.wiki import get_wiki_paths, ingest_source, init_wiki, wiki_status


def test_init_creates_structure(tmp_path):
    res = init_wiki(tmp_path)
    assert res["already_existed"] is False
    assert len(res["created_files"]) >= 4  # schema, index, log, architecture
    paths = get_wiki_paths(tmp_path)
    assert paths.index_file.exists()
    assert paths.pages_dir.is_dir() and paths.sources_dir.is_dir()
    assert (paths.pages_dir / "architecture.md").exists()


def test_init_idempotent(tmp_path):
    init_wiki(tmp_path)
    res2 = init_wiki(tmp_path)
    assert res2["already_existed"] is True
    assert res2["created_files"] == []


def test_status_before_and_after(tmp_path):
    assert wiki_status(tmp_path)["initialized"] is False
    init_wiki(tmp_path)
    st = wiki_status(tmp_path)
    assert st["initialized"] is True
    assert st["page_count"] == 1  # architecture.md


def test_ingest_copies_into_sources(tmp_path):
    (tmp_path / "README.md").write_text("# Readme", encoding="utf-8")
    assert ingest_source(tmp_path, "README.md")["ok"] is False  # not initialized yet
    init_wiki(tmp_path)
    res = ingest_source(tmp_path, "README.md")
    assert res["ok"] is True
    assert (get_wiki_paths(tmp_path).sources_dir / "README.md").exists()
    assert wiki_status(tmp_path)["source_count"] == 1


def test_ingest_missing_file(tmp_path):
    init_wiki(tmp_path)
    assert ingest_source(tmp_path, "nope.md")["ok"] is False
