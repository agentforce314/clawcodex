from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


@dataclass(frozen=True)
class Redirect:
    op: Literal[">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"]
    target: str
    fd: int | None = None


@dataclass(frozen=True)
class SimpleCommand:
    argv: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    redirects: list[Redirect] = field(default_factory=list)
    text: str = ""

    @property
    def name(self) -> str | None:
        return self.argv[0] if self.argv else None


@dataclass(frozen=True)
class Pipeline:
    commands: list[SimpleCommand] = field(default_factory=list)


@dataclass(frozen=True)
class CommandList:
    entries: list[CommandListEntry] = field(default_factory=list)


@dataclass(frozen=True)
class CommandListEntry:
    node: ASTNode
    operator: Literal["&&", "||", ";", "&", ""] = ""


@dataclass(frozen=True)
class Subshell:
    body: CommandList = field(default_factory=CommandList)


ASTNode = Union[SimpleCommand, Pipeline, CommandList, Subshell]
