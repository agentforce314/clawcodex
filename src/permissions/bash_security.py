from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

from .bash_parser.ast_nodes import SimpleCommand
from .bash_parser.commands import (
    CommandSafety,
    classify_command,
    is_read_only_command,
)
from .bash_parser.parser import ParseResult, extract_all_commands, parse_command
from .types import (
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
    PermissionResult,
    SafetyCheckDecisionReason,
)

CROSS_PLATFORM_CODE_EXEC: tuple[str, ...] = (
    "python", "python3", "python2",
    "node", "deno", "tsx",
    "ruby", "perl", "php", "lua",
    "npx", "bunx",
    "npm run", "yarn run", "pnpm run", "bun run",
    "bash", "sh", "ssh",
)

DANGEROUS_BASH_PATTERNS: tuple[str, ...] = (
    *CROSS_PLATFORM_CODE_EXEC,
    "zsh", "fish",
    "eval", "exec", "env", "xargs",
    "sudo",
)


def is_dangerous_bash_permission(tool_name: str, rule_content: str | None) -> bool:
    if tool_name != "Bash":
        return False

    if rule_content is None or rule_content.strip() == "":
        return True

    content = rule_content.strip()

    if content == "*":
        return True

    content_lower = content.lower()

    for pattern in DANGEROUS_BASH_PATTERNS:
        pattern_lower = pattern.lower()
        if content_lower == pattern_lower:
            return True
        if content_lower == f"{pattern_lower}:*":
            return True
        if content_lower == f"{pattern_lower}*":
            return True
        if content_lower == f"{pattern_lower} *":
            return True
        if content_lower.startswith(f"{pattern_lower}:"):
            return True
        if content_lower.startswith(f"{pattern_lower} "):
            return True
        if content_lower.startswith(f"{pattern_lower} -") and content_lower.endswith("*"):
            return True

    return False


BashSafetyLevel = Literal["safe", "read_only", "write", "destructive", "dangerous", "unknown"]


@dataclass(frozen=True)
class BashAnalysisResult:
    safety: BashSafetyLevel
    commands: list[SimpleCommand]
    paths: list[str]
    is_complex: bool = False
    reason: str = ""


def analyze_bash_command(command: str) -> BashAnalysisResult:
    parse_result = parse_command(command)

    if parse_result.kind == "too-complex":
        return BashAnalysisResult(
            safety="unknown",
            commands=[],
            paths=[],
            is_complex=True,
            reason=parse_result.reason,
        )

    commands = parse_result.commands
    if not commands:
        return BashAnalysisResult(safety="safe", commands=[], paths=[])

    overall_safety: BashSafetyLevel = "safe"
    all_paths: list[str] = []

    for cmd in commands:
        safety = classify_command(cmd.argv)
        level = safety.value
        overall_safety = _max_safety(overall_safety, level)

        paths = _extract_paths(cmd)
        all_paths.extend(paths)

    return BashAnalysisResult(
        safety=overall_safety,
        commands=commands,
        paths=all_paths,
    )


_SAFETY_ORDER: dict[str, int] = {
    "safe": 0,
    "read_only": 1,
    "write": 2,
    "destructive": 3,
    "dangerous": 4,
    "unknown": 5,
}


def _max_safety(a: BashSafetyLevel, b: BashSafetyLevel) -> BashSafetyLevel:
    if _SAFETY_ORDER.get(a, 0) >= _SAFETY_ORDER.get(b, 0):
        return a
    return b


def _extract_paths(cmd: SimpleCommand) -> list[str]:
    paths: list[str] = []
    if not cmd.argv:
        return paths

    name = cmd.argv[0].rsplit("/", 1)[-1]

    for redirect in cmd.redirects:
        if redirect.target and not redirect.target.startswith("&"):
            paths.append(redirect.target)

    for arg in cmd.argv[1:]:
        if arg.startswith("-"):
            if name in ("sed",) and arg in ("-i", "--in-place"):
                continue
            if "=" in arg:
                val = arg.split("=", 1)[1]
                if val and (val.startswith("/") or val.startswith("./") or val.startswith("~/")):
                    paths.append(val)
            continue

        if arg and (
            arg.startswith("/")
            or arg.startswith("./")
            or arg.startswith("../")
            or arg.startswith("~/")
            or "." in arg
            or "/" in arg
        ):
            paths.append(arg)

    return paths


def check_bash_command_safety(
    command: str,
    cwd: str | None = None,
    allowed_directories: list[str] | None = None,
) -> PermissionResult | None:
    analysis = analyze_bash_command(command)

    if analysis.is_complex:
        return PermissionAskDecision(
            behavior="ask",
            message=f"Complex bash command requires confirmation: {analysis.reason}",
            decision_reason=SafetyCheckDecisionReason(
                reason=f"Complex command: {analysis.reason}",
                classifier_approvable=True,
            ),
        )

    if analysis.safety == "dangerous":
        cmd_names = [c.argv[0] for c in analysis.commands if c.argv]
        return PermissionAskDecision(
            behavior="ask",
            message=f"Dangerous command ({', '.join(cmd_names)}) requires confirmation.",
            decision_reason=SafetyCheckDecisionReason(
                reason=f"Dangerous command: {', '.join(cmd_names)}",
                classifier_approvable=True,
            ),
        )

    if analysis.safety == "destructive":
        cmd_names = [c.argv[0] for c in analysis.commands if c.argv]
        return PermissionAskDecision(
            behavior="ask",
            message=f"Destructive command ({', '.join(cmd_names)}) requires confirmation.",
            decision_reason=SafetyCheckDecisionReason(
                reason=f"Destructive command: {', '.join(cmd_names)}",
                classifier_approvable=True,
            ),
        )

    if analysis.safety == "unknown":
        return PermissionAskDecision(
            behavior="ask",
            message="Unknown command requires confirmation.",
            decision_reason=SafetyCheckDecisionReason(
                reason="Unknown command",
                classifier_approvable=True,
            ),
        )

    return None


def is_sed_in_place(argv: list[str]) -> bool:
    if not argv or argv[0].rsplit("/", 1)[-1] != "sed":
        return False
    return any(a in ("-i", "--in-place") or a.startswith("-i") for a in argv[1:])


def classify_sed_pattern(argv: list[str]) -> CommandSafety:
    if not is_sed_in_place(argv):
        return CommandSafety.READ_ONLY
    return CommandSafety.WRITE


def should_sandbox_command(command: str) -> bool:
    analysis = analyze_bash_command(command)
    return analysis.safety in ("destructive", "dangerous", "unknown") or analysis.is_complex


def get_bash_command_description(command: str) -> str:
    analysis = analyze_bash_command(command)
    if not analysis.commands:
        return "empty command"
    names = [c.argv[0] for c in analysis.commands if c.argv]
    return ", ".join(names) if names else "unknown command"
