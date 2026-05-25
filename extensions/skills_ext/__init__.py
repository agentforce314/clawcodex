"""
Skills Extension Layer (skills_ext)

Extends upstream skills loader with clawcodex-specific functionality.
Follows the same pattern as tool_system_ext for consistency.

Architecture:
    src/skills/           - Layer 1: Upstream original code (read-only)
    src/skills_ext/       - Layer 2: clawcodex extension layer (new)

Extension components:
    - SkillRegistryExt: Wrapper for upstream loader with bundle support
    - bundles.py: Skill bundle definitions
    - agent_config.py: Per-agent skill configuration
    - paths.py: clawcodex-specific path resolution
    - hooks.py: Skill lifecycle callbacks
    - cache.py: Extension layer caching
    - bundled/: clawcodex-native bundled skills (POS converter, etc.)
"""

from .registry_ext import SkillRegistryExt, SkillRegistrationCallback

__all__ = [
    "SkillRegistryExt",
    "SkillRegistrationCallback",
]


def init_skills_ext() -> None:
    """Initialize skills_ext bundled skills.

    Registers clawcodex-native skills that are not part of upstream.
    Called by SkillRegistryExt when loading skills from clawscodex paths.
    """
    from .bundled import register_convert_pos_skill
    register_convert_pos_skill()