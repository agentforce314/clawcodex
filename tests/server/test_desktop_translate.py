"""Frame → gateway-event translation, especially tool rows.

The desktop renders each tool row from the payload's `name`/`args`/`result`
(lib/chat-messages.ts `upsertToolPart` → `toolArgs`/`toolResult`). Sending the
wrong field names produced contentless "Tool" rows; leaking a content block
where prose was expected printed raw `{"type":"tool_use",…}` JSON in the
transcript (the renderer's coerceGatewayText stringifies an object it can't
read). Both are regressions worth pinning.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.server.desktop_gateway_translate import (
    as_text,
    clean_tool_error,
    inline_diff_from_display,
    render_tool_name,
    render_tool_result,
    summarize_tool_result,
    tool_context,
    translate_frame,
    translate_sdk_envelope,
    translate_stream_event,
)


def _complete(name: str, text: str, display=None, is_error: bool = False):
    block = {"type": "tool_result", "tool_use_id": "c", "content": text}
    if is_error:
        block["is_error"] = True
    frame = {"type": "user", "message": {"role": "user", "content": [block]}}
    if display is not None:
        frame["tool_use_result"] = display
    (_, payload), = translate_sdk_envelope(frame, {"c": name})
    return payload


def test_tool_start_carries_name_args_and_context() -> None:
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
    (kind, payload), = events
    assert kind == "tool.start"
    # Text blocks are NOT re-emitted (they already streamed as deltas).
    assert payload["tool_id"] == "call_1"
    assert payload["name"] == "read_file"
    # `path` is aliased in; the renderer never looks for `file_path`.
    assert payload["args"]["path"] == "/w/seed.py"
    assert payload["args"]["file_path"] == "/w/seed.py"
    assert payload["context"] == "/w/seed.py"
    # The name is remembered under its ORIGINAL spelling for the result frame.
    assert names == {"call_1": "Read"}


def test_tool_names_map_to_the_renderers_vocabulary() -> None:
    """Every per-tool formatter in the desktop renderer keys off these names;
    an unmapped name falls through to an unlabelled generic row."""
    assert render_tool_name("Read") == "read_file"
    assert render_tool_name("Bash") == "terminal"
    assert render_tool_name("Glob") == "list_files"
    assert render_tool_name("Grep") == "search_files"
    assert render_tool_name("WebSearch") == "web_search"
    assert render_tool_name("WebFetch") == "web_extract"
    assert render_tool_name("Edit") == "edit_file"
    assert render_tool_name("TodoWrite") == "todo"
    # Unknown tools pass through rather than being mangled.
    assert render_tool_name("Task") == "Task"


def test_tool_context_matches_the_tui_priority() -> None:
    # A pattern beats a path, so Grep shows what it searched FOR.
    assert tool_context({"pattern": "TODO", "path": "/w"}) == "TODO"
    assert tool_context({"file_path": "/w/a.py"}) == "/w/a.py"
    assert tool_context({"command": "ls src/"}) == "ls src/"
    assert tool_context({"query": "weather"}) == "weather"
    assert tool_context({"description": "refactor"}) == "refactor"
    assert tool_context({}) == ""


def test_read_result_lands_where_the_renderer_reads_it() -> None:
    payload = _complete("Read", "1\tprint('seed')\n2\tprint('again')")
    assert payload["name"] == "read_file"
    # A plain string here is dropped by the renderer's parseMaybeJsonObject —
    # the text has to arrive under a key it knows.
    assert payload["result"]["content"] == "1\tprint('seed')\n2\tprint('again')"
    assert payload["result"]["context"] == "Read 2 lines"


def test_bash_output_becomes_a_terminal_body() -> None:
    payload = _complete("Bash", "src\ntests\n")
    assert payload["name"] == "terminal"
    assert payload["result"]["output"] == "src\ntests\n"


def test_glob_and_grep_keep_their_matches_and_get_a_count_chip() -> None:
    """A count reads like the TUI's "Found 3 files" without a summary
    displacing the matches themselves — which the renderer would do if the
    only `context` in play were the argument ("*.py")."""
    glob = _complete("Glob", "a.py\nb.py\nc.py")
    assert glob["name"] == "list_files"
    assert glob["result"]["file_count"] == 3
    assert glob["result"]["output"] == "a.py\nb.py\nc.py"
    assert glob["result"]["context"] == "a.py\nb.py\nc.py"

    grep = _complete("Grep", "a.py:1:TODO\nb.py:4:TODO")
    assert grep["result"]["match_count"] == 2


def test_edit_result_is_a_diff() -> None:
    payload = _complete(
        "Edit", "ok",
        {"type": "update", "filePath": "/w/a.py",
         "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1,
                              "newLines": 1, "lines": ["-old", "+new"]}]},
    )
    assert payload["name"] == "edit_file"
    diff = payload["result"]["inline_diff"]
    assert "@@ -1,1 +1,1 @@" in diff
    assert "-old" in diff and "+new" in diff
    assert payload["result"]["path"] == "/w/a.py"


def test_write_of_a_new_file_renders_as_an_all_additions_diff() -> None:
    diff = inline_diff_from_display(
        {"type": "create", "filePath": "/w/hi.py", "content": "print('hi')", "structuredPatch": []}
    )
    assert diff == "--- /dev/null\n+++ /w/hi.py\n+print('hi')"


def test_read_image_reports_its_size() -> None:
    payload = _complete("Read", "", {"type": "image", "originalSize": 12_600})
    assert payload["result"]["context"] == "Read image (12.3KB)"


def test_web_search_summarizes_count_and_duration() -> None:
    display = {"type": "web_search", "searchCount": 3, "durationSeconds": 2.4}
    assert summarize_tool_result("WebSearch", "", display) == "Did 3 searches in 2s"
    payload = _complete("WebSearch", "…blob…", display)
    assert payload["result"]["result_count"] == 3
    assert payload["result"]["duration_s"] == 2.4


def test_failed_tool_shows_the_reason_and_no_diff() -> None:
    payload = _complete("Bash", "<tool_use_error>no such file</tool_use_error>",
                        is_error=True)
    # The renderer flags a failed row from `error` being truthy.
    assert payload["error"] == "Error: no such file"
    # Nothing was written, so nothing is rendered as output.
    assert payload["result"] == {}


def test_error_text_is_cleaned_like_the_tui() -> None:
    assert clean_tool_error("") == "Tool execution failed"
    assert clean_tool_error("<tool_use_error>InputValidationError: bad</tool_use_error>") == (
        "Invalid tool parameters"
    )
    # An already-prefixed message isn't double-prefixed.
    assert clean_tool_error("Error: boom") == "Error: boom"
    assert clean_tool_error("Cancelled: by user") == "Cancelled: by user"
    assert clean_tool_error("<sandbox_violations>x</sandbox_violations>plain") == "Error: plain"


def test_unknown_tool_still_renders_its_output() -> None:
    result = render_tool_result("SomeMcpTool", "hello", None)
    assert result == {"output": "hello", "context": "hello"}


# ── cross-language contract ──────────────────────────────────────────────────
# A payload the renderer can't read is invisible to both languages' own tests
# and shows up only as a blank row in the transcript — which is how the "Tool"
# rows shipped. So the payloads this translator emits are frozen into a
# fixture, and ui-desktop/src/lib/gateway-tool-contract.test.ts renders that
# same fixture and asserts what the user ends up seeing. Changing the wire
# shape fails here until the fixture is regenerated, and fails there if the
# new shape doesn't actually render.

FIXTURE = Path(__file__).resolve().parents[2] / "ui-desktop/src/lib/gateway-tool-events.fixture.json"

# (case name, tool, input, output text, display envelope, is_error)
CONTRACT_CASES = [
    ("read", "Read", {"file_path": "/w/seed.py"},
     "1\tprint('seed')\n2\tprint('again')", None, False),
    ("bash", "Bash", {"command": "ls src/"}, "app\nlib\n", None, False),
    ("edit", "Edit", {"file_path": "/w/a.py", "old_string": "old", "new_string": "new"}, "ok",
     {"type": "update", "filePath": "/w/a.py",
      "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1,
                           "lines": ["-old", "+new"]}]}, False),
    ("write", "Write", {"file_path": "/w/hi.py", "content": "print('hi')"}, "ok",
     {"type": "create", "filePath": "/w/hi.py", "content": "print('hi')",
      "structuredPatch": []}, False),
    ("glob", "Glob", {"pattern": "*.py"}, "a.py\nb.py\nc.py", None, False),
    ("grep", "Grep", {"pattern": "TODO", "path": "/w"}, "a.py:1:TODO\nb.py:4:TODO", None, False),
    ("web_search", "WebSearch", {"query": "weather"}, "…blob…",
     {"type": "web_search", "searchCount": 3, "durationSeconds": 2.4}, False),
    ("failure", "Bash", {"command": "cat nope"},
     "<tool_use_error>no such file</tool_use_error>", None, True),
    ("unknown_tool", "Task", {"description": "refactor"}, "done", None, False),
]


def _contract_payloads() -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for name, tool, tool_input, text, display, is_error in CONTRACT_CASES:
        names: dict[str, str] = {}
        (_, start), = translate_sdk_envelope(
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call", "name": tool, "input": tool_input},
            ]}},
            names,
        )
        block = {"type": "tool_result", "tool_use_id": "call", "content": text}
        if is_error:
            block["is_error"] = True
        frame = {"type": "user", "message": {"role": "user", "content": [block]}}
        if display is not None:
            frame["tool_use_result"] = display
        (_, complete), = translate_sdk_envelope(frame, names)
        cases[name] = {"start": start, "complete": complete}
    return cases


def test_wire_payloads_match_the_renderer_fixture() -> None:
    current = _contract_payloads()
    if os.environ.get("UPDATE_FIXTURES"):
        FIXTURE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    recorded = json.loads(FIXTURE.read_text())
    assert current == recorded, (
        "tool.start/tool.complete payloads changed. Re-run with UPDATE_FIXTURES=1 "
        "and make sure ui-desktop's gateway-tool-contract.test.ts still passes — "
        "it renders this fixture and asserts what the user actually sees."
    )


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
