"""Unsigned thinking blocks must not reach the wire.

Signed blocks replay byte-for-byte — the signature carries the opaque state
the API validates. An unsigned block can never satisfy that contract:
Anthropic-shaped endpoints reject it outright
("messages.N.content.0.thinking.signature: Field required"), which killed the
first turn after resuming a stored conversation and later turns against
compatible endpoints that emit unsigned thinking (DeepSeek).
"""

from __future__ import annotations

from src.providers.anthropic_provider import _strip_unsigned_thinking


def _assistant(*blocks: dict) -> dict:
    return {"role": "assistant", "content": list(blocks)}


def test_drops_an_unsigned_thinking_block() -> None:
    out = _strip_unsigned_thinking(
        [_assistant({"type": "thinking", "thinking": "x"}, {"type": "text", "text": "hi"})]
    )

    assert out[0]["content"] == [{"type": "text", "text": "hi"}]


def test_keeps_a_signed_block_byte_for_byte() -> None:
    signed = {"type": "thinking", "thinking": "x", "signature": "sig=="}
    out = _strip_unsigned_thinking([_assistant(signed, {"type": "text", "text": "hi"})])

    assert out[0]["content"][0] is signed


def test_keeps_redacted_thinking() -> None:
    # redacted_thinking carries `data`, not a signature; it is valid replay.
    redacted = {"type": "redacted_thinking", "data": "opaque"}
    out = _strip_unsigned_thinking([_assistant(redacted)])

    assert out[0]["content"] == [redacted]


def test_treats_an_empty_signature_as_unsigned() -> None:
    out = _strip_unsigned_thinking(
        [_assistant({"type": "thinking", "thinking": "x", "signature": ""})]
    )

    assert out[0]["content"] == []


def test_leaves_user_messages_and_string_content_alone() -> None:
    msgs = [
        {"role": "user", "content": [{"type": "thinking", "thinking": "quoted"}]},
        {"role": "assistant", "content": "plain string"},
    ]

    assert _strip_unsigned_thinking(msgs) == msgs


def test_does_not_mutate_its_input() -> None:
    original = _assistant({"type": "thinking", "thinking": "x"}, {"type": "text", "text": "hi"})
    msgs = [original]

    _strip_unsigned_thinking(msgs)

    assert len(original["content"]) == 2


# ── the OpenAI-compatible path ────────────────────────────────────────────────
#
# Chat Completions has no thinking representation at all, so there the strip is
# unconditional: DeepSeek-style endpoints answer "messages[N]: unknown variant
# `thinking`, expected `text`" to any that leak through.


def test_openai_conversion_drops_thinking_blocks() -> None:
    from src.providers.openai_compatible import _convert_anthropic_messages_to_openai

    out = _convert_anthropic_messages_to_openai(
        [
            {"role": "user", "content": "hi"},
            _assistant(
                {"type": "thinking", "thinking": "x", "signature": "even-signed"},
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": "HOLA"},
            ),
        ]
    )

    assert out[1]["content"] == [{"type": "text", "text": "HOLA"}]


def test_openai_conversion_survives_a_thinking_only_message() -> None:
    from src.providers.openai_compatible import _convert_anthropic_messages_to_openai

    out = _convert_anthropic_messages_to_openai(
        [_assistant({"type": "thinking", "thinking": "x"})]
    )

    assert out[0]["role"] == "assistant"
    assert out[0]["content"] in ([], "", None)
