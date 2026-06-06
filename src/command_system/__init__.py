"""
Command system for Claw Codex.

A complete reimplementation of Claude Code's command system.
"""

# WI-0.1 (ch17 Phase 0): mark the moment the command-system package starts
# import work. Placed before the heavy re-exports so the checkpoint's
# delta-from-previous reflects the package's cost.
from src.utils.startup_profiler import profile_checkpoint

profile_checkpoint("command_system_imported")

from .aggregator import (
    clear_commands_cache,
    get_commands,
    get_skill_tool_commands,
    get_slash_command_tool_skills,
)
from .argument_substitution import parse_argument_names, substitute_arguments
from .builtins import (
    AUTO_FIX_COMMAND,
    CLEAR_COMMAND,
    COMPACT_COMMAND,
    CONTEXT_COMMAND,
    COST_COMMAND,
    EXIT_COMMAND,
    HELP_COMMAND,
    INIT_COMMAND,
    REVIEW_COMMAND,
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
from .moved_to_plugin import (
    MovedToPluginCommand,
    create_moved_to_plugin_command,
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
from .safe_commands import (
    BRIDGE_SAFE_COMMANDS,
    REMOTE_SAFE_COMMANDS,
    filter_commands_for_remote_mode,
    is_bridge_safe_command,
)
from .security_review import SECURITY_REVIEW_COMMAND
from .statusline import STATUSLINE_COMMAND, StatuslineCommand
from .shell_prompt import (
    execute_shell_commands_in_prompt,
    make_bash_shell_executor,
)
from .skills_integration import (
    get_skill_command,
    load_and_register_skills,
    load_skill_from_directory,
    register_skill_as_command,
    skill_to_prompt_command,
)
from .permissions_command import PERMISSIONS_COMMAND, PermissionsCommand
from .output_style_command import OUTPUT_STYLE_COMMAND, OutputStyleCommand
from .export_command import EXPORT_COMMAND, ExportCommand
from .types import (
    Command,
    CommandAvailability,
    CommandBase,
    CommandType,
    InteractiveCommand,
    InteractiveOutcome,
    InteractiveUnavailableError,
    LocalCommand,
    LocalCommandResult,
    NullUIHost,
    PromptCommand,
    SkillPromptCommand,
    UIHost,
    UIOption,
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
    "SkillPromptCommand",
    "LocalCommand",
    "LocalCommandResult",
    "InteractiveCommand",
    "InteractiveOutcome",
    "InteractiveUnavailableError",
    "UIHost",
    "UIOption",
    "NullUIHost",
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
    "AUTO_FIX_COMMAND",
    "REVIEW_COMMAND",
    "SECURITY_REVIEW_COMMAND",
    "STATUSLINE_COMMAND",
    "StatuslineCommand",
    "PERMISSIONS_COMMAND",
    "PermissionsCommand",
    "OUTPUT_STYLE_COMMAND",
    "OutputStyleCommand",
    "EXPORT_COMMAND",
    "ExportCommand",
    "get_builtin_commands",
    "register_builtin_commands",
    # Moved-to-plugin factory + shell-at-prompt-build
    "create_moved_to_plugin_command",
    "MovedToPluginCommand",
    "execute_shell_commands_in_prompt",
    "make_bash_shell_executor",
    # Aggregator
    "get_commands",
    "get_skill_tool_commands",
    "get_slash_command_tool_skills",
    "clear_commands_cache",
    # Safe commands
    "REMOTE_SAFE_COMMANDS",
    "BRIDGE_SAFE_COMMANDS",
    "is_bridge_safe_command",
    "filter_commands_for_remote_mode",
    # Skills integration
    "skill_to_prompt_command",
    "register_skill_as_command",
    "load_and_register_skills",
    "get_skill_command",
    "load_skill_from_directory",
]
