"""POS to Agent converter — transforms professional workflows into reusable Agents.

Three-layer mapping:
    POS (professional system)     → Agent
    workflow steps                → Skill
    SDK interfaces               → atomic tools

Architecture::

    SDK Spec + Requirements
         │
         ▼
    SdkParser ──────────────────► atomic_tools: list[str]
         │
         ▼
    SkillGrouper ────────────────► skills: list[SkillSpec]
         │
         ▼
    AgentBuilder ────────────────► agent: AgentDefinition
         │
         ▼
    Persistence / Registration
"""

from .sdk_parser import SdkParser, SdkMethod
from .skill_grouper import SkillGrouper, SkillSpec
from .agent_builder import AgentBuilder, AgentBuildResult
from .templates import AGENT_TEMPLATE, SKILL_TEMPLATE, MappingRule

__all__ = [
    "SdkParser",
    "SdkMethod",
    "SkillGrouper",
    "SkillSpec",
    "AgentBuilder",
    "AgentBuildResult",
    "AGENT_TEMPLATE",
    "SKILL_TEMPLATE",
    "MappingRule",
]