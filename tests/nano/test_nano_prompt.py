"""Nano system prompt: pi's shape, and none of the maximal sections."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.nano.prompt import (
    build_nano_prompt_blocks,
    build_nano_prompt_text,
)
from src.nano.state import set_nano_mode
from src.query.agent_loop_compat import build_effective_system_prompt

# Section markers of the maximal prompt that must never appear in nano.
_MAXIMAL_MARKERS = (
    "# auto memory",
    "# Doing tasks",
    "# Persistent Memory",
    "# Available Tools",
    "# Tone and style",
    "# Communicating with the user",
    "TaskCreate",
    "<available-deferred-tools>",
)


def test_base_prompt_is_small_and_clean(no_skills, tmp_path):
    text = build_nano_prompt_text(cwd=str(tmp_path), non_interactive=True)
    assert "nano mode" in text
    assert "Available tools:" in text
    for name in ("Read", "Bash", "Edit", "Write", "Grep", "Glob"):
        assert f"- {name}: " in text
    assert "running non-interactively" in text
    for marker in _MAXIMAL_MARKERS:
        assert marker not in text
    # pi territory: a few hundred tokens, not a few thousand.
    assert len(text) // 4 < 1000


def test_tool_listing_tracks_registry_filter(no_skills, tmp_path):
    text = build_nano_prompt_text(
        cwd=str(tmp_path), tool_names=("Read", "Bash", "Edit", "Write"),
    )
    assert "- Grep: " not in text and "- Glob: " not in text
    assert "- Read: " in text


def test_blocks_all_session_scope_with_one_trailing_cache_marker(
    no_skills, no_project_context, tmp_path
):
    blocks = build_nano_prompt_blocks(
        cwd=str(tmp_path), workspace_root=str(tmp_path), query_source="sdk",
    )
    assert all(b["_cache_scope"] == "session" for b in blocks)
    assert "cache_control" in blocks[-1]
    assert all("cache_control" not in b for b in blocks[:-1])


def test_agents_md_fallback_when_no_clawcodex_md(
    no_skills, no_project_context, tmp_path
):
    (tmp_path / "AGENTS.md").write_text("Always run ./test.sh before done.")
    blocks = build_nano_prompt_blocks(
        cwd=str(tmp_path), workspace_root=str(tmp_path),
    )
    joined = "\n".join(b["text"] for b in blocks)
    assert "<project_instructions" in joined
    assert "Always run ./test.sh before done." in joined


def test_clawcodex_md_wins_over_fallback(no_skills, tmp_path, monkeypatch):
    import src.context_system as context_system

    monkeypatch.setattr(
        context_system, "build_context_prompt_parts",
        lambda root, cwd=None: ("SNAPSHOT", "## Project Instructions\nfrom-clawcodex-md"),
    )
    (tmp_path / "AGENTS.md").write_text("fallback-should-not-appear")
    blocks = build_nano_prompt_blocks(
        cwd=str(tmp_path), workspace_root=str(tmp_path),
    )
    joined = "\n".join(b["text"] for b in blocks)
    assert "from-clawcodex-md" in joined
    assert "fallback-should-not-appear" not in joined
    # The volatile git/workspace snapshot half is deliberately dropped.
    assert "SNAPSHOT" not in joined


def test_build_effective_system_prompt_nano_branch(
    no_skills, no_project_context, tmp_path
):
    set_nano_mode(True)
    tool_context = SimpleNamespace(
        cwd=str(tmp_path),
        workspace_root=str(tmp_path),
        options=SimpleNamespace(is_non_interactive_session=True),
    )
    blocks = build_effective_system_prompt(
        "STYLE-LINE", tool_context, provider=None, query_source="sdk",
        nano_tool_names=("Read", "Bash", "Edit", "Write", "Grep", "Glob"),
    )
    joined = "\n".join(b["text"] for b in blocks)
    assert "nano mode" in joined
    assert "STYLE-LINE" in joined
    assert "running non-interactively" in joined
    for marker in _MAXIMAL_MARKERS:
        assert marker not in joined


@pytest.mark.parametrize("model,visible", [("claude-sonnet-4-6", False), ("deepseek-v4-pro", True)])
def test_nano_prompt_only_lists_vision_tool_for_text_models(
    no_skills, no_project_context, tmp_path, model, visible,
):
    set_nano_mode(True)
    tool_context = SimpleNamespace(cwd=tmp_path, workspace_root=tmp_path)
    blocks = build_effective_system_prompt(
        "", tool_context, provider=SimpleNamespace(model=model),
        nano_tool_names=("Read", "Bash", "vision_analyze"),
    )
    assert ("- vision_analyze:" in "\n".join(block["text"] for block in blocks)) is visible


def test_default_path_unchanged_when_nano_off(no_skills, tmp_path):
    tool_context = SimpleNamespace(
        cwd=str(tmp_path),
        workspace_root=str(tmp_path),
        options=SimpleNamespace(is_non_interactive_session=True),
    )
    blocks = build_effective_system_prompt(
        "", tool_context, provider=None, query_source="sdk",
    )
    joined = "\n".join(b["text"] for b in blocks)
    # The maximal prompt keeps its signature sections.
    assert "# Doing tasks" in joined
    assert "nano mode" not in joined
