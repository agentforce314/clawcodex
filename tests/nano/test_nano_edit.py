"""pi edit ladder: algorithm unit tests + nano Edit tool integration.

Mirrors the failure modes pi engineered away (edit-diff.ts): multi-edit
against the original file, fuzzy Unicode/whitespace rescue with
byte-preserving overlay, CRLF/BOM hygiene, occurrence-counted errors,
and model-quirk argument repair.
"""

from __future__ import annotations

import pytest

from src.nano.edit_ladder import (
    Edit,
    EditLadderError,
    apply_edits_to_normalized_content,
    detect_line_ending,
    normalize_for_fuzzy_match,
)
from src.nano.edit_tool import NanoEditTool, _prepare_edits
from src.tool_system.errors import ToolInputError

# ---------------------------------------------------------------------------
# Ladder unit tests
# ---------------------------------------------------------------------------


def _apply(content, edits, **kw):
    return apply_edits_to_normalized_content(content, edits, "f.py", **kw)


def test_exact_single_edit():
    base, new = _apply("a\nb\nc\n", [Edit("b", "B")])
    assert new == "a\nB\nc\n"


def test_multi_edit_matched_against_original_any_order():
    content = "alpha\nbeta\ngamma\n"
    # Deliberately given in reverse file order — offsets must not shift.
    base, new = _apply(
        content, [Edit("gamma", "GAMMA"), Edit("alpha", "ALPHA")]
    )
    assert new == "ALPHA\nbeta\nGAMMA\n"


def test_overlapping_edits_rejected_with_merge_hint():
    with pytest.raises(EditLadderError, match="overlap.*Merge them"):
        _apply("abcdef\n", [Edit("abcd", "x"), Edit("cdef", "y")])


def test_duplicate_occurrences_counted():
    with pytest.raises(EditLadderError, match="Found 3 occurrences"):
        _apply("x\nx\nx\n", [Edit("x", "y")])


def test_not_found_is_actionable():
    with pytest.raises(EditLadderError, match="must match exactly"):
        _apply("a\n", [Edit("nope", "y")])


def test_empty_old_string_rejected():
    with pytest.raises(EditLadderError, match="must not be empty"):
        _apply("a\n", [Edit("", "y")])


def test_no_change_rejected():
    with pytest.raises(EditLadderError, match="No changes made"):
        # Only in fuzzy space do these "match": replacement equals original.
        _apply("a\n", [Edit("a ", "a")])  # trailing space fuzzy-matches "a"


def test_fuzzy_rescues_trailing_whitespace():
    content = "def f():   \n    return 1\n"
    base, new = _apply(content, [Edit("def f():\n    return 1", "def f():\n    return 2")])
    assert "return 2" in new


def test_fuzzy_rescues_smart_quotes_and_dashes():
    content = "print(“hello”)  # em—dash\n"
    base, new = _apply(content, [Edit('print("hello")  # em-dash', 'print("bye")')])
    assert "bye" in new


def test_fuzzy_preserves_unchanged_lines_bytes():
    # Line 1 has trailing spaces the fuzzy normalization strips; an edit on
    # line 3 must leave line 1's original bytes alone.
    content = "keep me   \nmiddle\ntarget\n"
    base, new = _apply(content, [Edit("target", "TARGET")])
    assert new.startswith("keep me   \n")
    assert new.endswith("TARGET\n")


def test_replace_all_single_edit():
    base, new = _apply("x = x + x\n", [Edit("x", "y")], replace_all=True)
    assert new == "y = y + y\n"


def test_normalize_for_fuzzy_match_folds_unicode():
    assert normalize_for_fuzzy_match("‘a’ “b” – c d  ") == "'a' \"b\" - c d"


def test_detect_line_ending():
    assert detect_line_ending("a\r\nb\n") == "\r\n"
    assert detect_line_ending("a\nb\r\n") == "\n"
    assert detect_line_ending("abc") == "\n"


# ---------------------------------------------------------------------------
# Argument repair (model quirks)
# ---------------------------------------------------------------------------


def test_edits_as_json_string_repaired():
    edits = _prepare_edits({
        "file_path": "f",
        "edits": '[{"old_string": "a", "new_string": "b"}]',
    })
    assert edits == [Edit("a", "b")]


def test_pi_style_oldtext_keys_accepted():
    edits = _prepare_edits({
        "file_path": "f",
        "edits": [{"oldText": "a", "newText": "b"}],
    })
    assert edits == [Edit("a", "b")]


def test_legacy_pair_merges_with_edits():
    edits = _prepare_edits({
        "file_path": "f",
        "edits": [{"old_string": "a", "new_string": "b"}],
        "old_string": "c",
        "new_string": "d",
    })
    assert edits == [Edit("a", "b"), Edit("c", "d")]


def test_legacy_create_form_delegates():
    assert _prepare_edits({
        "file_path": "f", "old_string": "", "new_string": "content",
    }) is None


# ---------------------------------------------------------------------------
# Tool-level integration (real filesystem via ToolContext)
# ---------------------------------------------------------------------------


