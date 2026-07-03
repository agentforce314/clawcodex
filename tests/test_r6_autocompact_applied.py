"""R6-4 — the compression pipeline must APPLY the auto-compaction.

Found during the R6-3 long-task eval: run_compression_pipeline layer-5
(autocompact) computed the CompactionResult (an LLM summarization call — cost
incurred) but returned messages=current_messages (the UNCOMPACTED original) and
query() used that, so auto-compact never actually shrank the context — it
re-summarized every turn without effect. The fix assembles the compacted
conversation (summary_messages + messages_to_keep + attachments) into the
pipeline's returned messages.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from src.services.compact import pipeline as pipeline_mod
from src.services.compact.compact import CompactionResult
from src.services.compact.pipeline import PipelineConfig, run_compression_pipeline


def _msgs(n):
    return [{"type": "user" if i % 2 == 0 else "assistant",
             "content": f"message {i} " + "word " * 50} for i in range(n)]


class TestAutocompactApplied(unittest.TestCase):
    def _run(self, *, fires):
        # A big conversation; mock auto_compact_if_needed so no real LLM call.
        original = _msgs(120)
        summary = [{"type": "user", "content": "SUMMARY of the older messages."}]
        keep = [{"type": "user", "content": "recent kept message"}]
        attach = [{"type": "user", "content": "<file-state> foo.py …"}]

        async def _fake_autocompact(messages, input_tokens, *a, **k):
            if not fires:
                return None
            return CompactionResult(
                boundary_marker={"type": "system", "content": "[boundary]"},
                summary_messages=summary,
                messages_to_keep=keep,
                attachments=attach,
                tokens_saved=9000,
            )

        cfg = PipelineConfig(provider=object(), model="m")  # provider+model set
        # Only mock layer 5's decision. Layers 1-4 fail-soft / no-op on these
        # plain-dict messages (the pipeline catches each layer's exception),
        # and layer 5 replaces the working set wholesale, so the assertion on
        # res.messages is deterministic regardless.
        with patch.object(pipeline_mod, "auto_compact_if_needed", _fake_autocompact):
            res = asyncio.run(run_compression_pipeline(
                original, input_token_count=40000, config=cfg))
        return original, summary, keep, attach, res

    def test_pipeline_returns_the_compacted_conversation(self):
        original, summary, keep, attach, res = self._run(fires=True)
        self.assertIn("autocompact", res.layers_applied)
        # The returned messages are the ASSEMBLED compacted set, NOT the 120
        # originals — this is the whole bug fix.
        self.assertEqual(res.messages, summary + keep + attach)
        self.assertLess(len(res.messages), len(original))
        # File-state attachments are preserved (a coding agent needs them).
        self.assertIn(attach[0], res.messages)
        # The system boundary marker is NOT in the working set (API-clean).
        self.assertFalse(any(
            isinstance(m, dict) and m.get("type") == "system" for m in res.messages))

    def test_no_compaction_leaves_messages_unchanged(self):
        original, *_ , res = self._run(fires=False)
        self.assertNotIn("autocompact", res.layers_applied)
        self.assertEqual(len(res.messages), len(original))  # untouched


if __name__ == "__main__":
    unittest.main()
