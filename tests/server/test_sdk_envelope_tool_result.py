"""_sdk_envelope forwarding of rich Edit/Write tool results (tool_use_result).

The TUI renders the structured patch (line numbers, word diff, hunks) from
``tool_use_result`` on the user envelope; these tests pin the trimming rules
and the no-mutation contract, plus the diff_utils normalization the wire
shape depends on.
"""

from __future__ import annotations

import copy

from src.server.agent_server import _display_tool_result, _sdk_envelope
from src.tool_system.diff_utils import convert_leading_tabs_to_spaces, unified_diff_hunks
from src.types.messages import create_user_message


def _edit_output(**overrides):
    out = {
        "type": "update",
        "filePath": "/tmp/x.py",
        "content": "line one\nline two\n",
        "structuredPatch": [
            {"oldStart": 1, "oldLines": 2, "newStart": 1, "newLines": 2, "lines": [" a", "-b", "+B"]}
        ],
    }
    out.update(overrides)
    return out


class TestDisplayToolResult:
    def test_update_replaces_content_with_first_line(self):
        trimmed = _display_tool_result(_edit_output())
        assert trimmed == {
            "type": "update",
            "filePath": "/tmp/x.py",
            "structuredPatch": _edit_output()["structuredPatch"],
            "firstLine": "line one",
        }

    def test_create_keeps_content(self):
        trimmed = _display_tool_result(
            _edit_output(type="create", structuredPatch=[], content="new file\nbody\n")
        )
        assert trimmed is not None
        assert trimmed["content"] == "new file\nbody\n"
        assert "firstLine" not in trimmed

    def test_original_file_never_forwarded(self):
        trimmed = _display_tool_result(_edit_output(originalFile="old contents"))
        assert trimmed is not None
        assert "originalFile" not in trimmed

    def test_rejects_non_edit_shapes(self):
        assert _display_tool_result("Error: old_string not found in file") is None
        assert _display_tool_result(None) is None
        assert _display_tool_result({"stdout": "", "stderr": ""}) is None
        assert _display_tool_result(_edit_output(type="rename")) is None
        assert _display_tool_result(_edit_output(structuredPatch="nope")) is None
        assert _display_tool_result(_edit_output(filePath=123)) is None

    def test_source_dict_is_not_mutated(self):
        source = _edit_output(originalFile="old contents")
        snapshot = copy.deepcopy(source)
        _display_tool_result(source)
        assert source == snapshot


def _web_search_output(**overrides):
    out = {
        "query": "obama news",
        "results": [
            "**Title A** -- snippet (https://a.example)",
            {"tool_use_id": "tavily-search", "content": [{"title": "A", "url": "https://a.example"}]},
        ],
        "duration_seconds": 2.4,
    }
    out.update(overrides)
    return out


class TestDisplayWebSearchResult:
    """WebSearch output is trimmed to the numbers the TUI one-liner needs
    (original renders "Did N searches in Xs" — UI.tsx getSearchSummary counts
    non-string results)."""

    def test_trims_to_search_count_and_duration(self):
        assert _display_tool_result(_web_search_output()) == {
            "type": "web_search",
            "durationSeconds": 2.4,
            "searchCount": 1,
        }

    def test_counts_only_non_string_results(self):
        trimmed = _display_tool_result(_web_search_output(results=["No results found."]))
        assert trimmed is not None
        assert trimmed["searchCount"] == 0

    def test_rejects_lookalike_shapes(self):
        # bool is an int subclass — a True duration is not a duration
        assert _display_tool_result(_web_search_output(duration_seconds=True)) is None
        assert _display_tool_result(_web_search_output(duration_seconds="2.4")) is None
        assert _display_tool_result(_web_search_output(results="not-a-list")) is None
        assert _display_tool_result(_web_search_output(query=7)) is None
        # an explicit type field routes to the Edit/Write branch, not this one
        assert _display_tool_result(_web_search_output(type="rename")) is None

    def test_source_dict_is_not_mutated(self):
        source = _web_search_output()
        snapshot = copy.deepcopy(source)
        _display_tool_result(source)
        assert source == snapshot


class TestSdkEnvelope:
    def test_user_envelope_carries_trimmed_tool_use_result(self):
        msg = create_user_message(
            content=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            toolUseResult=_edit_output(),
        )
        env = _sdk_envelope(msg, "sess")
        assert env is not None
        assert env["type"] == "user"
        assert env["tool_use_result"]["firstLine"] == "line one"
        assert "content" not in env["tool_use_result"]

    def test_envelope_omits_field_for_plain_results(self):
        msg = create_user_message(
            content=[{"type": "tool_result", "tool_use_id": "t1", "content": "boom"}],
            toolUseResult="Error: boom",
        )
        env = _sdk_envelope(msg, "sess")
        assert env is not None
        assert "tool_use_result" not in env

    def test_envelope_leaves_message_tool_use_result_intact(self):
        source = _edit_output(originalFile="old contents")
        snapshot = copy.deepcopy(source)
        msg = create_user_message(
            content=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            toolUseResult=source,
        )
        _sdk_envelope(msg, "sess")
        assert source == snapshot


class TestDiffUtilsNormalization:
    def test_hunk_lines_have_no_terminators_lf(self):
        import difflib

        before = "a\nb\nc\n".splitlines(keepends=True)
        after = "a\nB\nc\n".splitlines(keepends=True)
        hunks = unified_diff_hunks(difflib.unified_diff(before, after, n=3, lineterm=""))
        assert hunks[0]["lines"] == [" a", "-b", "+B", " c"]

    def test_hunk_lines_have_no_terminators_crlf(self):
        import difflib

        before = "a\r\nb\r\n".splitlines(keepends=True)
        after = "a\r\nB\r\n".splitlines(keepends=True)
        hunks = unified_diff_hunks(difflib.unified_diff(before, after, n=3, lineterm=""))
        assert hunks[0]["lines"] == [" a", "-b", "+B"]

    def test_no_trailing_newline_final_line(self):
        import difflib

        before = "a\nb".splitlines(keepends=True)
        after = "a\nB".splitlines(keepends=True)
        hunks = unified_diff_hunks(difflib.unified_diff(before, after, n=3, lineterm=""))
        assert hunks[0]["lines"] == [" a", "-b", "+B"]

    def test_leading_tabs_become_two_spaces(self):
        assert convert_leading_tabs_to_spaces("\tx\n\t\ty\nz\ta\n") == "  x\n    y\nz\ta\n"
        # fast path: untouched when no tabs
        s = "plain\n"
        assert convert_leading_tabs_to_spaces(s) is s

    def test_lone_cr_terminators_stripped(self):
        import difflib

        before = "a\rb\r".splitlines(keepends=True)
        after = "a\rB\r".splitlines(keepends=True)
        hunks = unified_diff_hunks(difflib.unified_diff(before, after, n=3, lineterm=""))
        assert hunks[0]["lines"] == [" a", "-b", "+B"]

    def test_content_lines_starting_with_marker_runs_survive(self):
        # A removed "-- sql comment" emits "--- sql comment" and an added
        # "++i;" emits "+++i;" — both are hunk CONTENT, not file headers, and
        # must not be eaten (the header skip regression).
        import difflib

        before = "x\n-- sql comment\ny\n".splitlines(keepends=True)
        after = "x\ny\n++i;\n".splitlines(keepends=True)
        hunks = unified_diff_hunks(difflib.unified_diff(before, after, fromfile="a", tofile="b", n=3, lineterm=""))
        assert hunks[0]["lines"] == [" x", "--- sql comment", " y", "+++i;"]
