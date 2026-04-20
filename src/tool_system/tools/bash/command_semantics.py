"""Exit code interpretation for common commands.

Many commands use exit codes to convey information other than success/failure.
For example, grep returns 1 when no matches are found, which is not an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||[;|])\s*")


@dataclass(frozen=True)
class CommandInterpretation:
    is_error: bool
    message: str | None = None


CommandSemantic = Callable[[int, str, str], CommandInterpretation]


def _default_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandInterpretation:
    return CommandInterpretation(
        is_error=exit_code != 0,
        message=f"Command failed with exit code {exit_code}" if exit_code != 0 else None,
    )


def _grep_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandInterpretation:
    return CommandInterpretation(
        is_error=exit_code >= 2,
        message="No matches found" if exit_code == 1 else None,
    )


def _find_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandInterpretation:
    return CommandInterpretation(
        is_error=exit_code >= 2,
        message="Some directories were inaccessible" if exit_code == 1 else None,
    )


def _diff_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandInterpretation:
    return CommandInterpretation(
        is_error=exit_code >= 2,
        message="Files differ" if exit_code == 1 else None,
    )


def _test_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandInterpretation:
    return CommandInterpretation(
        is_error=exit_code >= 2,
        message="Condition is false" if exit_code == 1 else None,
    )


COMMAND_SEMANTICS: dict[str, CommandSemantic] = {
    "grep": _grep_semantic,
    "rg": _grep_semantic,
    "find": _find_semantic,
    "diff": _diff_semantic,
    "test": _test_semantic,
    "[": _test_semantic,
}


def _heuristically_extract_base_command(command: str) -> str:
    """Extract the primary command name from a complex command line.

    Takes the last segment (after splitting on &&, ||, ;, |) since that
    determines the exit code.
    """
    segments = _SPLIT_RE.split(command)
    last = segments[-1].strip() if segments else command.strip()
    first_word = last.split()[0] if last.split() else ""
    return first_word


def interpret_command_result(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> CommandInterpretation:
    base = _heuristically_extract_base_command(command)
    semantic = COMMAND_SEMANTICS.get(base, _default_semantic)
    return semantic(exit_code, stdout, stderr)
