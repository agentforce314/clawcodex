from __future__ import annotations

from dataclasses import dataclass

from .commands import PORTED_COMMANDS, execute_command
from .tools import PORTED_TOOLS, execute_tool


@dataclass(frozen=True)
class MirroredCommand:
    name: str
    source_hint: str

    def execute(self, prompt: str) -> str:
        return execute_command(self.name, prompt).message


@dataclass(frozen=True)
class MirroredTool:
    name: str
    source_hint: str

    def execute(self, payload: str) -> str:
        return execute_tool(self.name, payload).message


@dataclass(frozen=True)
class ExecutionRegistry:
    commands: tuple[MirroredCommand, ...]
    tools: tuple[MirroredTool, ...]

    def command(self, name: str) -> MirroredCommand | None:
        lowered = name.lower()
        for command in self.commands:
            if command.name.lower() == lowered:
                return command
        return None

    def tool(self, name: str) -> MirroredTool | None:
        lowered = name.lower()
        for tool in self.tools:
            if tool.name.lower() == lowered:
                return tool
        return None


def build_execution_registry() -> ExecutionRegistry:
    return ExecutionRegistry(
        commands=tuple(MirroredCommand(module.name, module.source_hint) for module in PORTED_COMMANDS),
        tools=tuple(MirroredTool(module.name, module.source_hint) for module in PORTED_TOOLS),
    )
