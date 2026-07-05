"""UTILS-1 — MCP server `instructions` surfaced in the system prompt.

Port of getMcpInstructions (constants/prompts.ts:572-596). The MCP client
captures each connected server's InitializeResult `instructions`, but the
port previously listed only server NAMES — dropping server-authored guidance.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.context_system.prompt_assembly import _build_mcp_section


def _srv(name, instructions=None):
    return SimpleNamespace(name=name, instructions=instructions)


def test_instructions_surfaced_for_servers_that_have_them():
    sec = _build_mcp_section([_srv("github", "Authenticate first."), _srv("fs")], use_cache=False)
    c = sec.content
    assert "# MCP Servers" in c and "- github" in c and "- fs" in c
    assert "# MCP Server Instructions" in c
    assert "## github\nAuthenticate first." in c
    assert "## fs" not in c


def test_no_instructions_section_when_none_provided():
    sec = _build_mcp_section([_srv("fs"), _srv("db", "")], use_cache=False)
    assert "# MCP Server Instructions" not in sec.content
    assert "- fs" in sec.content and "- db" in sec.content


def test_whitespace_only_instructions_ignored():
    sec = _build_mcp_section([_srv("x", "   \n  ")], use_cache=False)
    assert "# MCP Server Instructions" not in sec.content


def test_multiple_instruction_blocks_joined():
    sec = _build_mcp_section([_srv("a", "AAA"), _srv("b", "BBB")], use_cache=False)
    c = sec.content
    assert "## a\nAAA" in c and "## b\nBBB" in c
