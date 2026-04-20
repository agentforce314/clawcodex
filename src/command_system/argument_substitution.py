"""
Argument substitution for commands and skills.

Handles replacing $arg, $1, $ARGUMENTS placeholders in command/skill content.
"""

from __future__ import annotations

import re
import shlex
from typing import Sequence


def substitute_arguments(
    content: str,
    args: str,
    arg_names: Sequence[str] | None = None,
) -> str:
    """
    Substitute argument placeholders in content.

    Supports:
    - $name, ${name} - Named arguments
    - $0, $1, ${0}, ${1} - Positional arguments
    - $ARGUMENTS, ${ARGUMENTS} - All arguments as a string
    - $@ - All arguments as a list (for shell-like expansion)

    Args:
        content: The content to substitute into
        args: The argument string
        arg_names: Optional list of argument names

    Returns:
        Content with placeholders replaced
    """
    if not content:
        return ""

    # Parse arguments
    try:
        parsed_args = shlex.split(args) if args else []
    except ValueError:
        # Fallback if shlex fails
        parsed_args = args.split() if args else []

    result = content

    # Replace $ARGUMENTS first (special case)
    if "$ARGUMENTS" in result or "${ARGUMENTS}" in result:
        result = result.replace("$ARGUMENTS", args)
        result = result.replace("${ARGUMENTS}", args)

    # Replace $@
    if "$@" in result:
        result = result.replace("$@", args)

    # Replace positional arguments: $0, $1, etc.
    for i, arg in enumerate(parsed_args):
        result = result.replace(f"${i}", arg)
        result = result.replace(f"${{{i}}}", arg)

    # Replace named arguments
    if arg_names:
        for i, name in enumerate(arg_names):
            if i < len(parsed_args):
                value = parsed_args[i]
                result = result.replace(f"${name}", value)
                result = result.replace(f"${{{name}}}", value)

    # Replace remaining placeholders with empty string
    # This handles $name placeholders that weren't matched
    def replace_remaining(match: re.Match) -> str:
        return ""

    # Match $name or ${name} patterns that haven't been replaced
    # but be careful not to match escaped $
    result = re.sub(r'(?<!\\)\$[a-zA-Z_][a-zA-Z0-9_]*', replace_remaining, result)
    result = re.sub(r'(?<!\\)\$\{[a-zA-Z_][a-zA-Z0-9_]*\}', replace_remaining, result)

    # Unescape any escaped $ characters
    result = result.replace(r'\$', '$')

    return result


def parse_argument_names(arguments_spec: str | list[str] | None) -> list[str]:
    """
    Parse argument names from various formats.

    Args:
        arguments_spec: Can be:
            - None → empty list
            - A string like "name, age" or "[name, age]"
            - A list of strings

    Returns:
        List of argument names
    """
    if arguments_spec is None:
        return []

    if isinstance(arguments_spec, list):
        return [str(arg).strip() for arg in arguments_spec if str(arg).strip()]

    if isinstance(arguments_spec, str):
        spec = arguments_spec.strip()
        if not spec:
            return []

        # Handle [name, age] format
        if spec.startswith('[') and spec.endswith(']'):
            spec = spec[1:-1].strip()

        if not spec:
            return []

        # Split by commas
        return [name.strip() for name in spec.split(',') if name.strip()]

    return []
