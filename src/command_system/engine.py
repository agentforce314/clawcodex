"""
Command execution engine for Claw Codex.

Handles execution of commands and integration with the REPL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .argument_substitution import substitute_arguments
from .registry import CommandRegistry, get_command_registry
from .types import (
    Command,
    CommandContext,
    CommandType,
    LocalCommand,
    LocalCommandResult,
    PromptCommand,
)


@dataclass
class CommandResult:
    """Result of a command execution."""

    success: bool
    command_name: str
    result_type: str = "text"  # "text" | "prompt" | "skip"
    text: str = ""
    prompt_content: list[dict[str, Any]] = field(default_factory=list)
    should_query: bool = False
    display: str = "system"  # "skip" | "system" | "user"
    meta_messages: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @classmethod
    def success_text(cls, command_name: str, text: str) -> "CommandResult":
        """Create a successful text result."""
        return cls(
            success=True,
            command_name=command_name,
            result_type="text",
            text=text,
            display="system",
        )

    @classmethod
    def success_prompt(
        cls,
        command_name: str,
        prompt_content: list[dict[str, Any]],
        should_query: bool = True,
    ) -> "CommandResult":
        """Create a successful prompt result."""
        return cls(
            success=True,
            command_name=command_name,
            result_type="prompt",
            prompt_content=prompt_content,
            should_query=should_query,
            display="user",
        )

    @classmethod
    def error(cls, command_name: str, error: str) -> "CommandResult":
        """Create an error result."""
        return cls(
            success=False,
            command_name=command_name,
            result_type="text",
            text=f"Error: {error}",
            error=error,
            display="system",
        )

    @classmethod
    def skip(cls, command_name: str) -> "CommandResult":
        """Create a skip result."""
        return cls(
            success=True,
            command_name=command_name,
            result_type="skip",
            display="skip",
        )


@dataclass
class CommandEngine:
    """Command execution engine."""

    registry: CommandRegistry
    workspace_root: Path
    context: CommandContext
    _command_hooks: list[Callable[[str, CommandResult], None]] = field(
        default_factory=list
    )

    async def execute(
        self,
        command_input: str,
    ) -> CommandResult:
        """
        Execute a command.

        Args:
            command_input: Command input (e.g., "/help args")

        Returns:
            CommandResult with the execution result
        """
        # Parse command and args
        if not command_input.startswith("/"):
            return CommandResult.error(
                "",
                "Commands must start with '/'",
            )

        parts = command_input[1:].split(maxsplit=1)
        command_name = parts[0].strip()
        args = parts[1].strip() if len(parts) > 1 else ""

        # Get command
        command = self.registry.get(command_name)
        if command is None:
            return CommandResult.error(
                command_name,
                f"Unknown command: {command_name}",
            )

        # Check if command is enabled
        if not command.is_enabled():
            return CommandResult.error(
                command_name,
                f"Command {command_name} is disabled",
            )

        # Execute based on type
        result: CommandResult
        if command.command_type == CommandType.LOCAL:
            result = await self._execute_local(command, args)
        elif command.command_type == CommandType.PROMPT:
            result = await self._execute_prompt(command, args)
        else:
            result = CommandResult.error(
                command_name,
                f"Unknown command type: {command.command_type}",
            )

        # Run hooks
        for hook in self._command_hooks:
            try:
                hook(command_name, result)
            except Exception:
                # Don't let hook failures break command execution
                pass

        return result

    async def _execute_local(
        self,
        command: LocalCommand,
        args: str,
    ) -> CommandResult:
        """Execute a local command."""
        try:
            local_result = await command.call(args, self.context)

            if local_result.type == "skip":
                return CommandResult.skip(command.name)

            display_text = local_result.display_text or local_result.value
            return CommandResult.success_text(
                command.name,
                display_text,
            )
        except Exception as e:
            return CommandResult.error(
                command.name,
                str(e),
            )

    async def _execute_prompt(
        self,
        command: PromptCommand,
        args: str,
    ) -> CommandResult:
        """Execute a prompt command."""
        try:
            prompt_content = await command.get_prompt_for_command(args, self.context)
            return CommandResult.success_prompt(
                command.name,
                prompt_content,
                should_query=True,
            )
        except Exception as e:
            return CommandResult.error(
                command.name,
                str(e),
            )

    def add_command_hook(
        self,
        hook: Callable[[str, CommandResult], None],
    ) -> None:
        """Add a command execution hook."""
        self._command_hooks.append(hook)

    def remove_command_hook(
        self,
        hook: Callable[[str, CommandResult], None],
    ) -> None:
        """Remove a command execution hook."""
        if hook in self._command_hooks:
            self._command_hooks.remove(hook)


def create_command_context(
    workspace_root: str | Path,
    conversation: Any = None,
    cost_tracker: Any = None,
    history: Any = None,
    cwd: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> CommandContext:
    """
    Create a command context.

    Args:
        workspace_root: Root directory of the workspace
        conversation: Conversation object
        cost_tracker: Cost tracker object
        history: History log object
        cwd: Current working directory (defaults to workspace_root)
        config: Optional configuration dict

    Returns:
        CommandContext instance
    """
    root = Path(workspace_root).expanduser().resolve()
    current = Path(cwd).expanduser().resolve() if cwd is not None else root

    return CommandContext(
        workspace_root=root,
        cwd=current,
        conversation=conversation,
        cost_tracker=cost_tracker,
        history=history,
        config=config or {},
    )
