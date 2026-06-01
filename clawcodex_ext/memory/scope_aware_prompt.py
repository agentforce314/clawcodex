"""Scope-aware memory prompt builder — F-13 memory scope isolation.

This module sits entirely in ``clawcodex_ext/`` and relies on the existing
``src.memdir.load_memory_prompts()`` interface. No changes to ``src/memdir/``
are required.

The only integration points needed in ``src/`` are thin forwarding seams
(see ``src/context_system/prompt_assembly.py``):
1. ``build_full_system_prompt(memory_scopes=...)``
2. ``build_full_system_prompt_blocks(memory_scopes=...)``
3. ``_build_memory_section(memory_scopes=...)``
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# Mirror of src/agent/parse_agent_markdown.VALID_MEMORY_SCOPES.
# Kept here so the ext module does not import from src for validation.
VALID_MEMORY_SCOPES: frozenset[str] = frozenset({"user", "project", "local"})


def _validate_scopes(memory_scopes: Iterable[str]) -> list[str]:
    """Filter ``memory_scopes`` to known-valid values.

    Logs a warning for unknown scopes so the user can fix agent definitions,
    but does **not** raise — unrecognised scopes degrade gracefully.
    """
    validated: list[str] = []
    for scope in memory_scopes:
        s = str(scope).strip()
        if s in VALID_MEMORY_SCOPES:
            validated.append(s)
        else:
            logger.warning(
                "Unknown memory scope %r — must be one of %s; skipping",
                s,
                ", ".join(sorted(VALID_MEMORY_SCOPES)),
            )
    return validated


def build_scope_aware_memory_prompt(
    memory_scopes: list[str] | None = None,
) -> str | None:
    """Build a combined memory prompt for the given memory scopes.

    Args:
        memory_scopes:  List of scope names (``"user"``, ``"project"``,
                        ``"local"``).  ``None`` or an empty list returns
                        ``None`` (no-op / no-op path).

    Returns:
        A single concatenated memory-prompt string, or ``None`` if
        auto-memory is disabled, no scopes were given, or no prompt could
        be loaded for any scope.
    """
    if not memory_scopes:
        return None

    validated = _validate_scopes(memory_scopes)
    if not validated:
        return None

    # Lazy import: ``src.memdir`` may not be importable in all contexts
    # (e.g. CLI subcommands that don't need the full agent loop).
    try:
        from src.memdir import load_memory_prompts
    except ImportError:
        logger.debug("src.memdir not available — skipping scope-aware memory")
        return None

    try:
        prompts = load_memory_prompts(memory_scopes=validated)
    except Exception:
        logger.exception("Failed to load scope-aware memory prompts")
        return None

    if not prompts:
        return None

    # Join multiple scope prompts into one block, separated by a blank line.
    # Each individual prompt already has its own heading and structure.
    combined = "\n\n".join(prompts)
    return combined
