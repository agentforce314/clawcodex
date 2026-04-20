"""
Command registry for Claw Codex.

Manages registration and lookup of commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import Command, CommandBase, get_command_name


@dataclass
class CommandRegistry:
    """Registry for commands."""

    _commands: dict[str, Command] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    def register(self, command: Command) -> None:
        """
        Register a command.

        Args:
            command: The command to register
        """
        name = command.name.lower()
        self._commands[name] = command

        # Register aliases
        for alias in command.aliases:
            alias_lower = alias.lower()
            if alias_lower not in self._commands and alias_lower not in self._aliases:
                self._aliases[alias_lower] = name

    def unregister(self, name: str) -> None:
        """
        Unregister a command.

        Args:
            name: Name of the command to unregister
        """
        name_lower = name.lower()
        if name_lower in self._commands:
            del self._commands[name_lower]

        # Remove aliases pointing to this command
        aliases_to_remove = [
            alias for alias, target in self._aliases.items()
            if target == name_lower
        ]
        for alias in aliases_to_remove:
            del self._aliases[alias]

    def get(self, name: str) -> Optional[Command]:
        """
        Get a command by name or alias.

        Args:
            name: Name or alias of the command

        Returns:
            The command, or None if not found
        """
        name_lower = name.lower()

        # Check direct name
        if name_lower in self._commands:
            return self._commands[name_lower]

        # Check aliases
        if name_lower in self._aliases:
            return self._commands.get(self._aliases[name_lower])

        return None

    def has(self, name: str) -> bool:
        """
        Check if a command exists.

        Args:
            name: Name or alias to check

        Returns:
            True if the command exists
        """
        return self.get(name) is not None

    def list_commands(
        self,
        include_hidden: bool = False,
        include_disabled: bool = False,
    ) -> list[Command]:
        """
        List all registered commands.

        Args:
            include_hidden: Include hidden commands
            include_disabled: Include disabled commands

        Returns:
            List of commands
        """
        commands = list(self._commands.values())

        if not include_hidden:
            commands = [cmd for cmd in commands if not cmd.is_hidden]

        if not include_disabled:
            commands = [cmd for cmd in commands if cmd.is_enabled()]

        return sorted(commands, key=lambda c: c.name.lower())

    def find_commands(self, query: str, limit: int = 20) -> list[Command]:
        """
        Find commands matching a query.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of matching commands
        """
        query_lower = query.lower()
        matches: list[tuple[int, Command]] = []

        for command in self._commands.values():
            score = 0

            # Exact name match
            if query_lower == command.name.lower():
                score = 1000
            # Name starts with query
            elif command.name.lower().startswith(query_lower):
                score = 100
            # Query in name
            elif query_lower in command.name.lower():
                score = 50
            # Query in description
            elif query_lower in command.description.lower():
                score = 25
            # Query in aliases
            elif any(query_lower in alias.lower() for alias in command.aliases):
                score = 30

            if score > 0:
                matches.append((-score, command.name, command))  # Negative for ascending sort, name for tiebreaker

        # Sort by score (highest first), then name
        matches.sort()
        return [cmd for _, _, cmd in matches[:limit]]

    def clear(self) -> None:
        """Clear all registered commands."""
        self._commands.clear()
        self._aliases.clear()


# Global registry instance
_REGISTRY = CommandRegistry()


def get_command_registry() -> CommandRegistry:
    """Get the global command registry."""
    return _REGISTRY


def register_command(command: Command) -> None:
    """Register a command in the global registry."""
    _REGISTRY.register(command)


def get_command(name: str) -> Optional[Command]:
    """Get a command from the global registry."""
    return _REGISTRY.get(name)


def has_command(name: str) -> bool:
    """Check if a command exists in the global registry."""
    return _REGISTRY.has(name)


def list_commands(
    include_hidden: bool = False,
    include_disabled: bool = False,
) -> list[Command]:
    """List commands from the global registry."""
    return _REGISTRY.list_commands(include_hidden, include_disabled)


def find_commands(query: str, limit: int = 20) -> list[Command]:
    """Find commands in the global registry."""
    return _REGISTRY.find_commands(query, limit)
