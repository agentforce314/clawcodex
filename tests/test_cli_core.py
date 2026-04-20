"""Unit tests for the Phase 1 CLI protocol layer (``src/cli_core``)."""

from __future__ import annotations

import io
import json

import pytest

from src.cli_core import (
    AssistantEvent,
    ResultEvent,
    StreamJsonReader,
    StreamJsonWriter,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    cli_error,
    cli_ok,
    ndjson_safe_dumps,
)


# ---------------------------------------------------------------------------
# ndjson_safe_dumps


def test_ndjson_safe_dumps_matches_json_for_plain_input():
    value = {"hello": "world", "n": 1, "list": [1, 2, 3]}
    assert ndjson_safe_dumps(value) == json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_ndjson_safe_dumps_escapes_line_separator():
    value = {"text": "before\u2028after"}
    encoded = ndjson_safe_dumps(value)
    assert "\u2028" not in encoded
    assert "\\u2028" in encoded
    # Must still parse back to the original value.
    assert json.loads(encoded) == value


def test_ndjson_safe_dumps_escapes_paragraph_separator():
    value = "x\u2029y"
    encoded = ndjson_safe_dumps(value)
    assert "\u2029" not in encoded
    assert "\\u2029" in encoded


def test_ndjson_safe_dumps_roundtrips_complex_payloads():
    payload = {
        "type": "assistant",
        "text": "multi\nline\u2028with\u2029terminators",
        "items": [{"k": "v"}, None, True, 3.14],
    }
    line = ndjson_safe_dumps(payload)
    assert "\n" not in line  # single NDJSON line
    assert json.loads(line) == payload


# ---------------------------------------------------------------------------
# cli_error / cli_ok


def test_cli_error_writes_to_stderr_and_exits(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_error("boom", code=3)
    assert excinfo.value.code == 3
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert captured.out == ""


def test_cli_error_without_message(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_error()
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == ""


def test_cli_ok_writes_to_stdout_and_exits(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_ok("done")
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == "done\n"


# ---------------------------------------------------------------------------
# StreamJsonReader


def _read_lines(lines: list[str]):
    return list(StreamJsonReader(io.StringIO("\n".join(lines) + "\n")))


def test_stream_json_reader_parses_string_content():
    msgs = _read_lines(['{"type": "user", "message": {"content": "hello"}}'])
    assert len(msgs) == 1
    assert msgs[0].text == "hello"


def test_stream_json_reader_parses_block_list_content():
    line = json.dumps({
        "type": "user",
        "message": {
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ]
        },
    })
    msgs = _read_lines([line])
    assert len(msgs) == 1
    assert msgs[0].text == "part one\npart two"


def test_stream_json_reader_accepts_bare_prompt_shape():
    msgs = _read_lines(['{"prompt": "quick"}'])
    assert len(msgs) == 1
    assert msgs[0].text == "quick"


def test_stream_json_reader_skips_invalid_json_and_non_user_events():
    msgs = _read_lines([
        "not json",
        json.dumps({"type": "system", "message": {"content": "ignored"}}),
        json.dumps({"type": "user", "message": {"content": "ok"}}),
    ])
    assert [m.text for m in msgs] == ["ok"]


def test_stream_json_reader_skips_blank_lines():
    stream = io.StringIO("\n\n" + json.dumps({"type": "user", "message": {"content": "x"}}) + "\n\n")
    msgs = list(StreamJsonReader(stream))
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# StreamJsonWriter


def test_stream_json_writer_emits_ndjson_lines():
    buf = io.StringIO()
    writer = StreamJsonWriter(buf)

    writer.write(SystemEvent(session_id="s1", model="m", provider="p", cwd="/tmp", tools=["Bash"]))
    writer.write(ToolUseEvent(tool_use_id="t1", name="Bash", input={"command": "ls"}))
    writer.write(ToolResultEvent(tool_use_id="t1", name="Bash", output="ok", is_error=False))
    writer.write(AssistantEvent(text="done"))
    writer.write(ResultEvent(session_id="s1", num_turns=1, result="done", duration_ms=10))

    lines = [l for l in buf.getvalue().splitlines() if l]
    assert len(lines) == 5
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["type"] == "system"
    assert parsed[1]["type"] == "tool_use"
    assert parsed[2]["type"] == "tool_result"
    assert parsed[3]["type"] == "assistant"
    assert parsed[4]["type"] == "result"


def test_stream_json_writer_escapes_line_terminators_in_output():
    buf = io.StringIO()
    writer = StreamJsonWriter(buf)
    writer.write(AssistantEvent(text="x\u2028y"))
    line = buf.getvalue().strip()
    assert "\u2028" not in line
    # Still parses to the original value.
    payload = json.loads(line)
    assert payload["text"] == "x\u2028y"


def test_stream_json_writer_accepts_plain_dict():
    buf = io.StringIO()
    writer = StreamJsonWriter(buf)
    writer.write({"type": "custom", "k": 1})
    assert json.loads(buf.getvalue().strip()) == {"type": "custom", "k": 1}
