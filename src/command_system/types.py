"""
Command type system for Claw Codex.

Implements the core command types inspired by Claude Code's command system:
- PromptCommand: Expands to text/prompt content sent to the model
- LocalCommand: Executes local code without rendering UI
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence


class CommandType(Enum):
    """Types of commands."""
    PROMPT = "prompt"
    LOCAL = "local"


class CommandAvailability(Enum):
    """Availability environments where a command is available."""
    CLAUDE_AI = "claude-ai"
    CONSOLE = "console"


@dataclass(frozen=True)
class CompactionResult:
    """Result data from a compaction operation."""
    pre_compact_count: int = 0
    post_compact_count: int = 0
    tokens_saved: int = 0
    trigger: str = "manual"
    summary_preview: Optional[str] = None


@dataclass(frozen=True)
class LocalCommandResult:
    """Result of a local command execution."""
    type: str = "text"  # "text" | "compact" | "skip"
    value: str = ""
    compaction_result: Optional[CompactionResult] = None
    display_text: Optional[str] = None


@dataclass
class CommandContext:
    """Context passed to command execution."""
    workspace_root: Path
    cwd: Path
    conversation: Any  # Will be type hinted properly later
    cost_tracker: Any
    history: Any
    config: dict[str, Any] = field(default_factory=dict)


# Protocol for local command callables
LocalCommandCall = Callable[[str, CommandContext], LocalCommandResult]


@dataclass(frozen=True)
class CommandBase:
    """Base class for all commands."""
    name: str
    description: str
    aliases: list[str] = field(default_factory=list)
    availability: list[CommandAvailability] = field(default_factory=list)
    is_enabled: Callable[[], bool] = field(default=lambda: True)
    is_hidden: bool = False
    has_user_specified_description: bool = False
    argument_hint: Optional[str] = None
    when_to_use: Optional[str] = None
    version: Optional[str] = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    loaded_from: str = "builtin"  # "builtin" | "skills" | "plugin" | "bundled" | "mcp"
    kind: Optional[str] = None  # "workflow" or None
    immediate: bool = False
    is_sensitive: bool = False

    @property
    def command_type(self) -> CommandType:
        """Get the command type. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement command_type property")

    def user_facing_name(self) -> str:
        """Get the user-facing name of the command."""
        return self.name


@dataclass(frozen=True)
class PromptCommand(CommandBase):
    """A command that expands to prompt content."""
    progress_message: str = ""
    content_length: int = 0
    arg_names: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    model: Optional[str] = None
    source: str = "builtin"
    plugin_info: Optional[dict[str, Any]] = None
    disable_non_interactive: bool = False
    hooks: dict[str, Any] = field(default_factory=dict)
    skill_root: Optional[str] = None
    context: str = "inline"  # "inline" | "fork"
    agent: Optional[str] = None
    effort: Optional[str] = None
    paths: list[str] = field(default_factory=list)
    markdown_content: str = ""

    @property
    def command_type(self) -> CommandType:
        return CommandType.PROMPT

    async def get_prompt_for_command(
        self,
        args: str,
        context: CommandContext,
    ) -> list[dict[str, Any]]:
        """Get the prompt content for this command."""
        # Default implementation - will be overridden
        from .argument_substitution import substitute_arguments
        content = substitute_arguments(self.markdown_content, args, self.arg_names)
        return [{"type": "text", "text": content}]


@dataclass(frozen=True)
class LocalCommand(CommandBase):
    """A command that executes local code."""
    supports_non_interactive: bool = False
    _call_impl: Optional[LocalCommandCall] = field(default=None, repr=False, compare=False)

    @property
    def command_type(self) -> CommandType:
        return CommandType.LOCAL

    def set_call(self, call: LocalCommandCall) -> None:
        """Set the call implementation."""
        object.__setattr__(self, '_call_impl', call)

    async def call(self, args: str, context: CommandContext) -> LocalCommandResult:
        """Execute the local command."""
        if self._call_impl is not None:
            return self._call_impl(args, context)
        return LocalCommandResult(type="text", value=f"Command {self.name} not implemented")


# Type alias for any command
Command = PromptCommand | LocalCommand


def get_command_name(cmd: CommandBase) -> str:
    """Get the user-facing name of a command."""
    return cmd.user_facing_name()


def is_command_enabled(cmd: CommandBase) -> bool:
    """Check if a command is enabled."""
    return cmd.is_enabled()


def meets_availability_requirement(
    cmd: CommandBase,
    is_claude_ai_subscriber: bool = False,
    is_console_user: bool = False,
) -> bool:
    """Check if a command meets the availability requirement."""
    if not cmd.availability:
        return True

    for availability in cmd.availability:
        if availability == CommandAvailability.CLAUDE_AI and is_claude_ai_subscriber:
            return True
        if availability == CommandAvailability.CONSOLE and is_console_user:
            return True
    return False
