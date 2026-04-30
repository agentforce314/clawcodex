"""YAML frontmatter parser for SKILL.md / agent / output-style files.

Replaces the prior homegrown YAML-subset parser (top-level scalars + list
forms only) with ``yaml.safe_load`` so nested structures used by the TS
port — most importantly ``hooks:`` and ``shell:`` blocks — round-trip
intact. The public ``parse_frontmatter`` API is preserved.

Structure of an accepted file::

    ---
    description: Some skill
    allowed-tools:
      - Bash(git status:*)
      - Read
    hooks:
      PostToolUse:
        - matcher: Write
          hooks:
            - type: command
              command: ./scripts/format.sh
    ---
    <markdown body>

Behavior:
- A missing or malformed frontmatter block returns ``frontmatter={}``
  with the original markdown intact as ``body``. We never raise; a bad
  ``---`` fence should not block-load other skills.
- Empty frontmatter (``---\\n---``) returns ``{}`` and the body that
  follows.
- ``yaml.safe_load`` is used (not ``load``) so arbitrary Python tag
  resolution is disabled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — yaml is a hard runtime dep
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrontmatterParseResult:
    frontmatter: Dict[str, Any]
    body: str


def parse_frontmatter(markdown: str) -> FrontmatterParseResult:
    """Split ``---``-fenced frontmatter from a markdown document.

    Returns a ``FrontmatterParseResult`` with the parsed frontmatter
    dict and the remaining body. Files without a leading ``---`` fence
    return an empty dict and the original markdown as the body.
    """
    if not markdown:
        return FrontmatterParseResult(frontmatter={}, body=markdown or "")

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
    body = "\n".join(lines[end_idx + 1 :])

    if not fm_raw.strip():
        return FrontmatterParseResult(frontmatter={}, body=body)

    if yaml is None:
        # Defensive — ``pyproject.toml`` declares PyYAML as a hard
        # dependency, so this branch only runs in degraded installs.
        logger.warning(
            "PyYAML not available; frontmatter parser cannot read nested "
            "structures (hooks / shell will be silently dropped). "
            "Install PyYAML to enable full parsing."
        )
        return FrontmatterParseResult(frontmatter={}, body=body)

    try:
        parsed = yaml.safe_load(fm_raw)
    except yaml.YAMLError as exc:
        logger.debug("frontmatter YAML parse failed: %s", exc)
        return FrontmatterParseResult(frontmatter={}, body=body)

    if parsed is None:
        return FrontmatterParseResult(frontmatter={}, body=body)
    if not isinstance(parsed, dict):
        # Top-level scalar / list — not a frontmatter shape we recognize.
        logger.debug(
            "frontmatter parsed as %s, expected dict; ignoring",
            type(parsed).__name__,
        )
        return FrontmatterParseResult(frontmatter={}, body=body)

    return FrontmatterParseResult(frontmatter=parsed, body=body)
