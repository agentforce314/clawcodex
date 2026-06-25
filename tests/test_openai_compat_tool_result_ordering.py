"""Regression tests for Anthropic→OpenAI tool_result ORDERING in the
OpenAI-compatible provider converter.

OpenAI-compatible APIs require an assistant message carrying
``tool_calls`` to be followed IMMEDIATELY by the matching ``role=tool``
messages (one per ``tool_call_id``). When a single Anthropic user
message carries BOTH ``tool_result`` blocks AND plain text — which
``normalize_messages_for_api`` produces by merging a rejected/
interrupted tool turn with the user's next prompt — the converter must
emit the tool messages FIRST and the user text LAST. Emitting the text
first slips a ``role=user`` message between the ``tool_calls`` and the
tool responses, and the API rejects the request with:

    "An assistant message with 'tool_calls' must be followed by tool
     messages responding to each 'tool_call_id'. (insufficient tool
     messages following tool_calls message)"

This is the failure a user hits by rejecting a batch of tool calls
(e.g. 4 Reads) and then typing "please continue …". Mirrors TS
openaiShim.ts:546-567 (tool messages, THEN remaining user content).
"""

from __future__ import annotations

import unittest

from src.providers.openai_compatible import _convert_anthropic_messages_to_openai
from src.types.content_blocks import TextBlock, ToolUseBlock, ToolResultBlock
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    REJECT_MESSAGE,
    normalize_messages_for_api,
)


def _assert_tool_calls_immediately_followed(test: unittest.TestCase, out: list) -> None:
    """Every assistant message with ``tool_calls`` must be immediately
    followed by exactly one ``role=tool`` message per tool_call_id."""
    for idx, m in enumerate(out):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            following = out[idx + 1 : idx + 1 + n]
            test.assertEqual(
                [x.get("role") for x in following],
                ["tool"] * n,
                msg=(
                    f"assistant@{idx} with {n} tool_calls not immediately "
                    f"followed by {n} tool messages; got "
                    f"{[x.get('role') for x in out[idx + 1:]]}"
                ),
            )
            # Every tool_call must have a matching tool message among the
            # immediately-following N. OpenAI requires one response per
            # tool_call_id; their order within the run is not contractually
            # required, so compare as sets.
            test.assertEqual(
                {tc["id"] for tc in m["tool_calls"]},
                {x["tool_call_id"] for x in following},
            )


class TestToolResultOrdering(unittest.TestCase):
    def test_tool_messages_emitted_before_trailing_user_text(self) -> None:
        """User message carrying tool_result blocks AND trailing text →
        tool messages first, user text last."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Reading files."},
                {"type": "tool_use", "id": "call_a", "name": "Read", "input": {}},
                {"type": "tool_use", "id": "call_b", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_a", "content": "rejected"},
                {"type": "tool_result", "tool_use_id": "call_b", "content": "rejected"},
                {"type": "text", "text": "please continue"},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        roles = [m["role"] for m in out]
        self.assertEqual(roles, ["assistant", "tool", "tool", "user"])
        self.assertEqual(out[1]["tool_call_id"], "call_a")
        self.assertEqual(out[2]["tool_call_id"], "call_b")
        self.assertEqual(out[3]["content"][0]["text"], "please continue")
        _assert_tool_calls_immediately_followed(self, out)

    def test_reject_then_continue_end_to_end(self) -> None:
        """The real-world repro: a 4-tool turn is rejected, the user
        types a continuation prompt, and the merged history must convert
        to a VALID OpenAI payload.

        Runs the full prep pipeline (normalize_messages_for_api, which
        merges the rejected results with the next prompt) before the
        converter — this is the interaction that triggered the 400.
        """
        ids = ["call_a", "call_b", "call_c", "call_d"]
        conversation = [
            AssistantMessage(content=[
                TextBlock(text="Continuing where I left off."),
                *[ToolUseBlock(id=i, name="Read", input={"file_path": f"/x/{i}"}) for i in ids],
            ]),
            UserMessage(content=[
                ToolResultBlock(tool_use_id=i, content=REJECT_MESSAGE, is_error=True)
                for i in ids
            ]),
            UserMessage(content="please continue to build this blog and test it"),
        ]
        api_messages = normalize_messages_for_api(conversation)
        out = _convert_anthropic_messages_to_openai(api_messages)

        # assistant(4 tool_calls) → tool×4 → user(text)
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual([tc["id"] for tc in out[0]["tool_calls"]], ids)
        self.assertEqual([m["role"] for m in out[1:5]], ["tool"] * 4)
        self.assertEqual([m["tool_call_id"] for m in out[1:5]], ids)
        self.assertEqual(out[5]["role"], "user")
        _assert_tool_calls_immediately_followed(self, out)

    def test_pure_tool_result_user_message_emits_no_trailing_user(self) -> None:
        """A user message with ONLY tool_result blocks (the normal
        successful-tool turn) must not gain a spurious trailing user
        message."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_x", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_x", "content": "ok"},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual([m["role"] for m in out], ["assistant", "tool"])
        _assert_tool_calls_immediately_followed(self, out)

    def test_pure_text_user_message_unchanged(self) -> None:
        """A plain user message with no tool_result blocks is emitted as
        a single user message (no behavior change)."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")
        self.assertEqual(out[0]["content"][0]["text"], "hello")

    def test_multimodal_split_user_stays_adjacent_and_sibling_text_goes_last(self) -> None:
        """A tool_result carrying multimodal content splits into
        tool(text) + synthetic user(image); a SIBLING text block on the
        same user message must still be emitted LAST (after the split),
        never between the assistant tool_calls and the tool message."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_m", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_m", "content": [
                    {"type": "text", "text": "see image"},
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": "I"}},
                ]},
                {"type": "text", "text": "and please continue"},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        # assistant, tool (immediately), synthetic user (multimodal), user (sibling text)
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(out[1]["tool_call_id"], "call_m")
        # The split synthetic user (image) comes right after its tool msg.
        self.assertEqual(out[2]["role"], "user")
        self.assertIn("image_url", [b.get("type") for b in out[2]["content"]])
        # The sibling text is LAST.
        self.assertEqual(out[-1]["role"], "user")
        self.assertEqual(out[-1]["content"][0]["text"], "and please continue")
        _assert_tool_calls_immediately_followed(self, out)

    def test_multimodal_tool_result_batched_with_text_tool_result(self) -> None:
        """Parallel tool calls where ONE result is multimodal (image) and
        another is plain text. The two ``role=tool`` messages must stay
        contiguous — the image's synthetic ``role=user`` is deferred to
        AFTER both tool messages — so the second tool_call is never
        detached from its response (the pre-fix inline emission produced
        ``[assistant, tool, user, tool]`` and 400'd)."""
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_img", "name": "Read", "input": {}},
                {"type": "tool_use", "id": "call_txt", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_img", "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": "I"}},
                ]},
                {"type": "tool_result", "tool_use_id": "call_txt", "content": "plain text result"},
            ]},
        ]
        out = _convert_anthropic_messages_to_openai(messages)
        # assistant, then BOTH tool messages contiguous, THEN the deferred
        # synthetic user message carrying the image.
        self.assertEqual(out[0]["role"], "assistant")
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(out[2]["role"], "tool")
        self.assertEqual(
            {out[1]["tool_call_id"], out[2]["tool_call_id"]},
            {"call_img", "call_txt"},
        )
        self.assertEqual(out[-1]["role"], "user")
        self.assertIn("image_url", [b.get("type") for b in out[-1]["content"]])
        _assert_tool_calls_immediately_followed(self, out)


if __name__ == "__main__":
    unittest.main()
