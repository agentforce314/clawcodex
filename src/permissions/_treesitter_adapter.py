"""
tree-sitter-bash adapter for ClawCodex Bash AST parsing.

This module provides a tree-sitter-bash based parser that can replace
the manual Bash AST parsing in src/permissions/bash_parser/.

Architecture:
    src/permissions/bash_parser/ (existing parse_command API)
        ↓
    src/permissions/_treesitter_adapter.py (This module - tree-sitter-bash backend)
        ↓
    tree-sitter + tree-sitter-bash (Open source dependencies)

License: MIT - Compatible with ClawCodex

Switch:
    CLAW_USE_TREESITTER=true (default) - use tree-sitter-bash
    CLAW_USE_TREESITTER=false - fallback to original manual parsing
"""

from __future__ import annotations

import logging
import os
from typing import Any

from tree_sitter import Language, Parser

from src.permissions.bash_parser.commands import CommandSafety, classify_command

logger = logging.getLogger(__name__)

# Switching mechanism: control via environment variable
_USE_TREESITTER = os.getenv("CLAW_USE_TREESITTER", "true").lower() in ("true", "1")

# tree-sitter-bash availability
try:
    import tree_sitter_bash
    _TREESITTER_AVAILABLE = True
except ImportError:
    _TREESITTER_AVAILABLE = False
    tree_sitter_bash = None

# Initialize parser lazily
_PARSER: Parser | None = None


def _get_parser() -> Parser | None:
    """Get or create the tree-sitter-bash parser."""
    global _PARSER
    if not _TREESITTER_AVAILABLE:
        return None
    if _PARSER is None:
        _PARSER = Parser(Language(tree_sitter_bash.language()))
    return _PARSER


def is_bashlex_available() -> bool:
    """Check if tree-sitter-bash is available (alias for compatibility)."""
    return _TREESITTER_AVAILABLE


class BashlexParseResult:
    """Result from tree-sitter-bash based parsing."""
    def __init__(self, kind: str, commands: list[dict[str, Any]], reason: str = ""):
        self.kind = kind
        self.commands = commands
        self.reason = reason


def parse_command_with_bashlex(command: str) -> BashlexParseResult:
    """
    Parse a bash command using tree-sitter-bash library.

    This function bridges the existing parse_command API with
    the tree-sitter-bash library for better GNU Bash AST coverage.
    """
    if not _TREESITTER_AVAILABLE:
        return _fallback_parse(command)

    command = command.strip()
    if not command:
        return BashlexParseResult(kind="simple", commands=[])

    parser = _get_parser()
    if parser is None:
        return _fallback_parse(command)

    try:
        tree = parser.parse(bytes(command, "utf-8"))
    except Exception as e:
        logger.debug("tree-sitter parse error: %s", e)
        return BashlexParseResult(kind="too-complex", commands=[], reason=str(e))

    commands = _extract_commands(tree.root_node)
    if not commands:
        return BashlexParseResult(kind="too-complex", commands=[], reason="no_commands")

    return BashlexParseResult(kind="simple", commands=commands)


def _extract_commands(node) -> list[dict[str, Any]]:
    """Extract commands from tree-sitter node recursively."""
    result = []

    # Handle program level
    if node.type in ("program", "sequence"):
        for child in node.children:
            result.extend(_extract_commands(child))
        return result

    # Handle list with operators (&&, ||, |, etc.)
    if node.type == "list":
        for child in node.children:
            if child.type in ("and", "or", "pipe", "semicolon", "background"):
                continue
            result.extend(_extract_commands(child))
        return result

    # Handle command
    if node.type == "command":
        cmd = _parse_command_node(node)
        if cmd:
            result.append(cmd)
        return result

    # Handle compound commands (subshell, etc.)
    if node.type in ("subshell", "command_substitution"):
        if hasattr(node, "children"):
            for child in node.children:
                result.extend(_extract_commands(child))
        return result

    # Recurse into children if no specific handler
    if hasattr(node, "children"):
        for child in node.children:
            result.extend(_extract_commands(child))

    return result


def _parse_command_node(node) -> dict[str, Any] | None:
    """Parse a command node to extract argv, redirects, env_vars."""
    try:
        argv = []
        redirects = []
        env_vars = {}

        for child in node.children:
            if child.type == "command_name":
                # Get the actual command name
                name = _get_node_text(child)
                if name:
                    argv.append(name)
            elif child.type == "word" or child.type == "filename":
                word = _get_node_text(child)
                if word:
                    argv.append(word)
            elif child.type == "file_redirect":
                redirect = _parse_file_redirect(child)
                if redirect:
                    redirects.append(redirect)
            elif child.type == "io_redirect":
                redirect = _parse_io_redirect(child)
                if redirect:
                    redirects.append(redirect)
            elif child.type == "environment_variable":
                env = _parse_env_var(child)
                if env:
                    env_vars[env[0]] = env[1]

        return {
            "argv": argv,
            "env_vars": env_vars,
            "redirects": redirects,
            "text": " ".join(argv),
        }
    except Exception as e:
        logger.debug("command node parsing error: %s", e)
    return None


def _get_node_text(node) -> str:
    """Get the text content of a node."""
    if hasattr(node, "text"):
        text = node.text
        if isinstance(text, bytes):
            return text.decode("utf-8")
        return text
    return ""


def _parse_file_redirect(node) -> dict | None:
    """Parse a file_redirect node."""
    try:
        op = None
        target = None
        for child in node.children:
            if child.type in (">", ">>", "<", "<<", ">&", "<&", ">|"):
                op = child.type
            elif child.type == "word" or child.type == "filename":
                target = _get_node_text(child)
        if op:
            return {"op": op, "target": target or ""}
    except Exception as e:
        logger.debug("file redirect parse error: %s", e)
    return None


def _parse_io_redirect(node) -> dict | None:
    """Parse an io_redirect node."""
    return _parse_file_redirect(node)


def _parse_env_var(node) -> tuple[str, str] | None:
    """Parse an environment_variable node."""
    try:
        name = None
        value = ""
        for child in node.children:
            if child.type == "variable_name":
                name = _get_node_text(child)
            elif child.type == "word":
                value = _get_node_text(child)
        if name:
            return (name, value)
    except Exception as e:
        logger.debug("env var parse error: %s", e)
    return None


def _fallback_parse(command: str) -> BashlexParseResult:
    """Fallback when tree-sitter-bash is not available."""
    return BashlexParseResult(kind="too-complex", commands=[], reason="tree_sitter_bash_unavailable")


def classify_command_with_bashlex(command: str) -> CommandSafety:
    """
    Classify command safety using tree-sitter-bash for parsing.

    Returns the safety level by parsing the command and classifying
    based on the extracted argv.
    """
    result = parse_command_with_bashlex(command)
    if result.kind == "simple" and result.commands:
        argv = result.commands[0].get("argv", [])
        if argv:
            return classify_command(argv)
    return CommandSafety.UNKNOWN


# Alias for backward compatibility
def get_command_safety_from_bashlex(command: str) -> CommandSafety:
    """Alias for classify_command_with_bashlex."""
    return classify_command_with_bashlex(command)