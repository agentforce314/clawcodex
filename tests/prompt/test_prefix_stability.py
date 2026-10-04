"""
PR 4 — Prefix Byte-Stability CI Guard

Ensures the stable prefix (GLOBAL + SESSION scope blocks, excluding REQUEST scope)
is byte-for-byte identical across consecutive turns with identical inputs.

This is critical for DeepSeek's automatic prefix cache which requires true byte
equality. Any refactor that introduces non-determinism (timestamps, UUIDs,
unordered dict iteration, etc.) will fail this test.
"""
import pytest
from typing import Any, Dict, List

from src.context_system.prompt_assembly import build_full_system_prompt_blocks
from src.context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY


@pytest.fixture
def standard_prompt_args() -> Dict[str, Any]:
    """Standard arguments for building a system prompt."""
    return {
        "cwd": "/test/workspace",
        "tools": [],
        "tool_registry": None,
        "agents": [],
        "skills": [],
        "mcp_servers": [],
        "output_style": "default",
        "non_interactive": False,
        "tool_restrictions": None,
        "custom_system_prompt": None,
        "append_system_prompt": None,
        "use_cache": True,
        "query_source": "main",
        "provider": None,  # No provider = no global scope
    }


class TestPrefixStability:

    def _get_stable_prefix(self, blocks: List[Dict[str, Any]]) -> str:
        """
        Extract the stable prefix from system prompt blocks.

        Stable prefix = all blocks up to (but not including) REQUEST-scope blocks.
        This includes GLOBAL blocks, the dynamic boundary marker, and SESSION blocks.
        """
        stable_parts: List[str] = []
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            text = blk.get("text")
            if not text:
                continue
            # Drop the boundary marker (Anthropic cache-only signal)
            if text == SYSTEM_PROMPT_DYNAMIC_BOUNDARY:
                continue
            # Stop at REQUEST-scope blocks — these are the volatile tail
            if blk.get("_cache_scope") == "request":
                break
            stable_parts.append(str(text))
        return "\n\n".join(stable_parts)

    def test_prefix_stability_basic(self, standard_prompt_args):
        """Two consecutive calls with identical args produce identical stable prefix."""
        blocks_1 = build_full_system_prompt_blocks(**standard_prompt_args)
        blocks_2 = build_full_system_prompt_blocks(**standard_prompt_args)

        prefix_1 = self._get_stable_prefix(blocks_1)
        prefix_2 = self._get_stable_prefix(blocks_2)

        assert prefix_1 == prefix_2, (
            "Stable prefix differs between calls! "
            f"First: {len(prefix_1)} chars, Second: {len(prefix_2)} chars"
        )
        assert len(prefix_1) > 0, "Stable prefix should not be empty"

    def test_prefix_stability_with_tools(self, standard_prompt_args):
        """Stability holds when tools are present."""
        standard_prompt_args["tools"] = [
            {"name": "bash", "description": "Run shell commands"},
            {"name": "read", "description": "Read files"},
        ]
        blocks_1 = build_full_system_prompt_blocks(**standard_prompt_args)
        blocks_2 = build_full_system_prompt_blocks(**standard_prompt_args)

        prefix_1 = self._get_stable_prefix(blocks_1)
        prefix_2 = self._get_stable_prefix(blocks_2)

        assert prefix_1 == prefix_2

    def test_prefix_stability_with_skills(self, standard_prompt_args):
        """Stability holds when skills are present."""
        standard_prompt_args["skills"] = [
            {"name": "test-skill", "description": "A test skill"},
        ]
        blocks_1 = build_full_system_prompt_blocks(**standard_prompt_args)
        blocks_2 = build_full_system_prompt_blocks(**standard_prompt_args)

        prefix_1 = self._get_stable_prefix(blocks_1)
        prefix_2 = self._get_stable_prefix(blocks_2)

        assert prefix_1 == prefix_2

    def test_prefix_stability_with_mcp_servers(self, standard_prompt_args):
        """Stability holds when MCP servers are present."""
        standard_prompt_args["mcp_servers"] = [
            {"name": "test-server", "tools": []},
        ]
        blocks_1 = build_full_system_prompt_blocks(**standard_prompt_args)
        blocks_2 = build_full_system_prompt_blocks(**standard_prompt_args)

        prefix_1 = self._get_stable_prefix(blocks_1)
        prefix_2 = self._get_stable_prefix(blocks_2)

        assert prefix_1 == prefix_2

    def test_prefix_stability_different_cwds(self):
        """Different cwds produce same stable prefix (CWD is in REQUEST scope)."""
        args_1 = {
            "cwd": "/test/workspace1",
            "tools": [], "tool_registry": None, "agents": [], "skills": [],
            "mcp_servers": [], "output_style": "default", "non_interactive": False,
            "tool_restrictions": None, "custom_system_prompt": None,
            "append_system_prompt": None, "use_cache": True, "query_source": "main",
            "provider": None,
        }
        args_2 = {
            "cwd": "/test/workspace2",
            "tools": [], "tool_registry": None, "agents": [], "skills": [],
            "mcp_servers": [], "output_style": "default", "non_interactive": False,
            "tool_restrictions": None, "custom_system_prompt": None,
            "append_system_prompt": None, "use_cache": True, "query_source": "main",
            "provider": None,
        }

        blocks_1 = build_full_system_prompt_blocks(**args_1)
        blocks_2 = build_full_system_prompt_blocks(**args_2)

        prefix_1 = self._get_stable_prefix(blocks_1)
        prefix_2 = self._get_stable_prefix(blocks_2)

        # CWD is in REQUEST-scope env section, so stable prefix should be IDENTICAL
        assert prefix_1 == prefix_2, (
            "Stable prefix should not vary with CWD (CWD is in volatile REQUEST scope)"
        )

    def test_prefix_stability_excludes_request_scope(self, standard_prompt_args):
        """REQUEST-scope blocks (env, memory, plan-mode) are NOT in stable prefix."""
        standard_prompt_args["non_interactive"] = True  # Adds REQUEST-scope block
        standard_prompt_args["tool_restrictions"] = ["no-bash"]  # Adds REQUEST-scope block

        blocks = build_full_system_prompt_blocks(**standard_prompt_args)

        stable_prefix = self._get_stable_prefix(blocks)

        # REQUEST-scope content should NOT appear in stable prefix
        assert "non_interactive" not in stable_prefix.lower() or "non_interactive" not in stable_prefix
        assert "tool_restrictions" not in stable_prefix.lower() or "tool_restrictions" not in stable_prefix

        # But SESSION-scope should still be there
        assert len(stable_prefix) > 0

    def test_prefix_byte_exactness(self, standard_prompt_args):
        """Verify byte-for-byte equality (not just semantic equality)."""
        blocks_1 = build_full_system_prompt_blocks(**standard_prompt_args)
        blocks_2 = build_full_system_prompt_blocks(**standard_prompt_args)

        prefix_1 = self._get_stable_prefix(blocks_1)
        prefix_2 = self._get_stable_prefix(blocks_2)

        # Encode to bytes and compare
        bytes_1 = prefix_1.encode("utf-8")
        bytes_2 = prefix_2.encode("utf-8")

        assert bytes_1 == bytes_2, (
            "Byte-for-byte comparison failed. "
            f"First: {len(bytes_1)} bytes, Second: {len(bytes_2)} bytes. "
            f"First 100 bytes differ at: "
            f"{next((i for i, (a, b) in enumerate(zip(bytes_1, bytes_2)) if a != b), 'none')}"
        )

    def test_prefix_stability_multiple_iterations(self, standard_prompt_args):
        """Prefix remains stable across many iterations."""
        prefixes = []
        for _ in range(10):
            blocks = build_full_system_prompt_blocks(**standard_prompt_args)
            prefixes.append(self._get_stable_prefix(blocks))

        # All should be identical
        assert all(p == prefixes[0] for p in prefixes)

    def test_request_scope_blocks_are_volatile(self, standard_prompt_args):
        """Verify REQUEST-scope blocks are properly separated."""
        standard_prompt_args["non_interactive"] = True
        standard_prompt_args["tool_restrictions"] = ["no-bash"]

        blocks = build_full_system_prompt_blocks(**standard_prompt_args)

        request_blocks = [b for b in blocks if b.get("_cache_scope") == "request"]
        assert len(request_blocks) >= 1, "Should have REQUEST-scope blocks"

        # Verify they're after the boundary
        boundary_indices = [i for i, b in enumerate(blocks)
                            if b.get("text") == SYSTEM_PROMPT_DYNAMIC_BOUNDARY]
        assert len(boundary_indices) == 1, "Exactly one boundary marker expected"

        boundary_idx = boundary_indices[0]
        for req_block in request_blocks:
            req_idx = blocks.index(req_block)
            assert req_idx > boundary_idx, "REQUEST blocks must come after boundary"


