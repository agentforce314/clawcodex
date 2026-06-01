"""
Skills system integration with command system.

Bridges the existing skills system to the command system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from ..skills.argument_substitution import (
    substitute_arguments as skills_substitute_args,
)
from ..skills.frontmatter import parse_frontmatter
from ..skills.loader import (
    PromptSkill,
    get_all_skills,
    get_registered_skill,
    load_skills_from_dir,
)
from ..skills.model import Skill as BaseSkill
from .argument_substitution import substitute_arguments
from .registry import CommandRegistry, register_command
from .types import Command, CommandType, PromptCommand


def skill_to_prompt_command(skill: PromptSkill) -> PromptCommand:
    """
    Convert a PromptSkill to a PromptCommand.

    Args:
        skill: The PromptSkill to convert

    Returns:
        PromptCommand instance
    """
    return PromptCommand(
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
        # R2 (Phase 3 / P0-4): propagate the loader-computed flag so the
        # model-tool views can distinguish a real author-written description
        # from an auto-derived first-line one. ``get_slash_command_tool_skills``
        # *requires* this (or ``when_to_use``) to include a skill; without it
        # the predicate could never fire for managed/MCP/plugin skills. See
        # my-docs/get-parity-by-folder/commands-phase3-model-tool-exposure-plan.md §6 R2.
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

    .. note:: **P0-6 / why this is NOT called at bootstrap (intentional).**

        The TS gap item "auto-register skills in bootstrap (call
        ``load_and_register_skills``)" does NOT translate to a literal startup
        call in Python, because Python split TS's single command list into three
        surfaces (aggregator / ``CommandRegistry`` / skills-loader). The
        aggregator (:func:`~src.command_system.aggregator.get_commands`) already
        merges skills into the unified set, so listing/filtering/the P0-4 views
        need no registration. The ONLY new behavior a literal call would add is
        making skills resolvable via ``CommandRegistry.get(name)`` — and that is
        a *regression*: it reroutes REPL ``/myskill arg`` execution onto
        :meth:`PromptCommand.get_prompt_for_command` (bare arg-substitution),
        dropping the base-dir header, ``${CLAUDE_SKILL_DIR}`` /
        ``${CLAUDE_SESSION_ID}`` substitution, the gated shell-exec pass, and the
        bundled-skill callable that ``_run_markdown_skill`` provides. So under
        Phase 3 **Option A** this function stays available (tests, future
        unification) but is deliberately left out of the REPL/TUI bootstrap.
        Unifying execution correctly (fix the renderer first, then register) is
        Phase 3.5 **Option B**. See
        my-docs/get-parity-by-folder/commands-phase3-model-tool-exposure-plan.md §3 D-6.

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
