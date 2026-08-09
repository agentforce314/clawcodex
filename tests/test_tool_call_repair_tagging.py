"""Producer tests for ``repaired_tool_indices`` (the amputation signal).

The DeepSeek amputation guard consumes this metadata; these tests pin the
PRODUCER — the tagged parser and the streaming assembler's index
arithmetic — so a regression there can't silently disable the guard.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.providers.openai_compatible import (
    OpenAICompatibleProvider,
    _parse_tool_call_arguments_tagged,
)


# --- tagged parser ----------------------------------------------------------


def test_complete_args_not_repaired():
    parsed, repaired = _parse_tool_call_arguments_tagged('{"command": "ls"}')
    assert parsed == {"command": "ls"}
    assert repaired is False


def test_empty_args_not_repaired():
    """A zero-argument tool call streams no argument bytes — that is a
    complete call, not a truncation."""
    for raw in (None, ""):
        parsed, repaired = _parse_tool_call_arguments_tagged(raw)
        assert parsed == {}
        assert repaired is False


def test_truncated_mid_string_repaired():
    parsed, repaired = _parse_tool_call_arguments_tagged(
        '{"file_path": "/app/x.py", "content": "def main():\\n    pri'
    )
    assert repaired is True
    assert parsed["file_path"] == "/app/x.py"
    assert "content" in parsed


def test_dangling_comma_repaired():
    parsed, repaired = _parse_tool_call_arguments_tagged('{"a": 1,')
    assert repaired is True
    assert parsed == {"a": 1}


def test_unrecoverable_garbage_repaired_to_empty():
    parsed, repaired = _parse_tool_call_arguments_tagged("not json at all{{{")
    assert repaired is True
    assert parsed == {}


# --- streaming assembler index arithmetic -----------------------------------


class _FakeStreamProvider(OpenAICompatibleProvider):
    """Bypass __init__ / network; only _stream_attempt's assembly runs."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.api_key = "k"
        self.base_url = "http://x"
        self.model = "m"
        self._client = None

    def _create_client(self):  # pragma: no cover — client property patched
        raise AssertionError("no network in this test")

    def get_available_models(self):  # pragma: no cover — abstract filler
        return ["m"]


def _delta_chunk(tool_calls=None, content=None, finish=None):
    delta = SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=None
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], model="m", usage=None)


def _tc(index, name=None, args=None, call_id=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


def test_streaming_assembler_tags_only_the_truncated_call(monkeypatch):
    chunks = [
        _delta_chunk(tool_calls=[_tc(0, name="Bash", call_id="a")]),
        _delta_chunk(tool_calls=[_tc(0, args='{"command": "ls"}')]),
        _delta_chunk(tool_calls=[_tc(1, name="Write", call_id="b")]),
        # Second call's args cut mid-string by the output cap.
        _delta_chunk(tool_calls=[_tc(1, args='{"file_path": "/x", "content": "abc')]),
        _delta_chunk(finish="length"),
    ]
    provider = _FakeStreamProvider(chunks)

    class _FakeStream:
        def __init__(self, items):
            self._items = items

        def __iter__(self):
            return iter(self._items)

        def close(self):
            pass

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: _FakeStream(chunks)
            )
        )
    )
    monkeypatch.setattr(
        _FakeStreamProvider, "client", property(lambda self: fake_client)
    )

    response = provider._stream_attempt([{"role": "user", "content": "hi"}])
    assert [t["name"] for t in response.tool_uses] == ["Bash", "Write"]
    assert response.repaired_tool_indices == [1]
    assert response.finish_reason == "length"


def test_streaming_assembler_skips_nameless_entries_in_index_math(monkeypatch):
    """A nameless (dropped) entry must not shift the repaired index."""
    chunks = [
        # Index 0 never gets a name -> skipped at assembly.
        _delta_chunk(tool_calls=[_tc(0, args='{"zzz": 1')]),
        _delta_chunk(tool_calls=[_tc(1, name="Bash", call_id="a")]),
        _delta_chunk(tool_calls=[_tc(1, args='{"command": "ls')]),
        _delta_chunk(finish="length"),
    ]
    provider = _FakeStreamProvider(chunks)

    class _FakeStream:
        def __init__(self, items):
            self._items = items

        def __iter__(self):
            return iter(self._items)

        def close(self):
            pass

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: _FakeStream(chunks)
            )
        )
    )
    monkeypatch.setattr(
        _FakeStreamProvider, "client", property(lambda self: fake_client)
    )

    response = provider._stream_attempt([{"role": "user", "content": "hi"}])
    assert [t["name"] for t in response.tool_uses] == ["Bash"]
    assert response.repaired_tool_indices == [0]
