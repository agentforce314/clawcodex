"""UTILS-1 — MCP server `instructions` rendering (getMcpInstructions port).

C2 moved the render OUT of the session-cached name-list section into the
REQUEST-scoped `_build_mcp_instructions_section` (the utils-critic's M1) —
these pin the split renderer.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.context_system.prompt_assembly import (
    _build_mcp_instructions_section,
    _build_mcp_section,
)


def _srv(name, instructions=None):
    return SimpleNamespace(name=name, instructions=instructions)


def test_instructions_surfaced_for_servers_that_have_them():
    # C2 split: names in the session section, instructions in the request one.
    names = _build_mcp_section([_srv("github", "Authenticate first."), _srv("fs")], use_cache=False)
    assert "# MCP Servers" in names.content and "- github" in names.content and "- fs" in names.content
    sec = _build_mcp_instructions_section([_srv("github", "Authenticate first."), _srv("fs")])
    c = sec.content
    assert "# MCP Server Instructions" in c
    assert "## github\nAuthenticate first." in c
    assert "## fs" not in c


def test_no_instructions_section_when_none_provided():
    assert _build_mcp_instructions_section([_srv("fs"), _srv("db", "")]) is None
    names = _build_mcp_section([_srv("fs"), _srv("db", "")], use_cache=False)
    assert "- fs" in names.content and "- db" in names.content


def test_whitespace_only_instructions_ignored():
    assert _build_mcp_instructions_section([_srv("x", "   \n  ")]) is None


def test_multiple_instruction_blocks_joined():
    sec = _build_mcp_instructions_section([_srv("a", "AAA"), _srv("b", "BBB")])
    c = sec.content
    assert "## a\nAAA" in c and "## b\nBBB" in c