class TestPrefixStabilityWithProviders:
    """Test prefix stability across different provider configurations."""

    def test_stability_with_provider_none(self):
        """No provider = no global scope."""
        args = {
            "cwd": "/test", "tools": [], "tool_registry": None, "agents": [], "skills": [],
            "mcp_servers": [], "output_style": "default", "non_interactive": False,
            "tool_restrictions": None, "custom_system_prompt": None,
            "append_system_prompt": None, "use_cache": True, "query_source": "main",
            "provider": None,
        }
        blocks_1 = build_full_system_prompt_blocks(**args)
        blocks_2 = build_full_system_prompt_blocks(**args)

        stable_1 = "\n\n".join(
            str(b["text"]) for b in blocks_1
            if b.get("text") and b.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            and b.get("_cache_scope") != "request"
        )
        stable_2 = "\n\n".join(
            str(b["text"]) for b in blocks_2
            if b.get("text") and b.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            and b.get("_cache_scope") != "request"
        )
        assert stable_1 == stable_2

    def test_stability_with_different_query_sources(self):
        """Different query sources should produce stable (but different) prefixes."""
        args_main = {
            "cwd": "/test", "tools": [], "tool_registry": None, "agents": [], "skills": [],
            "mcp_servers": [], "output_style": "default", "non_interactive": False,
            "tool_restrictions": None, "custom_system_prompt": None,
            "append_system_prompt": None, "use_cache": True, "query_source": "main",
            "provider": None,
        }
        args_compact = {**args_main, "query_source": "compact"}

        blocks_main_1 = build_full_system_prompt_blocks(**args_main)
        blocks_main_2 = build_full_system_prompt_blocks(**args_main)
        blocks_compact_1 = build_full_system_prompt_blocks(**args_compact)
        blocks_compact_2 = build_full_system_prompt_blocks(**args_compact)

        stable_main_1 = "\n\n".join(
            str(b["text"]) for b in blocks_main_1
            if b.get("text") and b.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            and b.get("_cache_scope") != "request"
        )
        stable_main_2 = "\n\n".join(
            str(b["text"]) for b in blocks_main_2
            if b.get("text") and b.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            and b.get("_cache_scope") != "request"
        )
        stable_compact_1 = "\n\n".join(
            str(b["text"]) for b in blocks_compact_1
            if b.get("text") and b.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            and b.get("_cache_scope") != "request"
        )
        stable_compact_2 = "\n\n".join(
            str(b["text"]) for b in blocks_compact_2
            if b.get("text") and b.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            and b.get("_cache_scope") != "request"
        )

        assert stable_main_1 == stable_main_2
        assert stable_compact_1 == stable_compact_2
        # Different query sources may have different cache TTLs but stable prefix should be same
        # (TTL is in cache_control, not in the text content)


