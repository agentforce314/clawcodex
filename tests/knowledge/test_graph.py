"""Knowledge graph store + heuristic extraction tests."""

from src.knowledge import KnowledgeGraph


def test_record_extracts_files_symbols_urls():
    g = KnowledgeGraph()
    g.record_from_text("Edited src/app.py and `build_registry`; see https://example.com/docs", now=1.0)
    stats = g.stats()
    assert stats["file"] == 1
    assert stats["symbol"] == 1
    assert stats["url"] == 1
    assert stats["total"] == 3


def test_count_and_top():
    g = KnowledgeGraph()
    g.record_from_text("a.py a.py b.py", now=1.0)  # a.py twice, b.py once
    top = g.top()
    assert top[0].name == "a.py" and top[0].count == 2
    assert any(e.name == "b.py" for e in top)


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "graph.json"
    g = KnowledgeGraph()
    g.record_from_text("module main.ts has `Foo`", now=2.0)
    g.save(p)
    g2 = KnowledgeGraph.load(p)
    assert g2.stats()["total"] == g.stats()["total"]
    assert any(e.name == "main.ts" for e in g2.entities.values())


def test_clear():
    g = KnowledgeGraph()
    g.record_from_text("x.py", now=1.0)
    g.clear()
    assert g.stats()["total"] == 0


def test_load_missing_is_empty(tmp_path):
    assert KnowledgeGraph.load(tmp_path / "nope.json").stats()["total"] == 0
