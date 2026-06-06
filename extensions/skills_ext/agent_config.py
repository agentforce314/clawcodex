from __future__ import annotations

"""
Agent Skill Configuration

Defines how skills are selected for an agent based on mode and bundles.
Used by SkillRegistryExt.get_skills_for_config() to filter skills.

Modes:
    - bare:  No skills (zero skill agent for pure reasoning)
    - default: Default bundle set
    - clawcodex: All clawcodex native skills
    - all: All available skills

Mirrors AgentToolConfig pattern from tool_system_ext.
"""

from dataclasses import dataclass, field
from typing import Literal

from .bundles import SKILL_BUNDLES, MODE_BUNDLES, ALL_BUNDLE_NAMES


# Type alias for clarity
AgentSkillConfigMode = Literal["bare", "default", "clawcodex", "all"]


@dataclass
class AgentSkillConfig:
    """
    Configuration for which skills an agent can access.

    Attributes:
        mode: Skill loading mode (bare/default/clawcodex/all)
        bundles: Explicit list of bundle names to load (overrides mode default)
        exclude: Skill names to exclude from the selected set
    """

    mode: AgentSkillConfigMode = "default"
    bundles: list[str] | None = None
    exclude: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate bundle names."""
        if self.bundles is not None:
            unknown = set(self.bundles) - set(ALL_BUNDLE_NAMES)
            if unknown:
                raise ValueError(f"unknown bundles: {sorted(unknown)}")

    def get_effective_bundle_names(self) -> list[str]:
        """Get the bundle names this config will use."""
        if self.bundles is not None:
            return self.bundles
        if self.mode == "bare":
            return []
        if self.mode == "all":
            return ALL_BUNDLE_NAMES
        return MODE_BUNDLES.get(self.mode, ["default"])


def load_skill_config(
    mode: AgentSkillConfigMode | None = None,
    bundles: list[str] | None = None,
    exclude: list[str] | None = None,
) -> AgentSkillConfig:
    """
    Factory function to create AgentSkillConfig with validation.

    Args:
        mode: Skill mode (bare/default/clawcodex/all), defaults to "default"
        bundles: Explicit bundle list, overrides mode's default bundles
        exclude: Skill names to exclude

    Returns:
        Validated AgentSkillConfig instance
    """
    return AgentSkillConfig(
        mode=mode or "default",
        bundles=bundles,
        exclude=exclude or [],
    )