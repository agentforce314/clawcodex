"""Tests for Phase-7 specialized permission previews (gap #8)."""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.panel import Panel

from src.tui.screens.permission_modal import preview_for_tool


def _render(renderable) -> str:
    """Capture a Rich renderable as a plain string for assertion."""

    if renderable is None:
        return ""
    console = Console(record=True, width=120, force_terminal=False)
    console.print(renderable)
    return console.export_text()


# ------------------------------------------------------------------
# Generic fallback
# ------------------------------------------------------------------


def test_unknown_tool_falls_back_to_generic_preview() -> None:
    out = preview_for_tool("UnknownTool", {"foo": "bar"})
    assert isinstance(out, Panel)
    assert "foo: bar" in _render(out)


def test_empty_input_returns_none() -> None:
    assert preview_for_tool("Bash", {}) is None
    assert preview_for_tool("Bash", None) is None


def test_unknown_tool_with_long_value_truncates() -> None:
    huge = "x" * 1000
    out = preview_for_tool("Frobnicate", {"k": huge})
    text = _render(out)
    assert "…" in text  # truncation marker present


# ------------------------------------------------------------------
# Bash
# ------------------------------------------------------------------


def test_bash_renders_command() -> None:
    out = preview_for_tool("Bash", {"command": "ls -la"})
    assert out is not None
    text = _render(out)
    assert "$" in text
    assert "ls -la" in text


def test_bash_renders_matching_rule_when_present() -> None:
    out = preview_for_tool(
        "Bash",
        {"command": "git commit", "matched_permission_rule": "Bash(git commit*)"},
    )
    text = _render(out)
    assert "Bash(git commit*)" in text


def test_bash_renders_description_when_present() -> None:
    out = preview_for_tool(
        "Bash",
        {"command": "ls", "description": "list the files"},
    )
    assert "list the files" in _render(out)


def test_bash_no_command_returns_generic() -> None:
    """Without ``command`` the Bash renderer returns None and the
    dispatcher falls through to the generic preview."""

    out = preview_for_tool("Bash", {"description": "no command"})
    text = _render(out)
    assert "description: no command" in text


def test_bash_lowercase_alias_works() -> None:
    out = preview_for_tool("bash", {"command": "ls"})
    assert out is not None
    assert "ls" in _render(out)


# ------------------------------------------------------------------
# Edit
# ------------------------------------------------------------------


def test_edit_renders_inline_diff() -> None:
    out = preview_for_tool(
        "Edit",
        {
            "file_path": "src/foo.py",
            "old_string": "old line one\nold line two",
            "new_string": "new line one",
        },
    )
    text = _render(out)
    assert "src/foo.py" in text
    assert "- old line one" in text
    assert "- old line two" in text
    assert "+ new line one" in text


def test_edit_truncates_huge_diffs() -> None:
    old = "\n".join(f"old{i}" for i in range(50))
    new = "\n".join(f"new{i}" for i in range(50))
    out = preview_for_tool(
        "Edit",
        {"file_path": "f.py", "old_string": old, "new_string": new},
    )
    text = _render(out)
    assert "more removed lines" in text
    assert "more added lines" in text


def test_edit_only_path_no_diff_still_renders() -> None:
    out = preview_for_tool(
        "Edit", {"file_path": "f.py", "old_string": "", "new_string": ""}
    )
    text = _render(out)
    assert "f.py" in text


# ------------------------------------------------------------------
# Write
# ------------------------------------------------------------------


def test_write_renders_path_and_content() -> None:
    out = preview_for_tool(
        "Write",
        {"file_path": "out.txt", "content": "hello world"},
    )
    text = _render(out)
    assert "out.txt" in text
    assert "hello world" in text


def test_write_truncates_long_content() -> None:
    out = preview_for_tool(
        "Write",
        {"file_path": "f.txt", "content": "x" * 1000},
    )
    text = _render(out)
    assert "…" in text


# ------------------------------------------------------------------
# Read
# ------------------------------------------------------------------


def test_read_renders_path_and_offset_limit() -> None:
    out = preview_for_tool(
        "Read",
        {"file_path": "src/x.py", "offset": 10, "limit": 50},
    )
    text = _render(out)
    assert "src/x.py" in text
    assert "offset=10" in text
    assert "limit=50" in text


def test_read_no_path_returns_none() -> None:
    """Read without a file_path doesn't have a meaningful summary —
    fall through to generic."""

    out = preview_for_tool("Read", {"limit": 10})
    text = _render(out)
    assert "limit: 10" in text


# ------------------------------------------------------------------
# Renderer error → generic fallback
# ------------------------------------------------------------------


def test_handler_exception_falls_back_to_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a per-tool renderer raises, the dispatcher must not crash —
    the generic preview takes over."""

    from src.tui.screens import permission_modal

    def boom(_input):  # pragma: no cover - exercised via dispatcher
        raise RuntimeError("simulated render failure")

    monkeypatch.setitem(permission_modal._TOOL_RENDERERS, "Boom", boom)
    out = preview_for_tool("Boom", {"command": "ls"})
    text = _render(out)
    assert "command: ls" in text
