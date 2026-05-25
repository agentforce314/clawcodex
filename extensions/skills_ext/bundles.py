from __future__ import annotations

"""
Skill Bundle Definitions

Defines skill loading modes for agents:
- bare: No skills (pure reasoning agent)
- default: Default skill bundle
- clawcodex: All clawcodex native built-in skills
- all: All available skills

Mirrors the TOOL_BUNDLES pattern from tool_system_ext.
"""

from typing import Final

# Bundle definitions: bundle_name -> list of skill names
SKILL_BUNDLES: dict[str, list[str]] = {
    "default": [
        # Default skills available to all agents
        "git:commit",
        "git:push",
        "review-pr",
        "simplify",
        "debug",
    ],
    "clawcodex": [
        # All clawcodex native built-in skills
        "simplify",
        "debug",
        "loop",
        "verify-content",
        "pr-review",
        "code-review",
        "feature-dev",
        "dream",
        "keybindings-help",
        "update-config",
        "cron-list",
        "cron-delete",
        "loop",
        "simplify",
        "debug",
        "stuck",
        "ask",
        "convert-pos-to-agent",
    ],
}

# Mode to bundle names mapping
MODE_BUNDLES: dict[str, list[str]] = {
    "bare": [],
    "default": ["default"],
    "clawcodex": ["clawcodex"],
    "all": list(SKILL_BUNDLES.keys()),
}

# All available bundle names
ALL_BUNDLE_NAMES: list[str] = list(SKILL_BUNDLES.keys())


def get_bundle_skills(bundle_name: str) -> list[str]:
    """Get skill names for a bundle, returns empty list if bundle not found."""
    return list(SKILL_BUNDLES.get(bundle_name, []))


def get_all_bundle_skills() -> list[str]:
    """Get all skill names across all bundles (deduped)."""
    seen: set[str] = set()
    result: list[str] = []
    for skills in SKILL_BUNDLES.values():
        for s in skills:
            if s not in seen:
                seen.add(s)
                result.append(s)
    return result