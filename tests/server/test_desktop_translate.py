"""Frame → gateway-event translation, especially tool rows.

The desktop renders each tool row from the payload's `name`/`args`/`result`
(lib/chat-messages.ts `upsertToolPart` → `toolArgs`/`toolResult`). Sending the
wrong field names produced contentless "Tool" rows; leaking a content block
where prose was expected printed raw `{"type":"tool_use",…}` JSON in the
transcript (the renderer's coerceGatewayText stringifies an object it can't
read). Both are regressions worth pinning.
"""

from __future__ import annotations

from src.server.desktop_gateway_translate import (
    as_text,
    translate_frame,
    translate_sdk_envelope,
    translate_stream_event,
)


def test_tool_start_carries_name_and_args() -> None:
    names: dict[str, str] = {}
    events = translate_sdk_envelope(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "I'll read it."},
                {"type": "tool_use", "id": "call_1", "name": "Read",
                 "input": {"file_path": "/w/seed.py"}},
            ]},
        },
        names,
    )
    assert events == [("tool.start", {"tool_id": "call_1", "name": "Read",
                                      "args": {"file_path": "/w/seed.py"}})]
    # Text blocks are NOT re-emitted (they already streamed as deltas).
    assert all(kind != "message.delta" for kind, _ in events)
    # The name is remembered for the matching tool_result.
    assert names == {"call_1": "Read"}


def test_tool_complete_uses_the_fields_the_renderer_reads() -> None:
    names = {"call_1": "Read"}
    events = translate_sdk_envelope(
        {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": [{"type": "text", "text": "1\tprint('seed')"}]},
            ]},
        },
        names,
    )
    (kind, payload), = events
    assert kind == "tool.complete"
    # `name` labels the row (absent → the literal "tool"); `result` is what
    # toolResult() reads — the old "output" key rendered nothing.
    assert payload["name"] == "Read"
    assert payload["result"] == "1\tprint('seed')"
    assert payload["tool_id"] == "call_1"
    assert "output" not in payload


def test_tool_complete_forwards_the_display_envelope() -> None:
    """Edit/Write ride a trimmed `tool_use_result` (structuredPatch, filePath);
    forwarding it is what lets the row render a diff instead of raw text."""
    events = translate_sdk_envelope(
        {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_2", "content": "ok"},
            ]},
            "tool_use_result": {
                "type": "update", "filePath": "/w/a.py",
                "structuredPatch": [{"lines": ["-old", "+new"]}],
                "duration_s": 0.4,
            },
        },
        {"call_2": "Edit"},
    )
    (_, payload), = events
    assert payload["name"] == "Edit"
    assert payload["filePath"] == "/w/a.py"
    assert payload["structuredPatch"] == [{"lines": ["-old", "+new"]}]
    assert payload["duration_s"] == 0.4


def test_tool_error_uses_error_not_is_error() -> None:
    events = translate_sdk_envelope(
        {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "c", "content": "boom",
                 "is_error": True},
            ]},
        },
        {"c": "Bash"},
    )
    (_, payload), = events
    # The renderer flags a failed row from `error` being truthy.
    assert payload["error"] == "boom"
    assert payload["result"] == "boom"


def test_as_text_never_leaks_a_tool_block_as_prose() -> None:
    """The reported bug: raw {"type":"tool_use",…} printed inside a bubble."""
    block = {"type": "tool_use", "id": "call_1", "name": "Write",
             "input": {"file_path": "/w/hello.py"}}
    assert as_text(block) == ""
    assert as_text([{"type": "text", "text": "I'll write it."}, block]) == "I'll write it."
    assert as_text("plain") == "plain"
    assert as_text(None) == ""
    assert as_text({"text": "nested"}) == "nested"


def test_result_text_is_coerced() -> None:
    (kind, payload), = translate_frame({
        "type": "result", "subtype": "success", "is_error": False,
        "result": [{"type": "text", "text": "done"},
                   {"type": "tool_use", "id": "x", "name": "Write", "input": {}}],
    })
    assert kind == "message.complete"
    # Only the prose survives — the tool block can't reach the transcript.
    assert payload["text"] == "done"


def test_stream_deltas_map_to_message_and_reasoning() -> None:
    assert translate_stream_event({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "text_delta", "text": "hi"}},
    }) == [("message.delta", {"text": "hi"})]
    assert translate_stream_event({
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "thinking_delta", "thinking": "hmm"}},
    }) == [("reasoning.delta", {"text": "hmm"})]
