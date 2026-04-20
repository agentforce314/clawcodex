from __future__ import annotations

import json
from typing import Any

STRING_ARGUMENT_TOOL_FIELDS: dict[str, str] = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


def _is_blank_string(value: str) -> bool:
    return value.strip() == ""


def _is_likely_structured_object_literal(value: str) -> bool:
    import re
    return bool(re.match(r"^\s*\{\s*['\"]?\w+['\"]?\s*:", value))


def _get_plain_string_tool_argument_field(tool_name: str) -> str | None:
    return STRING_ARGUMENT_TOOL_FIELDS.get(tool_name)


def has_tool_field_mapping(tool_name: str) -> bool:
    return tool_name in STRING_ARGUMENT_TOOL_FIELDS


def _wrap_plain_string_tool_arguments(
    tool_name: str,
    value: str,
) -> dict[str, str] | None:
    field = _get_plain_string_tool_argument_field(tool_name)
    if not field:
        return None
    return {field: value}


def normalize_tool_arguments(
    tool_name: str,
    raw_arguments: str | None,
) -> Any:
    if raw_arguments is None:
        return {}

    try:
        parsed = json.loads(raw_arguments)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str) and not _is_blank_string(parsed):
            wrapped = _wrap_plain_string_tool_arguments(tool_name, parsed)
            return wrapped if wrapped is not None else parsed
        return parsed
    except (json.JSONDecodeError, ValueError):
        if _is_blank_string(raw_arguments) or _is_likely_structured_object_literal(raw_arguments):
            return {}
        wrapped = _wrap_plain_string_tool_arguments(tool_name, raw_arguments)
        return wrapped if wrapped is not None else {}
