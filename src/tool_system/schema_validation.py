from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .errors import ToolInputError


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _as_set(values: Iterable[str] | None) -> set[str]:
    return set(values or [])


def validate_json_schema(value: Any, schema: Mapping[str, Any], *, root_name: str = "input") -> None:
    issues: list[ValidationIssue] = []
    _validate(value, schema, path=root_name, issues=issues)
    if issues:
        rendered = "; ".join(f"{i.path}: {i.message}" for i in issues[:5])
        if len(issues) > 5:
            rendered += f"; (+{len(issues) - 5} more)"
        raise ToolInputError(rendered)


def _validate(value: Any, schema: Mapping[str, Any], *, path: str, issues: list[ValidationIssue]) -> None:
    if "oneOf" in schema:
        options = schema.get("oneOf") or []
        if any(_is_valid(value, opt) for opt in options):
            return
        issues.append(ValidationIssue(path, "does not match any allowed schema (oneOf)"))
        return

    if "anyOf" in schema:
        options = schema.get("anyOf") or []
        if any(_is_valid(value, opt) for opt in options):
            return
        issues.append(ValidationIssue(path, "does not match any allowed schema (anyOf)"))
        return

    expected_type = schema.get("type")
    if expected_type:
        if expected_type == "object":
            if not isinstance(value, dict):
                issues.append(ValidationIssue(path, f"expected object, got {_type_name(value)}"))
                return
            _validate_object(value, schema, path=path, issues=issues)
            return
        if expected_type == "array":
            if not isinstance(value, list):
                issues.append(ValidationIssue(path, f"expected array, got {_type_name(value)}"))
                return
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    _validate(item, item_schema, path=f"{path}[{idx}]", issues=issues)
            return
        if expected_type == "string":
            if not isinstance(value, str):
                issues.append(ValidationIssue(path, f"expected string, got {_type_name(value)}"))
                return
        elif expected_type == "boolean":
            if not isinstance(value, bool):
                issues.append(ValidationIssue(path, f"expected boolean, got {_type_name(value)}"))
                return
        elif expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                issues.append(ValidationIssue(path, f"expected number, got {_type_name(value)}"))
                return
        elif expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                issues.append(ValidationIssue(path, f"expected integer, got {_type_name(value)}"))
                return

    if "enum" in schema:
        allowed = schema.get("enum") or []
        if value not in allowed:
            issues.append(ValidationIssue(path, f"expected one of {allowed!r}, got {value!r}"))
            return


def _validate_object(value: dict[str, Any], schema: Mapping[str, Any], *, path: str, issues: list[ValidationIssue]) -> None:
    required = _as_set(schema.get("required"))
    properties = schema.get("properties") or {}
    additional = schema.get("additionalProperties", True)

    for req in required:
        if req not in value:
            issues.append(ValidationIssue(path, f"missing required field {req!r}"))

    for key, val in value.items():
        prop_schema = properties.get(key) if isinstance(properties, dict) else None
        if prop_schema is None:
            if additional is False:
                issues.append(ValidationIssue(f"{path}.{key}", "unexpected field"))
            continue
        if isinstance(prop_schema, dict):
            _validate(val, prop_schema, path=f"{path}.{key}", issues=issues)


def _is_valid(value: Any, schema: Mapping[str, Any]) -> bool:
    issues: list[ValidationIssue] = []
    _validate(value, schema, path="$", issues=issues)
    return not issues

