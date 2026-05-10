"""Prompt-cache dynamic-boundary marker.

Mirrors TS ``constants/prompts.ts:114-115``::

    export const SYSTEM_PROMPT_DYNAMIC_BOUNDARY =
      '__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__'

The literal is emitted as a system-prompt block separating globally-cacheable
identity/policy/tool sections from per-session sections that vary by user.
When ``shouldUseGlobalCacheScope()`` is true (firstParty provider, no MCP
tools), blocks BEFORE this marker can use ``scope: 'global'`` so two users
running the same Claude Code version share the prefix cache.

The marker is its own block (so it shows up in the wire payload of any
recorded request — making it easy to verify the boundary was emitted)
rather than embedded inside another block.
"""

from __future__ import annotations

__all__ = ["SYSTEM_PROMPT_DYNAMIC_BOUNDARY"]

SYSTEM_PROMPT_DYNAMIC_BOUNDARY: str = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"
