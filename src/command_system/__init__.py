"""
Command system for Claw Codex.

A complete reimplementation of Claude Code's command system.
"""

from .argument_substitution import parse_argument_names, substitute_arguments
from .builtins import (
    CLEAR_COMMAND,
    COMPACT_COMMAND,
    CONTEXT_COMMAND,
    COST_COMMAND,
    EXIT_COMMAND,
    HELP_COMMAND,
    INIT_COMMAND,
    SKILLS_COMMAND,
    execute_command_async,
    execute_command_sync,
    get_builtin_commands,
    register_builtin_commands,
)
from .engine import (
    CommandContext,
    CommandEngine,
    CommandResult,
    create_command_context,
)
from .registry import (
    CommandRegistry,
    find_commands,
    get_command,
    get_command_registry,
    has_command,
    list_commands,
    register_command,
)
from .skills_integration import (
    get_skill_command,
    load_and_register_skills,
    load_skill_from_directory,
    register_skill_as_command,
    skill_to_prompt_command,
)
from .types import (
    Command,
    CommandAvailability,
    CommandBase,
    CommandType,
    LocalCommand,
    LocalCommandResult,
    PromptCommand,
    get_command_name,
    is_command_enabled,
    meets_availability_requirement,
)

__all__ = [
    # Types
    "Command",
    "CommandType",
    "CommandAvailability",
    "CommandBase",
    "PromptCommand",
    "LocalCommand",
    "LocalCommandResult",
    "get_command_name",
    "is_command_enabled",
    "meets_availability_requirement",
    # Argument substitution
    "substitute_arguments",
    "parse_argument_names",
    # Registry
    "CommandRegistry",
    "get_command_registry",
    "register_command",
    "get_command",
    "has_command",
    "list_commands",
    "find_commands",
    # Engine
    "CommandEngine",
    "CommandContext",
    "CommandResult",
    "create_command_context",
    # Builtins
    "HELP_COMMAND",
    "CLEAR_COMMAND",
    "EXIT_COMMAND",
    "SKILLS_COMMAND",
    "COST_COMMAND",
    "CONTEXT_COMMAND",
    "COMPACT_COMMAND",
    "INIT_COMMAND",
    "get_builtin_commands",
    "register_builtin_commands",
    # Skills integration
    "skill_to_prompt_command",
    "register_skill_as_command",
    "load_and_register_skills",
    "get_skill_command",
    "load_skill_from_directory",
]
