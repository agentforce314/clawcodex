"""Tests for ``_format_subagent_tool_use`` — the per-tool-use progress line
the parent agent prints to stderr while a sync subagent is running.

Parity target: TS's ``getActivityDescription`` (e.g. ``FileReadTool.ts:369``)
renders the input data into a per-tool sentence so the user can follow what
the subagent is doing. The Python equivalent used to hard-code ``Name(...)``
which discarded the file path / command / pattern entirely.

The helper lives in ``src.tool_system.tools.agent``; these tests pin the
formatter contract so a future refactor can't silently regress back to the
placeholder string.
"""
from __future__ import annotations

from src.tool_system.tools.agent import _format_subagent_tool_use


def test_read_includes_file_path() -> None:
    line = _format_subagent_tool_use("critic", "Read", {"file_path": "/a/b/c.py"})
    assert line == "  ⎿ [critic] Read(/a/b/c.py)\n"


def test_read_includes_line_range_when_present() -> None:
    line = _format_subagent_tool_use(
        "critic", "Read", {"file_path": "/a.py", "offset": 10, "limit": 5}
    )
    assert line == "  ⎿ [critic] Read(/a.py · lines 10-14)\n"


def test_bash_includes_command() -> None:
    line = _format_subagent_tool_use("worker", "Bash", {"command": "ls -la"})
    assert line == "  ⎿ [worker] Bash(ls -la)\n"


def test_bash_flattens_multiline_command() -> None:
    line = _format_subagent_tool_use(
        "worker", "Bash", {"command": "echo a\necho b"}
    )
    # Newlines collapse so the line stays single-row in the terminal.
    assert "\n" not in line[:-1]  # only the trailing newline counts
    assert "echo a echo b" in line


def test_grep_includes_pattern_and_path() -> None:
    line = _format_subagent_tool_use(
        "general-purpose", "Grep", {"pattern": "TODO", "path": "src"}
    )
    assert line == "  ⎿ [general-purpose] Grep(TODO · src)\n"


def test_glob_includes_pattern() -> None:
    line = _format_subagent_tool_use("worker", "Glob", {"pattern": "**/*.py"})
    assert line == "  ⎿ [worker] Glob(**/*.py)\n"


def test_agent_nested_includes_subagent_and_description() -> None:
    line = _format_subagent_tool_use(
        "coordinator",
        "Agent",
        {"subagent_type": "critic", "description": "review plan"},
    )
    assert line == "  ⎿ [coordinator] Agent(@critic · review plan)\n"


def test_unknown_tool_with_no_summary_omits_parens() -> None:
    # When the summarizer returns "", the formatter should produce a clean
    # ``Name`` instead of literal ``Name()`` or the legacy ``Name(...)``.
    line = _format_subagent_tool_use("worker", "MysteryTool", {})
    assert line == "  ⎿ [worker] MysteryTool\n"


def test_non_dict_input_is_tolerated() -> None:
    # ToolUseBlock.input is dict-typed, but if a runtime sends a stray
    # ``None`` (or list) the formatter must not raise — silent degrade to
    # the bare tool name, like the unknown-tool case.
    line = _format_subagent_tool_use("worker", "Bash", None)
    assert line == "  ⎿ [worker] Bash\n"


def test_long_summary_is_truncated() -> None:
    long_cmd = "x" * 500  # well above the 200-char cap
    line = _format_subagent_tool_use("worker", "Bash", {"command": long_cmd})
    # The line itself: "  ⎿ [worker] Bash(" + flat (<=200) + ")\n"
    # The 200-char cap applies to the flattened summary text, not the whole
    # line — the trailing ``...`` proves truncation kicked in.
    assert line.endswith("...)\n")
    # Inner text length should be at most 200 chars including the ``...``.
    inner_start = line.index("Bash(") + len("Bash(")
    inner_end = line.rindex(")")
    assert inner_end - inner_start <= 200


def test_legacy_dots_placeholder_is_gone() -> None:
    # Regression guard: the old format ``Name(...)`` for every tool use is
    # the bug we're fixing. Make sure no path produces that literal.
    for name, inp in (
        ("Read", {"file_path": "/x"}),
        ("Bash", {"command": "ls"}),
        ("Grep", {"pattern": "p"}),
        ("Glob", {"pattern": "*"}),
        ("Mystery", {}),
    ):
        line = _format_subagent_tool_use("a", name, inp)
        assert "(...)" not in line, f"legacy placeholder leaked for {name}: {line!r}"


# --- Call-site integration --------------------------------------------------
#
# The unit tests above pin the formatter contract. This one pins that
# ``_collect_agent_messages`` actually *uses* the formatter — a future
# refactor that re-inlines ``f"...{block.name}(...)\n"`` would re-introduce
# the bug while leaving the helper tests green. Critic flagged this gap.


def test_collect_agent_messages_writes_formatted_line(monkeypatch, capsys) -> None:
    """The call site must route through the formatter, not bypass it."""
    import asyncio
    from types import SimpleNamespace

    from src.tool_system.tools import agent as agent_mod
    from src.types.content_blocks import TextBlock, ToolUseBlock
    from src.types.messages import AssistantMessage

    async def fake_run_agent(_params):  # type: ignore[no-untyped-def]
        # One assistant message that mixes text + tool_use, exactly the
        # shape that produced the buggy "Read(...)" lines the user saw.
        yield AssistantMessage(
            content=[
                TextBlock(text="Let me check the file."),
                ToolUseBlock(
                    id="t1",
                    name="Read",
                    input={"file_path": "/repo/src/foo.py"},
                ),
            ],
        )

    monkeypatch.setattr(agent_mod, "run_agent", fake_run_agent)

    params = SimpleNamespace(agent_definition=SimpleNamespace(agent_type="critic"))
    collected = asyncio.run(agent_mod._collect_agent_messages(params))

    assert len(collected) == 1
    err = capsys.readouterr().err
    # Text line still rendered.
    assert "  ⎿ [critic] Let me check the file." in err
    # Tool-use line shows the actual file path, not the legacy placeholder.
    assert "  ⎿ [critic] Read(/repo/src/foo.py)\n" in err
    assert "Read(...)" not in err
