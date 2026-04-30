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

# Lazy-init guard — populated on first read so callers don't need to
# explicitly invoke ``init_bundled_skills`` at runtime startup. Tests
# that wipe state via ``clear_bundled_skills`` also reset this flag,
# leaving them in full control.
_LAZY_INITIALIZED: bool = False


def _lazy_init() -> None:
    """Run ``init_bundled_skills`` once on first registry consumer.

    Imported lazily to break the import cycle (``bundled.*`` modules
    import from this file). The first call seeds the registry; later
    calls are no-ops until ``clear_bundled_skills`` resets the flag.
    """
    global _LAZY_INITIALIZED
    if _LAZY_INITIALIZED:
        return
    # Set BEFORE the import to avoid recursion if a register_*_skill
    # callable somehow calls back into ``get_bundled_skills`` mid-init.
    _LAZY_INITIALIZED = True
    try:
        from .bundled import init_bundled_skills
        init_bundled_skills()
    except Exception:
        # Fail open: if a bundled-skill module is malformed at import
        # time, surface the failure as "no bundled skills" rather than
        # crashing every SkillTool.call.
        logger.exception("failed to initialize bundled skills")


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
    """Append a bundled-skill definition to the in-process registry.

    Suppresses the lazy-init seeding for the rest of this fixture
    cycle: if a caller (test or app) is explicitly registering, they
    own the catalogue contents. ``clear_bundled_skills`` re-arms the
    lazy-init flag so the next caller-driven cycle starts fresh.
    """
    global _LAZY_INITIALIZED
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
    _LAZY_INITIALIZED = True


def get_bundled_skills() -> list[Skill]:
    _lazy_init()
    return list(_bundled_skills)


def get_bundled_skill_by_name(name: str) -> Skill | None:
    _lazy_init()
    for skill in _bundled_skills:
        if skill.name == name:
            return skill
        if name in skill.aliases:
            return skill
    return None


def clear_bundled_skills() -> None:
    """Wipe the registry and reset the lazy-init flag.

    Resetting the flag ensures the next ``get_bundled_skills`` /
    ``register_bundled_skill`` cycle re-seeds the catalogue, which is
    what test fixtures want — otherwise a clear-then-read would return
    an empty list and silently mask "did init_bundled_skills get
    called?" bugs.
    """
    global _LAZY_INITIALIZED
    _bundled_skills.clear()
    _LAZY_INITIALIZED = False
    try:
        from .bundled import reset_bundled_skills_init_flag
        reset_bundled_skills_init_flag()
    except Exception:
        # If the bundled package can't import, the lazy-init flag in
        # this module is enough; the bundled-side flag is just a
        # belt-and-braces idempotency check.
        pass