@pytest.fixture
def tool_context(tmp_path):
    from src.tool_system.context import ToolContext

    return ToolContext(cwd=tmp_path, workspace_root=tmp_path)


def _write_and_read(tool_context, tmp_path, name, content):
    p = tmp_path / name
    # Preserve the fixture's literal line endings, including explicit CRLF.
    p.write_text(content, encoding="utf-8", newline="")
    tool_context.mark_file_read(p)
    return p


def test_tool_multi_edit_end_to_end(tool_context, tmp_path):
    p = _write_and_read(
        tool_context, tmp_path, "m.py", "a = 1\nb = 2\nc = 3\n"
    )
    result = NanoEditTool.call(
        {
            "file_path": str(p),
            "edits": [
                {"old_string": "a = 1", "new_string": "a = 10"},
                {"old_string": "c = 3", "new_string": "c = 30"},
            ],
        },
        tool_context,
    )
    assert p.read_text() == "a = 10\nb = 2\nc = 30\n"
    assert result.output["editsApplied"] == 2
    assert result.output["structuredPatch"]


def test_tool_requires_read_first(tool_context, tmp_path):
    p = tmp_path / "unread.py"
    p.write_text("x\n")
    with pytest.raises(ToolInputError, match="read first"):
        NanoEditTool.call(
            {"file_path": str(p), "old_string": "x", "new_string": "y"},
            tool_context,
        )


def test_soft_refresh_when_file_changed_elsewhere(tool_context, tmp_path):
    # Read → the agent's own script rewrites ANOTHER part of the file →
    # Edit of an untouched region proceeds without a ceremonial re-Read.
    import os
    p = _write_and_read(tool_context, tmp_path, "s.py", "alpha\nbeta\ngamma\n")
    p.write_text("ALPHA\nbeta\ngamma\n")
    os.utime(p, (1, 1))  # force an mtime change even on coarse clocks
    result = NanoEditTool.call(
        {"file_path": str(p), "old_string": "gamma", "new_string": "GAMMA"},
        tool_context,
    )
    assert p.read_text() == "ALPHA\nbeta\nGAMMA\n"
    assert result.output["type"] == "update"
    # Fingerprint refreshed: an immediate follow-up edit passes the gate.
    NanoEditTool.call(
        {"file_path": str(p), "old_string": "beta", "new_string": "BETA"},
        tool_context,
    )
    assert p.read_text() == "ALPHA\nBETA\nGAMMA\n"


def test_soft_refresh_still_blocks_changed_target(tool_context, tmp_path):
    # The externally-changed region IS the edit target → the ladder's
    # actionable not-found error, never a blind overwrite.
    import os
    p = _write_and_read(tool_context, tmp_path, "t.py", "alpha\nbeta\n")
    p.write_text("ALPHA\nbeta\n")
    os.utime(p, (1, 1))
    with pytest.raises(ToolInputError, match="must match exactly"):
        NanoEditTool.call(
            {"file_path": str(p), "old_string": "alpha", "new_string": "omega"},
            tool_context,
        )


def test_tool_crlf_file_edited_with_lf_oldstring(tool_context, tmp_path):
    p = _write_and_read(
        tool_context, tmp_path, "w.txt", "line one\r\nline two\r\n"
    )
    NanoEditTool.call(
        {
            "file_path": str(p),
            "edits": [{"old_string": "line one\nline two", "new_string": "merged"}],
        },
        tool_context,
    )
    assert p.read_bytes() == b"merged\r\n"


def test_tool_preserves_bom(tool_context, tmp_path):
    p = tmp_path / "b.txt"
    p.write_bytes("﻿hello world\n".encode("utf-8"))
    tool_context.mark_file_read(p)
    NanoEditTool.call(
        {"file_path": str(p), "old_string": "hello", "new_string": "goodbye"},
        tool_context,
    )
    assert p.read_bytes().startswith("﻿".encode("utf-8"))
    assert b"goodbye world" in p.read_bytes()


def test_tool_legacy_create_still_works(tool_context, tmp_path):
    p = tmp_path / "new.txt"
    result = NanoEditTool.call(
        {"file_path": str(p), "old_string": "", "new_string": "fresh\n"},
        tool_context,
    )
    assert p.read_text() == "fresh\n"
    assert result.output["type"] == "create"


def test_tool_replace_all_with_edits_rejected(tool_context, tmp_path):
    p = _write_and_read(tool_context, tmp_path, "r.txt", "x x\n")
    with pytest.raises(ToolInputError, match="replace_all only applies"):
        NanoEditTool.call(
            {
                "file_path": str(p),
                "replace_all": True,
                "edits": [
                    {"old_string": "a", "new_string": "b"},
                    {"old_string": "c", "new_string": "d"},
                ],
            },
            tool_context,
        )


def test_stock_edit_tool_unchanged():
    # The nano variant must be a copy: the registered stock tool keeps its
    # exact-match call and old schema.
    from src.tool_system.tools.edit import EditTool

    assert "edits" not in EditTool.input_schema["properties"]
    assert EditTool.input_schema["required"] == [
        "file_path", "old_string", "new_string",
    ]
