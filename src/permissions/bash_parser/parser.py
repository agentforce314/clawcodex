from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Literal

from .ast_nodes import (
    CommandList,
    CommandListEntry,
    Pipeline,
    Redirect,
    SimpleCommand,
    Subshell,
)

REDIRECT_OPS = frozenset({">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"})

OPERATORS = ("&&", "||", ";;", ";", "&", "|&", "|")

_OPERATOR_RE = re.compile(
    r"(&&|\|\||;;|;|&|\|&|\|)"
)


@dataclass
class ParseResult:
    kind: Literal["simple", "too-complex"]
    commands: list[SimpleCommand] = field(default_factory=list)
    reason: str = ""


def parse_command(command: str) -> ParseResult:
    command = command.strip()
    if not command:
        return ParseResult(kind="simple", commands=[])

    command = _join_continuations(command)

    try:
        tokens = _tokenize(command)
    except ValueError:
        return ParseResult(kind="too-complex", reason="tokenization failed")

    if not tokens:
        return ParseResult(kind="simple", commands=[])

    try:
        groups = _split_by_operators(tokens)
    except ValueError as e:
        return ParseResult(kind="too-complex", reason=str(e))

    commands: list[SimpleCommand] = []
    for group in groups:
        cmd = _parse_simple_command(group)
        if cmd is None:
            return ParseResult(kind="too-complex", reason="complex command structure")
        commands.append(cmd)

    return ParseResult(kind="simple", commands=commands)


def _join_continuations(command: str) -> str:
    def _replacer(m: re.Match) -> str:
        backslash_count = len(m.group(0)) - 1
        if backslash_count % 2 == 1:
            return "\\" * (backslash_count - 1)
        return m.group(0)

    return re.sub(r"\\+\n", _replacer, command)


def _tokenize(command: str) -> list[str]:
    result: list[str] = []
    i = 0
    n = len(command)
    current = ""

    while i < n:
        c = command[i]

        if c == "\\" and i + 1 < n:
            current += command[i + 1]
            i += 2
            continue

        if c == "'":
            end = command.find("'", i + 1)
            if end == -1:
                raise ValueError("unterminated single quote")
            current += command[i + 1:end]
            i = end + 1
            continue

        if c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n and command[j + 1] in ('"', "\\", "$", "`"):
                    current += command[j + 1]
                    j += 2
                else:
                    current += command[j]
                    j += 1
            if j >= n:
                raise ValueError("unterminated double quote")
            i = j + 1
            continue

        if c == "$" and i + 1 < n and command[i + 1] == "(":
            depth = 1
            j = i + 2
            sub = ""
            while j < n and depth > 0:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                sub += command[j]
                j += 1
            current += "__CMDSUB__"
            i = j + 1
            continue

        if c == "`":
            j = i + 1
            while j < n and command[j] != "`":
                j += 1
            current += "__CMDSUB__"
            i = j + 1
            continue

        for op in OPERATORS:
            if command[i:i + len(op)] == op:
                if current:
                    result.append(current)
                    current = ""
                result.append(op)
                i += len(op)
                break
        else:
            if c == "(":
                if current:
                    result.append(current)
                    current = ""
                result.append("(")
                i += 1
            elif c == ")":
                if current:
                    result.append(current)
                    current = ""
                result.append(")")
                i += 1
            elif c in (" ", "\t"):
                if current:
                    result.append(current)
                    current = ""
                i += 1
            elif c == "\n":
                if current:
                    result.append(current)
                    current = ""
                result.append(";")
                i += 1
            elif c in (">", "<"):
                if current:
                    result.append(current)
                    current = ""
                if i + 1 < n:
                    two = command[i:i + 2]
                    if two in (">>", "<<", ">&", "<&", ">|"):
                        result.append(two)
                        i += 2
                        continue
                    three = command[i:i + 3]
                    if three in ("&>>", "&>", "<<<"):
                        result.append(three)
                        i += 3
                        continue
                result.append(c)
                i += 1
            elif c == "#":
                break
            else:
                current += c
                i += 1

    if current:
        result.append(current)

    return result


def _split_by_operators(tokens: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []

    for tok in tokens:
        if tok in ("&&", "||", ";", "&", "|", "|&"):
            if current:
                groups.append(current)
            current = []
        elif tok in ("(", ")"):
            if current:
                groups.append(current)
            current = []
        else:
            current.append(tok)

    if current:
        groups.append(current)

    return groups


def _parse_simple_command(tokens: list[str]) -> SimpleCommand | None:
    if not tokens:
        return SimpleCommand()

    argv: list[str] = []
    env_vars: dict[str, str] = {}
    redirects: list[Redirect] = []
    i = 0
    parsing_env = True

    while i < len(tokens):
        tok = tokens[i]

        if tok in REDIRECT_OPS or (len(tok) > 1 and tok[-1] in (">", "<") and tok[:-1].isdigit()):
            if tok[-1] in (">", "<") and tok[:-1].isdigit():
                fd = int(tok[:-1])
                op = tok[-1]
            else:
                fd = None
                op = tok
            if i + 1 < len(tokens):
                target = tokens[i + 1]
                redirects.append(Redirect(op=op, target=target, fd=fd))  # type: ignore[arg-type]
                i += 2
            else:
                i += 1
            continue

        if parsing_env and "=" in tok and not tok.startswith("="):
            eq_pos = tok.index("=")
            name = tok[:eq_pos]
            if name.isidentifier():
                env_vars[name] = tok[eq_pos + 1:]
                i += 1
                continue

        parsing_env = False
        argv.append(tok)
        i += 1

    return SimpleCommand(
        argv=argv,
        env_vars=env_vars,
        redirects=redirects,
        text=" ".join(tokens),
    )


def extract_all_commands(command: str) -> list[SimpleCommand]:
    result = parse_command(command)
    if result.kind == "simple":
        return result.commands
    return [SimpleCommand(argv=[command], text=command)]
