from __future__ import annotations

from dataclasses import dataclass

from .commands import get_commands
from .models import PortingModule


@dataclass(frozen=True)
class CommandGraph:
    builtins: tuple[PortingModule, ...]
    plugin_like: tuple[PortingModule, ...]
    skill_like: tuple[PortingModule, ...]

    def flattened(self) -> tuple[PortingModule, ...]:
        return self.builtins + self.plugin_like + self.skill_like

    def as_markdown(self) -> str:
        lines = [
            '# Command Graph',
            '',
            f'Builtins: {len(self.builtins)}',
            f'Plugin-like commands: {len(self.plugin_like)}',
            f'Skill-like commands: {len(self.skill_like)}',
        ]
        return '\n'.join(lines)


def build_command_graph() -> CommandGraph:
    commands = get_commands()
    builtins = tuple(module for module in commands if 'plugin' not in module.source_hint.lower() and 'skills' not in module.source_hint.lower())
    plugin_like = tuple(module for module in commands if 'plugin' in module.source_hint.lower())
    skill_like = tuple(module for module in commands if 'skills' in module.source_hint.lower())
    return CommandGraph(builtins=builtins, plugin_like=plugin_like, skill_like=skill_like)
