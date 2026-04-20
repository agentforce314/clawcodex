from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .model import Skill

logger = logging.getLogger(__name__)

LoadedFrom = str

VALID_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_:-]{0,63}$")
VALID_CONTEXTS = frozenset({"inline", "fork"})


@dataclass
class SkillValidationError:
    field: str
    message: str


@dataclass
class BundledSkillDefinition:
    name: str
    description: str
    get_prompt_for_command: Callable[[str], str]
    aliases: list[str] = field(default_factory=list)
    when_to_use: str | None = None
    argument_hint: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    model: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    is_enabled: Callable[[], bool] | None = None
    context: str = "inline"
    agent: str | None = None
    files: dict[str, str] | None = None


_bundled_skills: list[Skill] = []


def validate_skill_definition(
    definition: BundledSkillDefinition,
) -> list[SkillValidationError]:
    errors: list[SkillValidationError] = []
    if not definition.name or not definition.name.strip():
        errors.append(SkillValidationError("name", "Skill name is required"))
    elif not VALID_NAME_RE.match(definition.name):
        errors.append(SkillValidationError(
            "name",
            f"Skill name '{definition.name}' must match pattern: "
            f"start with letter, contain only [a-zA-Z0-9_:-], max 64 chars",
        ))

    if not definition.description or not definition.description.strip():
        errors.append(SkillValidationError("description", "Skill description is required"))

    if definition.context not in VALID_CONTEXTS:
        errors.append(SkillValidationError(
            "context", f"Invalid context '{definition.context}', must be one of: {', '.join(sorted(VALID_CONTEXTS))}"
        ))

    for alias in definition.aliases:
        if not alias or not alias.strip():
            errors.append(SkillValidationError("aliases", "Alias cannot be empty"))

    return errors


def validate_skill(skill: Skill) -> list[SkillValidationError]:
    errors: list[SkillValidationError] = []
    if not skill.name or not skill.name.strip():
        errors.append(SkillValidationError("name", "Skill name is required"))
    if not skill.description or not skill.description.strip():
        errors.append(SkillValidationError("description", "Skill description is required"))
    if skill.context not in VALID_CONTEXTS:
        errors.append(SkillValidationError(
            "context", f"Invalid context '{skill.context}'"
        ))
    return errors


def skill_from_mcp_tool(
    server_name: str,
    tool_name: str,
    tool_description: str,
    *,
    input_schema: dict[str, Any] | None = None,
) -> Skill:
    skill_name = f"mcp:{server_name}:{tool_name}"

    argument_hint = None
    argument_names: list[str] = []
    if input_schema and "properties" in input_schema:
        props = input_schema["properties"]
        required = set(input_schema.get("required", []))
        hints: list[str] = []
        for prop_name in props:
            if prop_name in required:
                hints.append(f"<{prop_name}>")
            else:
                hints.append(f"[{prop_name}]")
            argument_names.append(prop_name)
        argument_hint = " ".join(hints)

    def get_prompt(args: str) -> str:
        parts = [f"Use the MCP tool '{tool_name}' from server '{server_name}'."]
        if tool_description:
            parts.append(f"Tool description: {tool_description}")
        if args:
            parts.append(f"Arguments: {args}")
        return "\n".join(parts)

    return Skill(
        name=skill_name,
        description=tool_description or f"MCP tool {tool_name} from {server_name}",
        content="",
        source=f"mcp:{server_name}",
        loaded_from="mcp",
        user_invocable=True,
        allowed_tools=[f"mcp__{server_name}__{tool_name}"],
        argument_hint=argument_hint,
        argument_names=argument_names,
        get_prompt_for_command=get_prompt,
    )


def register_bundled_skill(definition: BundledSkillDefinition) -> None:
    skill = Skill(
        name=definition.name,
        description=definition.description,
        content="",
        source="bundled",
        loaded_from="bundled",
        aliases=definition.aliases,
        allowed_tools=definition.allowed_tools,
        argument_hint=definition.argument_hint,
        when_to_use=definition.when_to_use,
        model=definition.model,
        disable_model_invocation=definition.disable_model_invocation,
        user_invocable=definition.user_invocable,
        context=definition.context,
        agent=definition.agent,
        get_prompt_for_command=definition.get_prompt_for_command,
        is_hidden=not definition.user_invocable,
    )
    _bundled_skills.append(skill)


def get_bundled_skills() -> list[Skill]:
    return list(_bundled_skills)


def get_bundled_skill_by_name(name: str) -> Skill | None:
    for skill in _bundled_skills:
        if skill.name == name:
            return skill
        if name in skill.aliases:
            return skill
    return None


def clear_bundled_skills() -> None:
    _bundled_skills.clear()
