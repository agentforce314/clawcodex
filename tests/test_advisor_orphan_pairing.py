"""Tests for the orphan ``server_tool_use`` strip pass added to
``ensure_tool_result_pairing`` in ``src/types/messages.py``.

The advisor is a server tool: both the ``server_tool_use`` (name=advisor)
and the matching ``advisor_tool_result`` blocks land in the SAME
assistant message when the round-trip succeeds. If the stream is
interrupted before the result arrives, the use block is left dangling
and the API rejects subsequent turns with::

    400: advisor tool use without corresponding advisor_tool_result

The strip pass mirrors TS ``messages.ts:5247-5255``: drop orphan
``server_tool_use`` / ``mcp_tool_use`` blocks whose id has no matching
``*_tool_result`` block in the same assistant message.
"""

from __future__ import annotations

import unittest

from src.types.messages import ensure_tool_result_pairing


class TestOrphanServerToolUseStripping(unittest.TestCase):
    def test_orphan_advisor_use_stripped(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking…"},
                    {
                        "type": "server_tool_use",
                        "id": "srv_orphan",
                        "name": "advisor",
                        "input": {},
                    },
                ],
            },
        ]
        out = ensure_tool_result_pairing(msgs)
        types = [b["type"] for b in out[0]["content"]]
        self.assertNotIn("server_tool_use", types)
        self.assertIn("text", types)

    def test_paired_advisor_use_and_result_retained(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srv_paired",
                        "name": "advisor",
                        "input": {},
                    },
                    {
                        "type": "advisor_tool_result",
                        "tool_use_id": "srv_paired",
                        "content": {"type": "advisor_result", "text": "ok"},
                    },
                    {"type": "text", "text": "done"},
                ],
            },
        ]
        out = ensure_tool_result_pairing(msgs)
        types = [b["type"] for b in out[0]["content"]]
        self.assertEqual(
            types, ["server_tool_use", "advisor_tool_result", "text"]
        )

    def test_orphan_mcp_use_stripped(self) -> None:
        # TS strips mcp_tool_use orphans by the same rule — port both
        # for symmetry. Mirrors the rationale at messages.ts:5247.
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "mcp_tool_use",
                        "id": "mcp_orphan",
                        "name": "search",
                        "input": {},
                    },
                    {"type": "text", "text": "tail"},
                ],
            },
        ]
        out = ensure_tool_result_pairing(msgs)
        types = [b["type"] for b in out[0]["content"]]
        self.assertNotIn("mcp_tool_use", types)
        self.assertEqual(types, ["text"])

    def test_empty_after_orphan_strip_gets_placeholder(self) -> None:
        # The TS placeholder text is ``[Tool use interrupted]`` (TS
        # messages.ts:5265); the Python port matches at messages.py:414.
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srv_orphan",
                        "name": "advisor",
                        "input": {},
                    },
                ],
            },
        ]
        out = ensure_tool_result_pairing(msgs)
        self.assertEqual(
            out[0]["content"], [{"type": "text", "text": "[Tool use interrupted]"}]
        )

    def test_normal_tool_use_unaffected_by_orphan_pass(self) -> None:
        # Regular client-side tool_use orphans are handled by the
        # forward-pairing pass that inserts a synthetic tool_result on
        # the next user message — NOT by the orphan-server-tool strip.
        # The two behaviors must coexist; verify a regular tool_use
        # passes through this pass unchanged.
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_x",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_x",
                        "content": "drwxr-xr-x  .",
                    },
                ],
            },
        ]
        out = ensure_tool_result_pairing(msgs)
        # The tool_use survives because it has a matching tool_result.
        self.assertEqual(out[0]["content"][0]["type"], "tool_use")
        self.assertEqual(out[1]["content"][0]["type"], "tool_result")

    def test_multiple_assistant_messages_strip_independently(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [
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
                ],
            },
            {"role": "user", "content": "next"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srv_b",
                        "name": "advisor",
                        "input": {},
                    },
                ],
            },
        ]
        out = ensure_tool_result_pairing(msgs)
        # Message 0: pair retained.
        m0_types = [b["type"] for b in out[0]["content"]]
        self.assertEqual(m0_types, ["server_tool_use", "advisor_tool_result"])
        # Message 2: orphan stripped, placeholder inserted.
        m2_types = [b["type"] for b in out[-1]["content"]]
        self.assertEqual(m2_types, ["text"])
        self.assertEqual(out[-1]["content"][0]["text"], "[Tool use interrupted]")


if __name__ == "__main__":
    unittest.main()
