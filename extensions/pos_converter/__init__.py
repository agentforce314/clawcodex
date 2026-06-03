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
from .skill_grouper import SkillGrouper, SkillSpec, GroupStrategy, group_source_components
from .agent_builder import AgentBuilder, AgentBuildResult
from .templates import AGENT_TEMPLATE, SKILL_TEMPLATE, MappingRule, AGENT_MD_TEMPLATE, SKILL_MD_TEMPLATE_JINJA, OVERVIEW_AGENT_TEMPLATE
from .source_parser import SourceCodeParser, SourceComponent, SourceOperation, ParamSpec
from .agent_md_writer import AgentMarkdownWriter, AgentComponentInfo, WorkflowStage
from .default_agent import resolve_default_agent, resolve_agent_by_type

__all__ = [
    "SdkParser", "SdkMethod",
    "SkillGrouper", "SkillSpec", "GroupStrategy", "group_source_components",
    "AgentBuilder", "AgentBuildResult",
    "AGENT_TEMPLATE", "SKILL_TEMPLATE", "MappingRule",
    "AGENT_MD_TEMPLATE", "SKILL_MD_TEMPLATE_JINJA", "OVERVIEW_AGENT_TEMPLATE",
    "SourceCodeParser", "SourceComponent", "SourceOperation", "ParamSpec",
    "AgentMarkdownWriter", "AgentComponentInfo", "WorkflowStage",
    "resolve_default_agent", "resolve_agent_by_type",
]