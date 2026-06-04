"""Defensive round-trip test for `_build_chat_response` advisor preservation.

The Anthropic Python SDK 0.88.0 parses unknown content-block discriminators
via `construct_type` (lenient), which materializes the block as the first
matching union variant (typically `ParsedTextBlock` for an
`advisor_tool_result`) but preserves the original fields (`type`,
`tool_use_id`, `content`) as attributes accessible via `model_dump()`.

If a future SDK upgrade tightens that path — Pydantic v3, a stricter
discriminator handler, an `extra="ignore"` config — the advisor pair would
silently lose its content and the next turn would fail with API 400s on
replay. This test pins the round-trip contract so a regression in the SDK
or a refactor of `_build_chat_response` is caught locally.

Covers all three discriminated shapes of advisor_tool_result content:
  - advisor_result (text)
  - advisor_redacted_result (encrypted_content)
  - advisor_tool_result_error (error_code)
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from anthropic._models import construct_type
from anthropic.types.beta import BetaRawMessageStreamEvent
from anthropic.lib.streaming._messages import accumulate_event

from src.providers.anthropic_provider import AnthropicProvider


def _build_sdk_message_with_blocks(blocks: list[dict]) -> object:
    """Materialize a real SDK ParsedMessage via the same accumulate path
    the streaming provider uses, so the round-trip mirrors production."""
    events_raw = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_x",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-opus-4-6",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        }
    ]
    for idx, block in enumerate(blocks):
        events_raw.append(
            {"type": "content_block_start", "index": idx, "content_block": block}
        )
        events_raw.append({"type": "content_block_stop", "index": idx})
    events_raw.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        }
    )
    events_raw.append({"type": "message_stop"})

    snapshot = None
    for raw in events_raw:
        ev = construct_type(type_=BetaRawMessageStreamEvent, value=raw)
        snapshot = accumulate_event(event=ev, current_snapshot=snapshot)
    return snapshot


class TestAdvisorBlockPreservation(unittest.TestCase):
    """Build a fake assistant response, push it through the SDK's
    ``accumulate_event`` machinery, then through
    ``AnthropicProvider._build_chat_response``, and verify the
    ``raw_content_blocks`` list contains the advisor blocks with all
    their original fields intact.
    """

    def _provider(self) -> AnthropicProvider:
        # __init__ stores kwargs lazily; we don't need a real client.
        p = AnthropicProvider(api_key="fake", model="claude-opus-4-6")
        return p

    def test_advisor_result_content_preserved(self) -> None:
        blocks = [
            {"type": "text", "text": "ok"},
            {
                "type": "server_tool_use",
                "id": "srv_1",
                "name": "advisor",
                "input": {},
            },
            {
                "type": "advisor_tool_result",
                "tool_use_id": "srv_1",
                "content": {"type": "advisor_result", "text": "looks good"},
            },
        ]
        snapshot = _build_sdk_message_with_blocks(blocks)
        chat = self._provider()._build_chat_response(snapshot)
        self.assertIsNotNone(chat.raw_content_blocks)
        types = [b.get("type") for b in chat.raw_content_blocks]
        self.assertEqual(
            types,
            ["server_tool_use", "advisor_tool_result"],
            "advisor pair must be preserved in raw_content_blocks",
        )
        # The server_tool_use block keeps its name + id.
        use = next(b for b in chat.raw_content_blocks if b["type"] == "server_tool_use")
        self.assertEqual(use.get("name"), "advisor")
        self.assertEqual(use.get("id"), "srv_1")
        # The result block keeps its tool_use_id and full content dict.
        result = next(b for b in chat.raw_content_blocks if b["type"] == "advisor_tool_result")
        self.assertEqual(result.get("tool_use_id"), "srv_1")
        self.assertEqual(
            result.get("content"),
            {"type": "advisor_result", "text": "looks good"},
        )

    def test_advisor_redacted_result_content_preserved(self) -> None:
        blocks = [
            {
                "type": "server_tool_use",
                "id": "srv_r",
                "name": "advisor",
                "input": {},
            },
            {
                "type": "advisor_tool_result",
                "tool_use_id": "srv_r",
                "content": {
                    "type": "advisor_redacted_result",
                    "encrypted_content": "AAAA==",
                },
            },
        ]
        snapshot = _build_sdk_message_with_blocks(blocks)
        chat = self._provider()._build_chat_response(snapshot)
        result = next(
            b for b in (chat.raw_content_blocks or [])
            if b.get("type") == "advisor_tool_result"
        )
        # The opaque envelope MUST round-trip exactly — redacted
        # results are the whole reason advisor exists for sensitive
        # workloads; dropping encrypted_content would silently
        # downgrade history fidelity.
        self.assertEqual(
            result.get("content"),
            {"type": "advisor_redacted_result", "encrypted_content": "AAAA=="},
        )

    def test_advisor_tool_result_error_content_preserved(self) -> None:
        blocks = [
            {
                "type": "server_tool_use",
                "id": "srv_e",
                "name": "advisor",
                "input": {},
            },
            {
                "type": "advisor_tool_result",
                "tool_use_id": "srv_e",
                "content": {
                    "type": "advisor_tool_result_error",
                    "error_code": "rate_limit",
                },
            },
        ]
        snapshot = _build_sdk_message_with_blocks(blocks)
        chat = self._provider()._build_chat_response(snapshot)
        result = next(
            b for b in (chat.raw_content_blocks or [])
            if b.get("type") == "advisor_tool_result"
        )
        # The error_code is the only signal the UI uses to render
        # "Advisor unavailable (<code>)" — losing it would silently
        # turn errors into "Advisor reviewed" successes.
        self.assertEqual(
            result.get("content"),
            {"type": "advisor_tool_result_error", "error_code": "rate_limit"},
        )

    def test_advisor_use_alone_preserved_when_no_result(self) -> None:
        # Interrupted advisor: use without matching result. We MUST
        # preserve the use block so the orphan-strip pass in
        # ensure_tool_result_pairing can find it and drop it on the
        # next API replay.
        blocks = [
            {
                "type": "server_tool_use",
                "id": "srv_orphan",
                "name": "advisor",
                "input": {},
            },
        ]
        snapshot = _build_sdk_message_with_blocks(blocks)
        chat = self._provider()._build_chat_response(snapshot)
        self.assertEqual(len(chat.raw_content_blocks or []), 1)
        use = chat.raw_content_blocks[0]
        self.assertEqual(use["type"], "server_tool_use")
        self.assertEqual(use["name"], "advisor")
        self.assertEqual(use["id"], "srv_orphan")

    def test_non_advisor_server_tools_not_in_raw_content_blocks(self) -> None:
        # Only ADVISOR server-tool blocks should land in
        # raw_content_blocks. Other server tools (web_search,
        # code_execution, ...) have their own handling and would
        # leak into history wrongly if scooped up here.
        blocks = [
            {
                "type": "server_tool_use",
                "id": "srv_ws",
                "name": "web_search",
                "input": {"query": "anthropic news"},
            },
        ]
        snapshot = _build_sdk_message_with_blocks(blocks)
        chat = self._provider()._build_chat_response(snapshot)
        # Either None or empty — both signal "no advisor blocks".
        self.assertFalse(chat.raw_content_blocks)

    def test_text_and_tool_use_still_projected_normally(self) -> None:
        # The advisor-preservation pass MUST NOT regress the existing
        # text/tool_use projection. Mixed block stream:
        blocks = [
            {"type": "text", "text": "hello"},
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "Bash",
                "input": {"command": "ls"},
            },
            {
                "type": "server_tool_use",
                "id": "srv_a",
                "name": "advisor",
                "input": {},
            },
            {
                "type": "advisor_tool_result",
                "tool_use_id": "srv_a",
                "content": {"type": "advisor_result", "text": "ok"},
            },
            {"type": "text", "text": " world"},
        ]
        snapshot = _build_sdk_message_with_blocks(blocks)
        chat = self._provider()._build_chat_response(snapshot)
        # Text concatenated from text blocks (existing behavior).
        self.assertEqual(chat.content, "hello world")
        # tool_use surfaced.
        self.assertEqual(len(chat.tool_uses or []), 1)
        self.assertEqual(chat.tool_uses[0]["id"], "tu_1")
        # Advisor pair preserved.
        self.assertEqual(len(chat.raw_content_blocks or []), 2)


if __name__ == "__main__":
    unittest.main()