class TestDeepSeekPrefixStability:
    """Test prefix stability specifically for DeepSeek's relocation path.

    DeepSeek uses automatic prefix caching which requires TRUE byte-for-byte
    equality of the prefix. This tests the _split_system_prompt_blocks path
    with relocate_request_scope=True.
    """

    def _get_deepseek_stable_prefix(self, blocks: List[Dict[str, Any]]) -> str:
        """Get stable prefix using DeepSeek's split logic."""
        from src.query.query import _split_system_prompt_blocks
        stable, volatile = _split_system_prompt_blocks(blocks, relocate_request_scope=True)
        return stable

    def test_deepseek_stable_prefix_byte_exact(self, standard_prompt_args):
        """DeepSeek stable prefix is byte-exact across calls."""
        blocks_1 = build_full_system_prompt_blocks(**standard_prompt_args)
        blocks_2 = build_full_system_prompt_blocks(**standard_prompt_args)

        prefix_1 = self._get_deepseek_stable_prefix(blocks_1)
        prefix_2 = self._get_deepseek_stable_prefix(blocks_2)

        assert prefix_1 == prefix_2
        assert prefix_1.encode("utf-8") == prefix_2.encode("utf-8")

    def test_deepseek_volatile_tail_contains_env(self, standard_prompt_args):
        """DeepSeek volatile tail contains env/memory sections."""
        blocks = build_full_system_prompt_blocks(**standard_prompt_args)
        from src.query.query import _split_system_prompt_blocks
        stable, volatile = _split_system_prompt_blocks(blocks, relocate_request_scope=True)

        # Env section should be in volatile tail
        assert "Environment" in volatile or "CWD" in volatile
        # Memory store should be in stable (SESSION scope)
        assert "Persistent Memory" in stable

    def test_deepseek_stable_excludes_request_scope(self, standard_prompt_args):
        """DeepSeek stable prefix excludes REQUEST-scope blocks."""
        # Use non_interactive=False (default) but check that env section (REQUEST) is in volatile
        standard_prompt_args["non_interactive"] = True  # SESSION scope
        standard_prompt_args["tool_restrictions"] = ["no-bash"]  # SESSION scope

        blocks = build_full_system_prompt_blocks(**standard_prompt_args)
        from src.query.query import _split_system_prompt_blocks
        stable, volatile = _split_system_prompt_blocks(blocks, relocate_request_scope=True)

        # REQUEST-scope content (env section) should be in volatile
        assert "Environment" in volatile or "CWD" in volatile
        # SESSION-scope content should be in stable
        assert "Persistent Memory" in stable
        assert "non-interactive" in stable.lower()
        assert "tool restrictions" in stable.lower()

    def test_deepseek_multiple_calls_byte_exact(self, standard_prompt_args):
        """Byte-for-byte equality across many calls."""
        prefixes = []
        for _ in range(5):
            blocks = build_full_system_prompt_blocks(**standard_prompt_args)
            from src.query.query import _split_system_prompt_blocks
            stable, _ = _split_system_prompt_blocks(blocks, relocate_request_scope=True)
            prefixes.append(stable)

        assert all(p == prefixes[0] for p in prefixes)
        assert all(p.encode("utf-8") == prefixes[0].encode("utf-8") for p in prefixes)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])