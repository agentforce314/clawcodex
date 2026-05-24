"""AgentBuilder — builds an AgentDefinition from grouped Skills.

Fills the Agent definition template using Skill specs and metadata.
The resulting AgentDefinition can be registered and persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agent.agent_definitions import AgentDefinition, AgentSource
from src.skills.model import Skill
from .skill_grouper import SkillSpec, MappingRule
from .templates import AGENT_TEMPLATE, SKILL_TEMPLATE


@dataclass
class AgentBuildResult:
    """Result of building an Agent from POS conversion."""
    agent: AgentDefinition
    skill_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AgentBuilder:
    """Build an AgentDefinition from SkillSpecs and metadata.

    Takes grouped SkillSpecs plus agent metadata (name, description, model,
    tools, memory_scope) and fills the Agent definition template.
    """

    def __init__(
        self,
        skills: list[SkillSpec],
        *,
        agent_name: str,
        agent_description: str,
        model: str | None = None,
        tools: list[str] | None = None,
        memory_scope: list[str] | None = None,
        persistent: bool = True,
        mapping_rules: list[MappingRule] | None = None,
    ) -> None:
        self._skills = skills
        self._agent_name = agent_name
        self._agent_description = agent_description
        self._model = model
        self._tools = tools
        self._memory_scope = memory_scope or []
        self._persistent = persistent
        self._mapping_rules = mapping_rules
        self._result: AgentBuildResult | None = None

    def build(self) -> AgentBuildResult:
        """Build the AgentDefinition and optionally persist Skill files."""
        if self._result is not None:
            return self._result

        skill_names = [s.name for s in self._skills]
        allowed_tools = self._tools or self._collect_tools()
        allowed_tools.sort()

        agent = AgentDefinition(
            agent_type=self._agent_name,
            when_to_use=self._agent_description,
            tools=allowed_tools,
            skills=skill_names,
            source="dynamic",
            base_dir="dynamic",
            model=self._model,
        )

        skill_files = []
        warnings = []
        for spec in self._skills:
            try:
                path = _write_skill_file(spec, mapping_rules=self._mapping_rules)
                skill_files.append(path)
            except Exception as exc:
                warnings.append(f"Failed to write skill file for {spec.name}: {exc}")

        self._result = AgentBuildResult(
            agent=agent,
            skill_files=skill_files,
            warnings=warnings,
        )
        return self._result

    def _collect_tools(self) -> list[str]:
        """Collect all tools from grouped skills."""
        tools: dict[str, bool] = {}
        for spec in self._skills:
            for tool in spec.allowed_tools:
                tools[tool] = True
        return list(tools.keys())


def _write_skill_file(spec: SkillSpec, *, mapping_rules: list[MappingRule] | None = None) -> Path:
    """Write a SKILL.md file from a SkillSpec."""
    rules = mapping_rules or []
    rule = next((r for r in rules if r.skill_name == spec.name), None)

    skill_dir = Path.home() / ".clawcodex" / "skills" / spec.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    frontmatter_lines = [
        "---",
        f"name: {spec.name}",
        f"description: {spec.description}",
        "user-invocable: true",
    ]
    if spec.when_to_use:
        frontmatter_lines.append(f"when_to_use: {spec.when_to_use}")
    if spec.argument_names:
        frontmatter_lines.append(f"arguments:")
        for arg in spec.argument_names:
            frontmatter_lines.append(f"  - {arg}")
    if spec.allowed_tools:
        frontmatter_lines.append(f"allowed-tools:")
        for tool in spec.allowed_tools:
            frontmatter_lines.append(f"  - {tool}")
    if rule and rule.description:
        frontmatter_lines.append(f"when_to_use: {rule.description}")
    frontmatter_lines.append("---")

    content = "\n".join(frontmatter_lines) + "\n\n" + SKILL_TEMPLATE.format(
        skill_name=spec.name,
        description=spec.description,
        tools=", ".join(spec.allowed_tools),
    )
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def write_agent_markdown(agent: AgentDefinition, path: Path) -> None:
    """Write an AgentDefinition as a markdown file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {agent.agent_type}",
        f"description: {agent.when_to_use}",
    ]
    if agent.model:
        lines.append(f"model: {agent.model}")
    if agent.tools:
        tool_list = ", ".join(agent.tools)
        lines.append(f"tools: [{tool_list}]")
    if agent.skills:
        skill_list = ", ".join(agent.skills)
        lines.append(f"skills: [{skill_list}]")
    if agent.memory:
        lines.append(f"memory: {agent.memory}")
    lines.append("---")
    lines.append("")
    lines.append(agent.when_to_use or "")

    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass
class AgentPersistenceSpec:
    """JSON-serializable agent spec for persistence (3.9.12 design)."""
    name: str
    description: str
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_scope: list[str] = field(default_factory=list)
    persistent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "model": self.model,
            "tools": self.tools,
            "skills": self.skills,
            "memory_scope": self.memory_scope,
            "persistent": self.persistent,
        }

    @classmethod
    def from_agent(cls, agent: AgentDefinition) -> AgentPersistenceSpec:
        return cls(
            name=agent.agent_type,
            description=agent.when_to_use,
            model=agent.model,
            tools=agent.tools or [],
            skills=agent.skills or [],
            memory_scope=[agent.memory] if agent.memory else [],
        )

    def save(self, agents_dir: Path | None = None) -> Path:
        agents_dir = agents_dir or (Path.home() / ".clawcodex" / "agents")
        agents_dir.mkdir(parents=True, exist_ok=True)
        path = agents_dir / f"{self.name}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, name: str, agents_dir: Path | None = None) -> AgentPersistenceSpec | None:
        agents_dir = agents_dir or (Path.home() / ".clawcodex" / "agents")
        path = agents_dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                name=data["name"],
                description=data.get("description", ""),
                model=data.get("model"),
                tools=data.get("tools", []),
                skills=data.get("skills", []),
                memory_scope=data.get("memory_scope", []),
                persistent=data.get("persistent", True),
            )
        except (json.JSONDecodeError, KeyError):
            return None


def persist_converted_agent(
    agent: AgentDefinition,
    skills: list[SkillSpec],
    agents_dir: Path | None = None,
) -> AgentPersistenceSpec:
    """Persist a converted Agent to disk for long-term use (3.9.12)."""
    spec = AgentPersistenceSpec.from_agent(agent)
    path = spec.save(agents_dir=agents_dir)

    for skill_spec in skills:
        try:
            _write_skill_file(skill_spec)
        except Exception:
            pass

    return spec