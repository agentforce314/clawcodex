"""
Skills system integration with command system.

Bridges the existing skills system to the command system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from src.skills.argument_substitution import (
    substitute_arguments as skills_substitute_args,
)
from src.skills.frontmatter import parse_frontmatter
from src.skills.loader import (
    PromptSkill,
    get_all_skills,
    get_registered_skill,
    load_skills_from_dir,
)
from src.skills.model import Skill as BaseSkill
from .argument_substitution import substitute_arguments
from .registry import CommandRegistry, register_command
from .types import Command, CommandType, PromptCommand, SkillPromptCommand


def skill_to_prompt_command(skill: PromptSkill) -> PromptCommand:
    """
    Convert a PromptSkill to a ``SkillPromptCommand``.

    Returns a ``SkillPromptCommand`` (a ``PromptCommand`` subclass) so that when
    this command is executed via the registry, its prompt is rendered through the
    same ``_run_markdown_skill`` path the Skill tool uses — preserving the
    base-dir header, ``${CLAUDE_SKILL_DIR}`` / ``${CLAUDE_SESSION_ID}``
    substitution, and gated shell-exec. The plain base ``PromptCommand`` renderer
    would drop all of that (P0-6 Option B / Phase 3.5).

    Args:
        skill: The PromptSkill to convert

    Returns:
        SkillPromptCommand instance (typed as its ``PromptCommand`` base)
    """
    return SkillPromptCommand(
        name=skill.name,
        description=skill.description,
        progress_message=f"Executing {skill.name}...",
        content_length=skill.content_length,
        arg_names=list(skill.arg_names),
        allowed_tools=list(skill.allowed_tools),
        model=skill.model,
        source=skill.loaded_from,
        skill_root=skill.skill_root,
        context=skill.context or "inline",
        agent=skill.agent,
        effort=skill.effort,
        paths=list(skill.paths) if skill.paths else [],
        markdown_content=skill.markdown_content,
        when_to_use=skill.when_to_use,
        version=skill.version,
        disable_model_invocation=skill.disable_model_invocation,
        user_invocable=skill.user_invocable,
        loaded_from=skill.loaded_from,
        is_hidden=skill.is_hidden,
        has_user_specified_description=skill.has_user_specified_description,
    )


def register_skill_as_command(skill: PromptSkill) -> PromptCommand:
    """
    Register a PromptSkill as a PromptCommand.

    Args:
        skill: The PromptSkill to register

    Returns:
        The registered PromptCommand
    """
    command = skill_to_prompt_command(skill)
    register_command(command)
    return command


def load_and_register_skills(
    project_root: str | Path | None = None,
    user_skills_dir: str | Path | None = None,
    registry: CommandRegistry | None = None,
) -> list[PromptCommand]:
    """
    Load all skills and register them as commands.

    Args:
        project_root: Optional project root directory
        user_skills_dir: Optional user skills directory
        registry: Optional command registry (uses global if None)

    Returns:
        List of registered PromptCommands
    """
    skills = get_all_skills(
        project_root=project_root,
        user_skills_dir=user_skills_dir,
    )

    registered_commands: list[PromptCommand] = []
    for skill in skills:
        command = skill_to_prompt_command(skill)
        if registry:
            registry.register(command)
        else:
            register_command(command)
        registered_commands.append(command)

    return registered_commands


def get_skill_command(name: str) -> Optional[PromptCommand]:
    """
    Get a skill-based command by name.

    Args:
        name: Name of the skill/command

    Returns:
        PromptCommand if found, None otherwise
    """
    skill = get_registered_skill(name)
    if skill:
        return skill_to_prompt_command(skill)
    return None


def get_skill_tool_commands(cwd: str | None = None) -> tuple[PromptCommand, ...]:
    """ALL prompt-based commands the model may invoke.

    Source for the model-facing "# Available Skills" system-prompt listing,
    wired into ``build_full_system_prompt_blocks(skills=...)`` at
    ``src/query/engine.py``. Mirrors b24b8cb's ``getSkillToolCommands(cwd)``
    behaviour (typescript/src/commands.ts:587-605) without porting the
    full ``aggregator`` module — the fork has no aggregator yet, so we
    walk the global command registry and keep every PromptCommand whose
    ``loaded_from`` is one of the skill-loaded buckets. CWD is accepted
    for signature parity with b24b8cb but currently unused.
    """
    del cwd  # signature parity; no per-cwd filtering yet
    try:
        from .registry import get_command_registry, list_commands
    except ImportError:
        return ()
    # ``list_commands`` may not exist in every test config; fall back to
    # the registry's own iteration if needed.
    reg = get_command_registry()
    if hasattr(reg, "list_commands"):
        all_cmds = list(reg.list_commands())
    else:
        all_cmds = list_commands()
    skill_buckets = {"skills", "bundled", "commands_DEPRECATED"}
    return tuple(
        cmd
        for cmd in all_cmds
        if isinstance(cmd, PromptCommand)
        and getattr(cmd, "loaded_from", "builtin") in skill_buckets
    )


def load_skill_from_directory(
    directory: str | Path,
    loaded_from: str = "skills",
) -> list[PromptCommand]:
    """
    Load skills from a directory and convert to commands.

    Args:
        directory: Directory to load skills from
        loaded_from: Source label for the skills

    Returns:
        List of PromptCommands
    """
    skills = load_skills_from_dir(directory, loaded_from=loaded_from)
    return [skill_to_prompt_command(skill) for skill in skills]


async def execute_skill_command(
    command: PromptCommand,
    args: str,
    context: Any,
) -> list[dict[str, Any]]:
    """
    Execute a skill-based prompt command.

    Args:
        command: The PromptCommand to execute
        args: Arguments string
        context: Command context

    Returns:
        Prompt content blocks
    """
    content = substitute_arguments(
        command.markdown_content,
        args,
        command.arg_names,
    )
    return [{"type": "text", "text": content}]
