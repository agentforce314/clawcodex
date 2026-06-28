"""Semantic entity extraction tests (mock provider)."""

from src.knowledge import extract_entities_semantic


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _Provider:
    def __init__(self, content: str) -> None:
        self._content = content

    def chat(self, messages, tools=None, **kw):  # noqa: ANN001
        return _Resp(self._content)


def test_parses_json_array():
    p = _Provider('[{"name":"app.py","type":"file"},{"name":"Foo","type":"symbol"}]')
    ents = extract_entities_semantic("blah", p)
    assert ("app.py", "file") in ents
    assert ("Foo", "symbol") in ents


def test_handles_fences_and_prose():
    p = _Provider('Sure:\n```json\n[{"name":"Caching","type":"concept"}]\n```')
    assert ("Caching", "concept") in extract_entities_semantic("t", p)


def test_unknown_type_defaults_concept():
    p = _Provider('[{"name":"thing","type":"weird"}]')
    assert ("thing", "concept") in extract_entities_semantic("t", p)


def test_bad_json_returns_empty():
    assert extract_entities_semantic("t", _Provider("not json")) == []


def test_none_provider():
    assert extract_entities_semantic("t", None) == []
