"""DeepSeek reasoning-runaway protection (the "fuse").

Failure shape, measured on terminal-bench 2.1 (tb21-flash-visiontool,
2026-08-08): ``deepseek-v4-flash`` at ``reasoning_effort=max`` produced
single requests of 131,071 output tokens — 100% ``reasoning_content``, zero
content, zero tool calls — at ~90-145 tok/s. One such request consumed an
entire 900s trial budget (polyglot-rust-c: ONE step recorded, reward 0).

Three protections, all in ``DeepSeekProvider``:

1. a default per-request ``max_tokens`` so the runaway is bounded at all;
2. on a truncated response with nothing actionable, ONE retry of the same
   request with ``thinking: {"type": "disabled"}`` (probed live 2026-08-09:
   the same prompt that burned its whole 4,096-token budget on reasoning
   answered in 4.7s / 527 tokens with thinking disabled);
3. after N such burns in one session, thinking is disabled for the rest of
   the session.
"""

from __future__ import annotations

import pytest

from src.providers.base import ChatResponse
from src.providers.deepseek_provider import DeepSeekProvider
from src.providers.openai_compatible import OpenAICompatibleProvider


def _provider() -> DeepSeekProvider:
    return DeepSeekProvider(api_key="k", model="deepseek-v4-flash")


def _burn(usage=None) -> ChatResponse:
    """A pure-reasoning truncation: length-capped, nothing actionable."""
    return ChatResponse(
        content="",
        model="deepseek-v4-flash",
        usage=usage or {"input_tokens": 100, "output_tokens": 16384,
                        "reasoning_tokens": 16384},
        finish_reason="length",
        reasoning_content="hmm " * 100,
    )


def _ok(content="done") -> ChatResponse:
    return ChatResponse(
        content=content,
        model="deepseek-v4-flash",
        usage={"input_tokens": 100, "output_tokens": 50},
        finish_reason="stop",
    )


class _Script:
    """Patch the base streaming/chat entrypoints with scripted responses."""

    def __init__(self, monkeypatch, responses):
        self.calls: list[dict] = []
        responses = list(responses)

        def fake(_self, messages, tools=None, **kwargs):
            # chat_stream_response's extra callback kwargs land in **kwargs
            # here and are recorded alongside the request fields.
            self.calls.append(dict(kwargs))
            return responses.pop(0)

        monkeypatch.setattr(OpenAICompatibleProvider, "chat_stream_response", fake)
        monkeypatch.setattr(OpenAICompatibleProvider, "chat", fake)


# --- output cap ------------------------------------------------------------


def test_default_cap_applied_when_caller_sets_none(monkeypatch):
    script = _Script(monkeypatch, [_ok()])
    _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    assert script.calls[0]["max_tokens"] == DeepSeekProvider.DEFAULT_OUTPUT_CAP


def test_explicit_small_max_tokens_preserved(monkeypatch):
    script = _Script(monkeypatch, [_ok()])
    _provider().chat_stream_response(
        [{"role": "user", "content": "hi"}], max_tokens=2048
    )
    assert script.calls[0]["max_tokens"] == 2048


def test_huge_explicit_max_tokens_clamped_to_twice_cap(monkeypatch):
    script = _Script(monkeypatch, [_ok()])
    _provider().chat_stream_response(
        [{"role": "user", "content": "hi"}], max_tokens=100_000
    )
    assert script.calls[0]["max_tokens"] == DeepSeekProvider.DEFAULT_OUTPUT_CAP * 2


