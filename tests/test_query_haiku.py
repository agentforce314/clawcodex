"""Tests for the ``query_haiku`` fast-path entry (Phase F)."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from src.services.api.claude import (
    CAPPED_DEFAULT_MAX_TOKENS,
    CLIENT_REQUEST_ID_HEADER,
    SMALL_FAST_MODEL,
    query_haiku,
)


class _RecordingMessages:
    def __init__(self, response) -> None:
        self.response = response
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self.response


class _FakeClient:
    def __init__(self, response, base_url: str = "") -> None:
        self.base_url = base_url
        self.messages = _RecordingMessages(response)


class TestQueryHaiku(unittest.IsolatedAsyncioTestCase):
    async def test_uses_haiku_model_by_default(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        result = await query_haiku(user_prompt="hi", client=client)

        self.assertIs(result, response)
        self.assertEqual(client.messages.last_kwargs["model"], SMALL_FAST_MODEL)

    async def test_omits_stream_flag(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        await query_haiku(user_prompt="hi", client=client)

        # query_haiku is the non-streaming fast path — must not request
        # stream=True (which would change the SDK return type).
        self.assertNotIn("stream", client.messages.last_kwargs)

    async def test_max_tokens_capped_by_helper(self) -> None:
        """Default cap for Haiku is 8K (matches its native default)."""
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        await query_haiku(user_prompt="hi", client=client)

        self.assertEqual(
            client.messages.last_kwargs["max_tokens"],
            CAPPED_DEFAULT_MAX_TOKENS,
        )

    async def test_explicit_max_tokens_honoured(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        await query_haiku(user_prompt="hi", client=client, max_tokens=512)

        self.assertEqual(client.messages.last_kwargs["max_tokens"], 512)

    async def test_system_prompt_attached_as_block(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        await query_haiku(
            user_prompt="classify this", system_prompt="You are a classifier.",
            client=client,
        )

        system = client.messages.last_kwargs.get("system")
        self.assertEqual(system, [{"type": "text", "text": "You are a classifier."}])

    async def test_no_system_block_when_empty(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        await query_haiku(user_prompt="hi", client=client)

        self.assertNotIn("system", client.messages.last_kwargs)

    async def test_first_party_client_gets_request_id(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response, base_url="")

        await query_haiku(user_prompt="hi", client=client)

        headers = client.messages.last_kwargs.get("extra_headers", {})
        self.assertIn(CLIENT_REQUEST_ID_HEADER, headers)

    async def test_third_party_client_no_request_id(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response, base_url="https://bedrock.us-east-1.amazonaws.com")

        await query_haiku(user_prompt="hi", client=client)

        headers = client.messages.last_kwargs.get("extra_headers", {})
        self.assertNotIn(CLIENT_REQUEST_ID_HEADER, headers)

    async def test_structured_output_passed_through(self) -> None:
        response = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        client = _FakeClient(response)

        schema = {"type": "json_schema", "json_schema": {"name": "X"}}
        await query_haiku(
            user_prompt="hi", structured_output=schema, client=client,
        )

        oc = client.messages.last_kwargs.get("output_config")
        self.assertEqual(oc, {"format": schema})


if __name__ == "__main__":
    unittest.main()
