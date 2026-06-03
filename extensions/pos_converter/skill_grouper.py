"""SkillGrouper — groups atomic tools into business-level Skills.

Uses LLM-assisted grouping to cluster related tools by business logic.
Falls back to static rules (from MappingRule config) when LLM is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .sdk_parser import SdkMethod, SdkParser
from enum import Enum
from .source_parser import SourceComponent, SourceOperation


class GroupStrategy(Enum):
    KEYWORD_MATCH = "keyword_match"
    COMPONENT_GROUP = "component_group"
    IO_RELATION = "io_relation"
    LLM_SEMANTIC = "llm_semantic"


@dataclass
class SkillSpec:
    """Specification for a Skill derived from grouped SDK methods."""
    name: str
    description: str
    allowed_tools: list[str] = field(default_factory=list)
    argument_names: list[str] = field(default_factory=list)
    when_to_use: str | None = None
    version: str | None = None
    model: str | None = None


@dataclass
class MappingRule:
    """A mapping rule: SDK method pattern → tool name."""
    method_pattern: str
    tool_name: str
    skill_name: str
    description: str = ""


# Default static rules for common SDK patterns
DEFAULT_MAPPING_RULES: list[MappingRule] = [
    MappingRule("docker_build", "docker_build", "build_image", "Build Docker image"),
    MappingRule("docker_tag", "docker_tag", "build_image", "Tag Docker image"),
    MappingRule("docker_push", "docker_push", "build_image", "Push Docker image"),
    MappingRule("k8s_apply", "k8s_apply", "deploy_service", "Apply Kubernetes manifest"),
    MappingRule("k8s_delete", "k8s_delete", "deploy_service", "Delete Kubernetes resource"),
    MappingRule("k8s_get", "k8s_get", "deploy_service", "Get Kubernetes resource"),
    MappingRule("health_check", "health_check", "deploy_service", "Check service health"),
    MappingRule("rollback", "rollback", "deploy_service", "Rollback deployment"),
    MappingRule("slack_send", "slack_send", "notify_team", "Send Slack notification"),
    MappingRule("email_send", "email_send", "notify_team", "Send email notification"),
    MappingRule("s3_upload", "s3_upload", "upload_artifact", "Upload to S3"),
    MappingRule("s3_download", "s3_download", "upload_artifact", "Download from S3"),
    MappingRule("spark_submit", "spark_submit", "run_spark", "Submit Spark job"),
]


@dataclass
class SkillGrouper:
    """Group atomic SDK methods into Skills based on business logic.

    Uses MappingRule config for static grouping when LLM is not available.
    The group method accepts a requirements hint that can be used by LLM
    to determine which tools belong together.
    """

    def __init__(
        self,
        methods: list[SdkMethod],
        *,
        mapping_rules: list[MappingRule] | None = None,
        strategy: GroupStrategy | list[GroupStrategy] | None = None,
        source_components: list[SourceComponent] | None = None,
    ) -> None:
        self._methods = methods
        self._rules = mapping_rules or DEFAULT_MAPPING_RULES
        self._strategy = strategy
        self._source_components = source_components or []
        self._grouped: list[SkillSpec] | None = None

    def group(self, requirements: str = "") -> list[SkillSpec]:
        """Group methods into Skills.

        Args:
            requirements: Business requirements hint (e.g., "CI/CD pipeline",
                "data processing"). Passed to LLM when available for smarter
                grouping. Falls back to static MappingRule matching.
        """
        if self._grouped is not None:
            return self._grouped

        if self._strategy is None:
            self._grouped = self._static_group()
        elif isinstance(self._strategy, list):
            if GroupStrategy.COMPONENT_GROUP in self._strategy:
                self._grouped = self._component_group()
            elif GroupStrategy.IO_RELATION in self._strategy:
                self._grouped = self._io_relation_group()
            else:
                self._grouped = self._static_group()
        elif self._strategy == GroupStrategy.KEYWORD_MATCH:
            self._grouped = self._static_group()
        elif self._strategy == GroupStrategy.COMPONENT_GROUP:
            self._grouped = self._component_group()
        elif self._strategy == GroupStrategy.IO_RELATION:
            self._grouped = self._io_relation_group()
        elif self._strategy == GroupStrategy.LLM_SEMANTIC:
            self._grouped = self._group_with_llm(requirements)
        else:
            self._grouped = self._static_group()

        return self._grouped

    def _static_group(self) -> list[SkillSpec]:
        """Group tools using static MappingRule patterns."""
        skill_map: dict[str, SkillSpec] = {}
        unmatched: list[SdkMethod] = []

        for method in self._methods:
            matched = False
            for rule in self._rules:
                if rule.method_pattern in method.name:
                    if rule.skill_name not in skill_map:
                        skill_map[rule.skill_name] = SkillSpec(
                            name=rule.skill_name,
                            description=rule.description or f"Skill: {rule.skill_name}",
                            allowed_tools=[],
                        )
                    skill = skill_map[rule.skill_name]
                    if method.name not in skill.allowed_tools:
                        skill.allowed_tools.append(method.name)
                    if method.parameters:
                        skill.argument_names.extend(method.parameters)
                    matched = True
                    break
            if not matched:
                unmatched.append(method)

        # Put unmatched tools in a default skill
        if unmatched:
            skill_map["_unmatched"] = SkillSpec(
                name="sdk_utility",
                description="SDK utility methods",
                allowed_tools=[m.name for m in unmatched],
                argument_names=[],
            )

        return list(skill_map.values())

    def _component_group(self) -> list[SkillSpec]:
        """Group by SourceComponent. Each component becomes a SkillSpec.
        Operations map to allowed_tools.
        """
        if not self._source_components:
            return self._static_group()

        skills: list[SkillSpec] = []
        for component in self._source_components:
            tools = [op.name for op in component.operations]
            skills.append(
                SkillSpec(
                    name=component.name,
                    description=component.description or f"Component: {component.name}",
                    allowed_tools=tools,
                )
            )
        return skills

    def _io_relation_group(self) -> list[SkillSpec]:
        """Group operations sharing similar parameter types across components."""
        if not self._source_components:
            return self._static_group()

        type_groups: dict[str, SkillSpec] = {}
        for component in self._source_components:
            for op in component.operations:
                param_types = tuple(sorted(p.type_hint or "Any" for p in op.parameters)) if op.parameters else ("none",)
                group_key = "_".join(param_types) if param_types else "none"
                if group_key not in type_groups:
                    type_groups[group_key] = SkillSpec(
                        name=f"io_group_{len(type_groups) + 1}",
                        description=f"Operations with parameter types: {', '.join(param_types)}",
                        allowed_tools=[],
                    )
                if op.name not in type_groups[group_key].allowed_tools:
                    type_groups[group_key].allowed_tools.append(op.name)
        return list(type_groups.values())

    def _group_with_llm(self, requirements: str) -> list[SkillSpec]:
        """TODO: LLM-based grouping placeholder. Falls back to static grouping."""
        # TODO: wire in LLM tool call when available
        return self._static_group()

    def group_with_llm(self, requirements: str) -> list[SkillSpec]:
        """Group methods using LLM for smarter business-logic grouping.

        This is the LLM-assisted path. When LLM is unavailable, falls back
        to static grouping. The LLM is called via a separate tool (not in
        this module) to avoid circular imports.

        The prompt sent to LLM would be:
            Given these SDK methods: {methods}
            And business requirements: {requirements}
            Group them into Skills with names and descriptions.
            Return JSON: {{"skills": [{{"name": "...", "description": "...", "tools": [...]}}]}}
        """
        # TODO: wire in LLM tool call when available
        return self._static_group()


@dataclass
class GroupResult:
    """Result of skill grouping operation."""
    skills: list[SkillSpec]
    unmatched_tools: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def group_into_skills(
    methods: list[SdkMethod],
    requirements: str = "",
    mapping_rules: list[MappingRule] | None = None,
) -> GroupResult:
    """Convenience function to group SDK methods into Skills."""
    grouper = SkillGrouper(methods, mapping_rules=mapping_rules)
    skills = grouper.group(requirements)
    all_tools = {t for s in skills for t in s.allowed_tools}
    method_tools = {m.name for m in methods}
    unmatched = [t for t in method_tools if t not in all_tools]
    return GroupResult(skills=skills, unmatched_tools=unmatched)


def group_source_components(
    components: list[SourceComponent],
    strategy: GroupStrategy = GroupStrategy.COMPONENT_GROUP,
) -> GroupResult:
    """Convenience function to group source components into Skills by strategy."""
    grouper = SkillGrouper(
        methods=[],
        strategy=strategy,
        source_components=components,
    )
    skills = grouper.group()
    all_tools = {t for s in skills for t in s.allowed_tools}
    component_tools = {op.name for c in components for op in c.operations}
    unmatched = [t for t in component_tools if t not in all_tools]
    return GroupResult(skills=skills, unmatched_tools=unmatched)