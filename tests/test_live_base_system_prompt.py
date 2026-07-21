"""Regression test for the live TUI/headless base-system-prompt fix.

Before the fix, the cutover's ``build_effective_system_prompt`` returned only
``{style}\n\n{context}`` (no base sections), so the live TUI/headless agent
received NO operating instructions. The fix makes it return the full base
prompt as a block list (mirroring the engine/REPL canonical path), with the
style appended and the workspace/git/CLAWCODEX.md context preserved.

See ``my-docs/get-parity-by-folder/live-base-system-prompt-gap-analysis.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.query.agent_loop_compat import build_effective_system_prompt
from src.tool_system.context import ToolContext


_SENTINEL_STYLE = "ZZZ_OUTPUT_STYLE_SENTINEL_marker_42"


@pytest.fixture(autouse=True)
def _isolate_prompt_cache():
    """build_effective_system_prompt populates the process-global prompt
    cache (intended in production). Clear it before+after each test here so
    this file never leaks cached sections into other tests (e.g.
    test_clear_system_prompt_sections asserts exact cache sizes)."""
    from src.context_system.prompt_assembly import get_system_prompt_cache

    get_system_prompt_cache().invalidate_all()
    yield
    get_system_prompt_cache().invalidate_all()


def _joined(blocks) -> str:
    assert isinstance(blocks, list), f"expected block list, got {type(blocks)}"
    assert all(isinstance(b, dict) and "text" in b for b in blocks), blocks
    return "\n".join(b.get("text", "") for b in blocks)


def test_returns_block_list_with_cache_control(tmp_path: Path):
    """The helper returns the block-list shape so query() engages caching."""
    ctx = ToolContext(workspace_root=tmp_path)
    blocks = build_effective_system_prompt(_SENTINEL_STYLE, ctx)
    assert isinstance(blocks, list) and len(blocks) >= 1
    # At least one block carries a cache_control marker (cached base prefix).
    assert any("cache_control" in b for b in blocks), (
        "no cache_control marker — caching not engaged"
    )


def test_includes_base_sections(tmp_path: Path):
    """The base operating instructions are present (the bug: they were absent)."""
    ctx = ToolContext(workspace_root=tmp_path)
    text = _joined(build_effective_system_prompt(_SENTINEL_STYLE, ctx))
    for marker in (
        "# Doing tasks",
        "# Executing actions with care",
        "# Using your tools",
        "# Tone and style",
        "# Environment",
    ):
        assert marker in text, f"base section missing: {marker!r}"


def test_names_clawcodex_data_dir(tmp_path: Path):
    """The live prompt tells the model where clawcodex keeps session history,
    so it stops falling back on the real Claude Code harness's ~/.claude when
    asked to inspect previous sessions."""
    ctx = ToolContext(workspace_root=tmp_path)
    text = _joined(build_effective_system_prompt(_SENTINEL_STYLE, ctx))
    assert "clawcodex data directory:" in text
    assert "NOT under ~/.claude" in text


def test_appends_the_resolved_style(tmp_path: Path):
    """The resolved output-style prompt is appended (style still applied)."""
    ctx = ToolContext(workspace_root=tmp_path)
    text = _joined(build_effective_system_prompt(_SENTINEL_STYLE, ctx))
    assert _SENTINEL_STYLE in text


def test_does_not_emit_prose_tool_docs(tmp_path: Path):
    """tools/tool_registry are NOT passed (mirrors engine.py:167) so no prose
    tool-docs section is emitted — tool schemas go via the API tools= param.
    The general '# Using your tools' guidance is still present (asserted above);
    here we guard against a per-tool prose docs block sneaking in."""
    ctx = ToolContext(workspace_root=tmp_path)
    text = _joined(build_effective_system_prompt(_SENTINEL_STYLE, ctx))
    # The prose tool-docs section header (_build_tool_docs_section emits
    # "# Available Tools" only when tools/tool_registry are passed). It must
    # be absent — distinct from "# Available Skills", which IS expected.
    assert "# Available Tools" not in text


def test_preserves_clawcodex_md_project_instructions(tmp_path: Path, monkeypatch):
    """CLAWCODEX.md must NOT be dropped (critic B1): it is kept via the trailing
    build_context_prompt block (## Project Instructions), since the base
    blocks' memory section is MEMORY.md auto-memory, not CLAWCODEX.md."""
    # Isolate HOME so global ~/.claude/CLAWCODEX.md doesn't mask the test file.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    marker = "PROJECT_CLAUDE_MD_SENTINEL_xyz"
    (tmp_path / "CLAWCODEX.md").write_text(f"# Project rules\n{marker}\n", encoding="utf-8")

    ctx = ToolContext(workspace_root=tmp_path)
    text = _joined(build_effective_system_prompt(_SENTINEL_STYLE, ctx))
    assert marker in text, "CLAWCODEX.md project instructions were dropped"


def test_provider_none_is_safe(tmp_path: Path):
    """provider/mcp_servers default to None (disables global cache scope) and
    the helper still produces a valid full prompt."""
    ctx = ToolContext(workspace_root=tmp_path)
    blocks = build_effective_system_prompt(
        _SENTINEL_STYLE, ctx, provider=None, mcp_servers=None,
    )
    text = _joined(blocks)
    assert "# Doing tasks" in text and _SENTINEL_STYLE in text
