"""
python-frontmatter adapter for ClawCodex frontmatter parsing.

This module provides a python-frontmatter-based parser that can replace
the manual YAML frontmatter parsing in src/skills/frontmatter.py.

Architecture:
    src/skills/frontmatter.py (existing parse_frontmatter API)
        ↓
    src/skills/_frontmatter_adapter.py (This module - python-frontmatter backend)
        ↓
    python-frontmatter (Open source dependency)

Switch:
    CLAW_USE_FRONTMATTER_LIB=true (default) - use python-frontmatter
    CLAW_USE_FRONTMATTER_LIB=false - fallback to manual YAML parsing
"""

from __future__ import annotations

from clawcodex_ext.capabilities import AdapterRegistry, env_switch, dependency_available
import logging
import os
from dataclasses import dataclass
from typing import Any

from src.skills.frontmatter import FrontmatterParseResult

logger = logging.getLogger(__name__)

# Switching mechanism: control via environment variable
_USE_FRONTMATTER_LIB = env_switch("CLAW_USE_FRONTMATTER_LIB")

# python-frontmatter availability
_FRONTMATTER_AVAILABLE = dependency_available("frontmatter")
if _FRONTMATTER_AVAILABLE:
    import frontmatter
else:
    frontmatter = None


def is_frontmatter_available() -> bool:
    """Check if python-frontmatter is available."""
    return _FRONTMATTER_AVAILABLE


def parse_frontmatter_with_library(markdown: str) -> FrontmatterParseResult:
    """
    Parse frontmatter using python-frontmatter library.

    This function bridges the existing parse_frontmatter API with
    the python-frontmatter library for better nested structure support.
    """
    if not _FRONTMATTER_AVAILABLE:
        return _fallback_parse(markdown)

    if not markdown:
        return FrontmatterParseResult(frontmatter={}, body=markdown or "")

    try:
        post = frontmatter.loads(markdown)
        metadata = post.metadata if isinstance(post.metadata, dict) else {}
        return FrontmatterParseResult(frontmatter=metadata, body=post.content)
    except Exception as e:
        logger.debug("python-frontmatter parse failed: %s, falling back to manual", e)
        return _fallback_parse(markdown)


def _fallback_parse(markdown: str) -> FrontmatterParseResult:
    """
    Fallback manual frontmatter parsing.

    Used when python-frontmatter is not available or fails to parse.
    This is essentially the same logic as the original src/skills/frontmatter.py.
    """
    lines = markdown.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return FrontmatterParseResult(frontmatter={}, body=markdown)

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return FrontmatterParseResult(frontmatter={}, body=markdown)

    fm_raw = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])

    if not fm_raw.strip():
        return FrontmatterParseResult(frontmatter={}, body=body)

    try:
        import yaml
        parsed = yaml.safe_load(fm_raw)
    except Exception as e:
        logger.debug("fallback YAML parse failed: %s", e)
        return FrontmatterParseResult(frontmatter={}, body=body)

    if parsed is None:
        return FrontmatterParseResult(frontmatter={}, body=body)
    if not isinstance(parsed, dict):
        return FrontmatterParseResult(frontmatter={}, body=body)

    return FrontmatterParseResult(frontmatter=parsed, body=body)


# Module-level registration (no class in this adapter)
AdapterRegistry.register("frontmatter", env_var="CLAW_USE_FRONTMATTER_LIB", dependency="frontmatter")(
    type("FrontmatterAdapter", (), {"is_available": staticmethod(is_frontmatter_available)})
)