def test_cap_disabled_via_env(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_DEEPSEEK_MAX_OUTPUT", "0")
    script = _Script(monkeypatch, [_ok()])
    _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    assert "max_tokens" not in script.calls[0]


def test_cap_value_overridable_via_env(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_DEEPSEEK_MAX_OUTPUT", "9000")
    script = _Script(monkeypatch, [_ok()])
    _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    assert script.calls[0]["max_tokens"] == 9000


# --- the fuse --------------------------------------------------------------


def test_reasoning_burn_retries_with_thinking_disabled(monkeypatch):
    script = _Script(monkeypatch, [_burn(), _ok("acted")])
    result = _provider().chat_stream_response(
        [{"role": "user", "content": "hi"}],
        extra_body={"reasoning_effort": "max"},
    )
    assert result.content == "acted"
    assert len(script.calls) == 2
    retry_body = script.calls[1]["extra_body"]
    assert retry_body["thinking"] == {"type": "disabled"}
    # A level and an explicit disable configure the same mode; the retry
    # must not carry both.
    assert "reasoning_effort" not in retry_body


def test_burn_usage_merged_into_returned_response(monkeypatch):
    _Script(monkeypatch, [_burn(), _ok()])
    result = _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    # 16384 burned + 50 real
    assert result.usage["output_tokens"] == 16434
    assert result.usage["input_tokens"] == 200


def test_truncation_with_tool_calls_is_not_a_burn(monkeypatch):
    truncated_but_useful = ChatResponse(
        content="",
        model="deepseek-v4-flash",
        usage={},
        finish_reason="length",
        tool_uses=[{"id": "t1", "name": "Bash", "input": {"command": "ls"}}],
    )
    script = _Script(monkeypatch, [truncated_but_useful])
    result = _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    assert result.tool_uses
    assert len(script.calls) == 1


def test_truncation_with_content_is_not_a_burn(monkeypatch):
    truncated_text = ChatResponse(
        content="partial answer",
        model="deepseek-v4-flash",
        usage={},
        finish_reason="length",
    )
    script = _Script(monkeypatch, [truncated_text])
    _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    assert len(script.calls) == 1


def test_fuse_disabled_via_env(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_DEEPSEEK_FUSE", "0")
    script = _Script(monkeypatch, [_burn()])
    result = _provider().chat_stream_response([{"role": "user", "content": "hi"}])
    assert result.finish_reason == "length"
    assert len(script.calls) == 1


def test_sticky_disable_after_repeated_burns(monkeypatch):
    monkeypatch.setenv("CLAWCODEX_DEEPSEEK_FUSE_STICKY", "3")
    provider = _provider()
    script = _Script(
        monkeypatch,
        [_burn(), _ok(), _burn(), _ok(), _burn(), _ok(), _ok("later")],
    )
    for _ in range(3):
        provider.chat_stream_response([{"role": "user", "content": "hi"}])
    assert provider._thinking_disabled_sticky
    # 4th request: thinking disabled up front, no burn, no retry.
    result = provider.chat_stream_response(
        [{"role": "user", "content": "hi"}],
        extra_body={"reasoning_effort": "max"},
    )
    assert result.content == "later"
    first_attempt_body = script.calls[-1]["extra_body"]
    assert first_attempt_body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in first_attempt_body


def test_sticky_off_by_default_for_interactive_sessions(monkeypatch):
    """The provider default is NO sticky escalation: an interactive session
    has no trial clock, and burns hours apart must not permanently cost the
    user thinking. Headless arms it via env (pinned by the profile test)."""
    monkeypatch.delenv("CLAWCODEX_DEEPSEEK_FUSE_STICKY", raising=False)
    provider = _provider()
    _Script(
        monkeypatch,
        [_burn(), _ok(), _burn(), _ok(), _burn(), _ok(), _burn(), _ok()],
    )
    for _ in range(4):
        provider.chat_stream_response([{"role": "user", "content": "hi"}])
    assert not provider._thinking_disabled_sticky


def test_chat_path_has_same_fuse(monkeypatch):
    script = _Script(monkeypatch, [_burn(), _ok("acted")])
    result = _provider().chat([{"role": "user", "content": "hi"}])
    assert result.content == "acted"
    assert script.calls[1]["extra_body"]["thinking"] == {"type": "disabled"}


def test_streaming_callbacks_forwarded_on_retry(monkeypatch):
    seen = []

    def fake(_self, messages, tools=None, on_text_chunk=None, **kwargs):
        seen.append(on_text_chunk)
        return _burn() if len(seen) == 1 else _ok()

    monkeypatch.setattr(OpenAICompatibleProvider, "chat_stream_response", fake)
    cb = lambda _t: None  # noqa: E731
    _provider().chat_stream_response(
        [{"role": "user", "content": "hi"}], on_text_chunk=cb
    )
    assert seen == [cb, cb]
