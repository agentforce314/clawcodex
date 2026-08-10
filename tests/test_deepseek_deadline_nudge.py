"""DeepSeek deadline wrap-up nudge (query loop hook).

deepseek-v4-flash has no sense of the trial wall-clock and over-refines
compute/fit tasks (raman-fitting, largest-eigenval, dna-assembly) past the
point it already had a working artifact, then the 900 s agent timeout kills
it with nothing saved. When the harbor adapter passes the budget as
CLAWCODEX_DEADLINE_SEC, the loop fires ONE reminder at a fraction of it to
save-and-verify. Gated on the DeepSeek provider; inert without the env.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.providers.base import ChatResponse
from src.query.query import QueryParams, query
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.types.messages import UserMessage
from src.utils.abort_controller import AbortController


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=text, model="deepseek-v4-flash",
        usage={"input_tokens": 5, "output_tokens": 5},
        finish_reason="stop", tool_uses=None,
    )


class _DeepSeekish:
    is_deepseek = True

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen_messages = []
        self.model = "deepseek-v4-flash"

    def chat(self, messages, tools=None, **kwargs):
        # Record the user-message texts the model was asked with.
        self.seen_messages.append(messages)
        return self._responses.pop(0)

    def chat_stream_response(self, *a, **k):
        raise NotImplementedError


class TestDeadlineNudge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = build_default_registry()
        self.ctx = ToolContext(workspace_root=Path(self.tmp.name))
        self.abort = AbortController()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, provider, **envpatch):
        import os
        for k, v in envpatch.items():
            os.environ[k] = v
        try:
            params = QueryParams(
                messages=[UserMessage(content="do the task")],
                system_prompt="x", tools=self.registry.list_tools(),
                tool_registry=self.registry, tool_use_context=self.ctx,
                provider=provider, abort_controller=self.abort, max_turns=5,
            )

            async def go():
                async for _ in query(params):
                    pass
            asyncio.run(go())
        finally:
            for k in envpatch:
                os.environ.pop(k, None)

    @staticmethod
    def _msg_text(m) -> str:
        """Flatten a normalized API message's content to searchable text.

        Consecutive user messages MERGE in normalize_messages_for_api, so the
        nudge arrives as a second text block inside a list, not a bare string.
        """
        c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(
                b.get("text", "") for b in c if isinstance(b, dict)
            )
        return str(c)

    def _nudge_seen(self, provider) -> bool:
        for batch in provider.seen_messages:
            for m in batch:
                if "budget remain" in self._msg_text(m):
                    return True
        return False

    def test_nudge_fires_for_deepseek_past_threshold(self):
        # Deadline 1s, warn at 0.0 fraction → fires on the very first turn.
        p = _DeepSeekish([_text_response("working on it"), _text_response("done")])
        self._run(p, CLAWCODEX_DEADLINE_SEC="1", CLAWCODEX_DEADLINE_WARN_FRAC="0")
        self.assertTrue(self._nudge_seen(p), "deadline reminder should be injected")

    def test_nudge_absent_without_env(self):
        p = _DeepSeekish([_text_response("done")])
        self._run(p)
        self.assertFalse(self._nudge_seen(p))

    def test_nudge_absent_before_threshold(self):
        # Huge deadline, warn at 0.99 → never reached in a fast test.
        p = _DeepSeekish([_text_response("a"), _text_response("done")])
        self._run(p, CLAWCODEX_DEADLINE_SEC="100000", CLAWCODEX_DEADLINE_WARN_FRAC="0.99")
        self.assertFalse(self._nudge_seen(p))

    def test_nudge_fires_at_most_once(self):
        p = _DeepSeekish([_text_response("a"), _text_response("b"), _text_response("done")])
        self._run(p, CLAWCODEX_DEADLINE_SEC="1", CLAWCODEX_DEADLINE_WARN_FRAC="0")
        count = 0
        for batch in p.seen_messages:
            for m in batch:
                c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                if isinstance(c, str) and "budget remain" in c:
                    count += 1
        # The reminder is appended to history once; later turns still SEE it
        # (it's in the message list), so assert it was INJECTED once by
        # checking it appears in the first firing turn and isn't duplicated
        # across separate append operations: the first batch that contains it
        # should contain exactly one.
        first = next(
            b for b in p.seen_messages
            if any("budget remain" in self._msg_text(m) for m in b)
        )
        n_in_first = sum(1 for m in first if "budget remain" in self._msg_text(m))
        self.assertEqual(n_in_first, 1)

    def test_non_deepseek_never_nudged(self):
        class _Other:
            is_deepseek = False
            model = "x"
            def __init__(self): self.seen_messages = []
            def chat(self, messages, tools=None, **k):
                self.seen_messages.append(messages); return _text_response("done")
            def chat_stream_response(self, *a, **k): raise NotImplementedError
        p = _Other()
        self._run(p, CLAWCODEX_DEADLINE_SEC="1", CLAWCODEX_DEADLINE_WARN_FRAC="0")
        self.assertFalse(self._nudge_seen(p))


if __name__ == "__main__":
    unittest.main()
