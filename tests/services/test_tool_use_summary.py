"""SERVICES-1 — the tool-use summary GENERATOR (faithful port).

Port of toolUseSummaryGenerator.ts. Query-loop wiring is deferred (no
in-repo consumer of the label; see the module docstring), so these tests
pin the generator's faithfulness in isolation — verbatim system prompt,
byte-exact truncation, exact user-prompt shape, small-model side query,
and the never-raises contract.
"""
from __future__ import annotations

import asyncio

import pytest

from src.services.tool_use_summary import (
    TOOL_USE_SUMMARY_SYSTEM_PROMPT,
    _truncate_json,
    generate_tool_use_summary,
)


class _Provider:
    """Captures the side-query args; returns a canned label."""

    def __init__(self, label: str = "Fixed NPE in UserService"):
        self.label = label
        self.calls: list = []

    async def chat_async(self, messages, **kwargs):
        self.calls.append((messages, kwargs))

        class _R:
            content = self.label

        return _R()


def _run(coro):
    return asyncio.run(coro)


class TestSystemPrompt:
    def test_verbatim(self):
        # Mechanically extracted from the TS template literal (444 chars).
        assert len(TOOL_USE_SUMMARY_SYSTEM_PROMPT) == 444
        assert "git-commit-subject, not\nsentence" not in TOOL_USE_SUMMARY_SYSTEM_PROMPT
        for needle in (
            "truncates around 30 characters",
            "Keep the verb in past tense",
            "- Searched in auth/",
            "- Fixed NPE in UserService",
            "- Ran failing tests",
        ):
            assert needle in TOOL_USE_SUMMARY_SYSTEM_PROMPT


class TestTruncateJson:
    def test_byte_exact_to_ts(self):
        # TS: slice(0, maxLen-3) + '...' (three ASCII dots); result len==maxLen.
        r = _truncate_json("x" * 400, 10)
        assert r.endswith("...") and len(r) == 10
        assert "…" not in r  # NOT the unicode ellipsis

    def test_small_and_none(self):
        assert _truncate_json({"a": 1}, 300) == '{"a":1}'  # JSON.stringify no-space form
        assert _truncate_json({"a": 1, "b": 2}, 300) == '{"a":1,"b":2}'
        assert _truncate_json(None, 300) == "null"

    def test_non_serializable_falls_back(self):
        class X:
            pass

        assert isinstance(_truncate_json(X(), 300), str)  # default=str, no raise


class TestGenerator:
    def test_empty_tools_returns_none(self):
        assert _run(generate_tool_use_summary([], _Provider())) is None

    def test_returns_stripped_label(self):
        p = _Provider("  Created signup endpoint  ")
        out = _run(generate_tool_use_summary(
            [{"name": "Write", "input": {"file_path": "x"}, "output": "ok"}], p,
        ))
        assert out == "Created signup endpoint"

    def test_user_prompt_shape(self):
        p = _Provider()
        _run(generate_tool_use_summary(
            [{"name": "Edit", "input": {"file_path": "a.py"}, "output": "done"}],
            p, last_assistant_text="fix the null deref",
        ))
        messages, kwargs = p.calls[-1]
        prompt = messages[0]["content"]
        # exact TS segments
        assert prompt.startswith(
            "User's intent (from assistant's last message): fix the null deref\n\n"
        )
        assert "Tools completed:\n\n" in prompt
        assert "Tool: Edit\nInput: " in prompt and "Output: " in prompt
        assert prompt.endswith("\n\nLabel:")
        assert kwargs["system"] == TOOL_USE_SUMMARY_SYSTEM_PROMPT

    def test_no_context_prefix_when_no_last_text(self):
        p = _Provider()
        _run(generate_tool_use_summary([{"name": "X", "input": 1, "output": 2}], p))
        prompt = p.calls[-1][0][0]["content"]
        assert prompt.startswith("Tools completed:\n\n")

    def test_never_raises_on_provider_error(self):
        class _Bad:
            async def chat_async(self, m, **k):
                raise RuntimeError("boom")

        assert _run(generate_tool_use_summary(
            [{"name": "X", "input": 1, "output": 2}], _Bad(),
        )) is None

    def test_none_provider_and_no_chat_async(self):
        assert _run(generate_tool_use_summary(
            [{"name": "X", "input": 1, "output": 2}], None,
        )) is None
        assert _run(generate_tool_use_summary(
            [{"name": "X", "input": 1, "output": 2}], object(),
        )) is None

    def test_empty_label_becomes_none(self):
        assert _run(generate_tool_use_summary(
            [{"name": "X", "input": 1, "output": 2}], _Provider("   "),
        )) is None

    def test_small_model_pin_reused(self, monkeypatch):
        # The generator reuses memdir's small-fast-model resolver.
        import src.services.tool_use_summary as mod

        monkeypatch.setattr(mod, "_resolve_summary_model", lambda p: "small-fast-x")
        p = _Provider()
        _run(generate_tool_use_summary([{"name": "X", "input": 1, "output": 2}], p))
        assert p.calls[-1][1]["model"] == "small-fast-x"
