"""Tests for x-client-request-id injection (Phase C)."""
from __future__ import annotations

import asyncio
import unittest

from src.services.api.claude import (
    CLIENT_REQUEST_ID_HEADER,
    CallModelOptions,
    _is_first_party_endpoint,
    call_model,
    make_client_request_id,
)


class _FakeStream:
    """Async iterator that produces a minimal sequence of SDK-shaped events.

    Used to drive ``call_model`` without an Anthropic SDK install. Each
    event is a SimpleNamespace-like object with the attributes ``call_model``
    reads via ``getattr``.
    """

    def __init__(self) -> None:
        self._events: list[object] = [
            type("E", (), {
                "type": "message_start",
                "message": type("M", (), {
                    "usage": type("U", (), {
                        "input_tokens": 1, "output_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    })(),
                    "model": "claude-haiku-4-5",
                })(),
            })(),
            type("E", (), {
                "type": "message_delta",
                "delta": type("D", (), {"stop_reason": "end_turn"})(),
                "usage": type("U", (), {"output_tokens": 5})(),
            })(),
            type("E", (), {"type": "message_stop"})(),
        ]
        self._i = 0

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> object:
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        return ev


class _RecordingMessages:
    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeStream()


class _FakeClient:
    def __init__(self, base_url: str = "") -> None:
        self.base_url = base_url
        self.messages = _RecordingMessages()


class TestIsFirstPartyEndpoint(unittest.TestCase):
    def test_default_base_url(self) -> None:
        client = _FakeClient(base_url="")
        self.assertTrue(_is_first_party_endpoint(client))

    def test_anthropic_base_url(self) -> None:
        client = _FakeClient(base_url="https://api.anthropic.com")
        self.assertTrue(_is_first_party_endpoint(client))

    def test_bedrock_base_url(self) -> None:
        client = _FakeClient(base_url="https://bedrock-runtime.us-east-1.amazonaws.com")
        self.assertFalse(_is_first_party_endpoint(client))

    def test_vertex_base_url(self) -> None:
        client = _FakeClient(base_url="https://us-east5-aiplatform.googleapis.com")
        self.assertFalse(_is_first_party_endpoint(client))

    def test_attribute_missing(self) -> None:
        class _NoBaseUrl:
            pass
        # No base_url attribute at all → conservatively first-party.
        self.assertTrue(_is_first_party_endpoint(_NoBaseUrl()))


class TestMakeClientRequestId(unittest.TestCase):
    def test_returns_unique_hex(self) -> None:
        a = make_client_request_id()
        b = make_client_request_id()
        self.assertNotEqual(a, b)
        # uuid4().hex is 32 chars
        self.assertEqual(len(a), 32)


class TestCallModelHeaders(unittest.TestCase):
    def test_first_party_client_gets_request_id(self) -> None:
        client = _FakeClient(base_url="")
        opts = CallModelOptions(model="claude-haiku-4-5")

        async def _drain() -> None:
            async for _ in call_model([], opts, client=client):
                pass

        asyncio.run(_drain())

        headers = (client.messages.last_kwargs or {}).get("extra_headers", {})
        self.assertIn(CLIENT_REQUEST_ID_HEADER, headers)
        self.assertEqual(len(headers[CLIENT_REQUEST_ID_HEADER]), 32)

    def test_third_party_client_no_request_id(self) -> None:
        client = _FakeClient(base_url="https://bedrock-runtime.us-east-1.amazonaws.com")
        opts = CallModelOptions(model="claude-haiku-4-5")

        async def _drain() -> None:
            async for _ in call_model([], opts, client=client):
                pass

        asyncio.run(_drain())

        headers = (client.messages.last_kwargs or {}).get("extra_headers", {})
        self.assertNotIn(CLIENT_REQUEST_ID_HEADER, headers)

    def test_caller_supplied_request_id_preserved(self) -> None:
        """A caller pre-setting the header (e.g. to thread one across a
        streaming + non-streaming-fallback pair) must override the auto-mint."""
        client = _FakeClient(base_url="")
        custom_id = "caller-provided-id"
        opts = CallModelOptions(
            model="claude-haiku-4-5",
            extra_headers={CLIENT_REQUEST_ID_HEADER: custom_id},
        )

        async def _drain() -> None:
            async for _ in call_model([], opts, client=client):
                pass

        asyncio.run(_drain())

        headers = (client.messages.last_kwargs or {}).get("extra_headers", {})
        self.assertEqual(headers[CLIENT_REQUEST_ID_HEADER], custom_id)

    def test_resolved_max_tokens_applied_in_request(self) -> None:
        client = _FakeClient(base_url="")
        opts = CallModelOptions(model="claude-opus-4-7")  # default cap = 8K

        async def _drain() -> None:
            async for _ in call_model([], opts, client=client):
                pass

        asyncio.run(_drain())

        self.assertEqual((client.messages.last_kwargs or {}).get("max_tokens"), 8_000)


if __name__ == "__main__":
    unittest.main()
